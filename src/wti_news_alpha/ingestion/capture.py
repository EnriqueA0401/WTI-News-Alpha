"""Captura prospectiva de la disponibilidad real de las fuentes (D12, ADR-016).

Cada ejecución (cron de GitHub Actions, ~5 min) consulta fuentes públicas sin
clave y añade una observación por cada ítem nuevo:

- ``gdelt``:
  - ``<fichero>|listed|<md5>``: aparece en ``lastupdate.txt``;
  - ``<fichero>|available``: el fichero responde 200 (puede ir después del
    anuncio: 404 durante minutos);
  - ``<fichero>|expired``: anunciado y nunca disponible;
  - ``gap|<desde>|<hasta>``: saltos de ficheros entre ejecuciones.
- ``eia_wpsr``: ``<semana>|<Last-Modified>|<DateGreaterThan>`` de ``table1.csv``
  (un re-subido crea un ítem nuevo: aguas abajo, usar el mínimo por semana).
- ``cftc_cot``: ``<report_date>|<created_at>`` de CL.

Cotas temporales de cada observación:
- ``observed_at_utc``: respuesta recibida (cota superior).
- ``request_started_utc``: inicio de la sonda.
- ``previous_check_utc``: inicio del chequeo anterior con éxito (cota
  inferior), válida solo si ``lower_bound_valid``. Para ``|available`` visto en
  la misma ejecución que su anuncio no lo es: el fichero puede existir antes
  de anunciarse.

Las observaciones son append-only en
``data_dir/observations/YYYY-MM.jsonl`` (mes de ``observed_at_utc``); el estado
va en ``data_dir/state.json``.

Fallos:
- Un fallo de una fuente (red, HTTP o formato inesperado) se registra como
  observación con ``error`` y no detiene las demás; el llamador termina con
  código ≠ 0.
- Si una ejecución muere entre escribir observaciones y estado, la siguiente
  puede repetir ítems: aguas abajo cuenta la primera aparición.
"""

import base64
import binascii
import json
import os
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from wti_news_alpha.config import CaptureConfig
from wti_news_alpha.errors import SchemaError, SourceAccessError, WNAError
from wti_news_alpha.timeutils import ensure_utc

_LASTUPDATE_RE = re.compile(
    r"^(\d+) ([0-9a-f]{32}) https?://data\.gdeltproject\.org/gdeltv2/"
    r"((\d{14})\.\w+\.(?:csv|CSV)\.zip)$"
)
_TABLE1_WEEK_RE = re.compile(r'^"STUB_1","(\d{1,2})/(\d{1,2})/(\d{2})"')
TS14 = "%Y%m%d%H%M%S"

# Errores de una sonda que se registran como observación (formato inesperado incluido).
PROBE_ERRORS: tuple[type[BaseException], ...] = (
    WNAError,
    httpx.HTTPError,
    httpx.InvalidURL,
    KeyError,
    IndexError,
    TypeError,
    ValueError,
    AttributeError,
    OverflowError,
)


@dataclass(frozen=True)
class Item:
    key: str
    details: dict[str, object]
    lower_bound_valid: bool = True


@dataclass(frozen=True)
class ProbeOutput:
    items: list[Item]
    extra: dict[str, Any] = field(default_factory=dict)


class SourceState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_check_utc: AwareDatetime | None = None
    seen: list[str] = []
    extra: dict[str, Any] = {}


class CaptureState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: dict[str, SourceState] = {}


Probe = Callable[[httpx.Client, CaptureConfig, SourceState], ProbeOutput]


# ------------------------------------------------------------------ parsers


@dataclass(frozen=True)
class GdeltFile:
    size: int
    md5: str
    name: str
    nominal: datetime


def parse_lastupdate(text: str) -> list[GdeltFile]:
    """Parsea ``lastupdate.txt``: exactamente 3 líneas ``size md5 url``."""
    out: list[GdeltFile] = []
    for line in (x for x in text.splitlines() if x.strip()):
        m = _LASTUPDATE_RE.match(line.strip())
        if m is None:
            raise SchemaError(f"línea inesperada en lastupdate.txt: {line!r}")
        nominal = datetime.strptime(m.group(4), TS14).replace(tzinfo=UTC)
        out.append(GdeltFile(int(m.group(1)), m.group(2), m.group(3), nominal))
    if len(out) != 3:
        raise SchemaError(f"lastupdate.txt con {len(out)} ficheros (se esperaban 3)")
    return out


def nominal_of(name: str) -> datetime:
    """Timestamp nominal (UTC) del nombre de un fichero GDELT."""
    return datetime.strptime(name[:14], TS14).replace(tzinfo=UTC)


def http_date_to_iso(value: str | None) -> str | None:
    """HTTP-date → ISO UTC; ``None`` si falta. Un HTTP-date siempre es GMT."""
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"fecha HTTP inválida: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)  # "-0000": GMT según RFC 9110 §5.6.7
    return ensure_utc(parsed).isoformat()


def parse_cloudfront_policy(location: str) -> tuple[datetime | None, datetime | None]:
    """Condiciones (DateGreaterThan, DateLessThan) de una URL firmada de CloudFront.

    Su significado (¿hora de publicación?) no está verificado: se guarda tal cual.
    """
    policy = parse_qs(urlparse(location).query).get("Policy")
    if not policy:
        return None, None
    b64 = policy[0].replace("-", "+").replace("_", "=").replace("~", "/")
    try:
        cond = json.loads(base64.b64decode(b64, validate=True))["Statement"][0]["Condition"]

        def epoch(key: str) -> datetime | None:
            v = cond.get(key, {}).get("AWS:EpochTime")
            return None if v is None else datetime.fromtimestamp(int(v), tz=UTC)

        return epoch("DateGreaterThan"), epoch("DateLessThan")
    except (
        binascii.Error,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        AttributeError,
        OverflowError,
    ) as exc:
        raise SchemaError("Policy de CloudFront ilegible") from exc


def parse_table1_week(text: str) -> str:
    """Semana del dato (primera columna de fecha de la cabecera) en ISO."""
    m = _TABLE1_WEEK_RE.match(text.lstrip("﻿"))
    if m is None:
        raise SchemaError(f"cabecera inesperada en table1.csv: {text[:80]!r}")
    month, day, yy = (int(g) for g in m.groups())
    try:
        return date(2000 + yy, month, day).isoformat()
    except ValueError as exc:
        raise SchemaError(f"fecha inválida en table1.csv: {m.group(0)!r}") from exc


def _iso(ts: datetime | None) -> str | None:
    return None if ts is None else ts.isoformat()


# ------------------------------------------------------------------ sondas


def _get(client: httpx.Client, url: str, **kw: Any) -> httpx.Response:
    try:
        resp = client.get(url, **kw)
    except httpx.HTTPError as exc:
        raise SourceAccessError(f"{url}: {type(exc).__name__}") from exc
    if resp.status_code >= 400:
        raise SourceAccessError(f"{url}: HTTP {resp.status_code}")
    return resp


def _head_status(client: httpx.Client, url: str) -> httpx.Response:
    try:
        head = client.head(url)
    except httpx.HTTPError as exc:
        raise SourceAccessError(f"{url}: HEAD {type(exc).__name__}") from exc
    if head.status_code not in (200, 404):
        raise SourceAccessError(f"{url}: HEAD HTTP {head.status_code}")
    return head


def probe_gdelt(client: httpx.Client, cfg: CaptureConfig, st: SourceState) -> ProbeOutput:
    """Anuncios, disponibilidad real, ficheros que nunca llegan y saltos de GDELT.

    ``extra`` guarda ``pending`` (anunciados con 404, se re-comprueban en cada
    ejecución aunque ya no estén anunciados) y ``last_nominal``.
    """
    pending: dict[str, str] = dict(st.extra.get("pending", {}))
    last_nominal = st.extra.get("last_nominal")
    resp = _get(client, cfg.gdelt_lastupdate_url)
    lu_lm = http_date_to_iso(resp.headers.get("last-modified"))
    files = parse_lastupdate(resp.text)
    newest = max(f.nominal for f in files)
    items: list[Item] = []

    if last_nominal is not None:
        prev = datetime.fromisoformat(last_nominal)
        slot = timedelta(minutes=cfg.gdelt_slot_minutes)
        if newest - prev > slot:
            missing = int((newest - prev) / slot) - 1
            details = {"from_nominal": prev.isoformat(), "to_nominal": newest.isoformat()}
            items.append(
                Item(
                    f"gap|{prev:%Y%m%d%H%M%S}|{newest:%Y%m%d%H%M%S}",
                    {**details, "missing_slots": missing},
                )
            )

    for f in files:
        head = _head_status(client, cfg.gdelt_file_prefix + f.name)
        items.append(
            Item(
                f"{f.name}|listed|{f.md5}",
                {
                    "size": f.size,
                    "md5": f.md5,
                    "lastupdate_last_modified_utc": lu_lm,
                    "head_status_when_listed": head.status_code,
                },
            )
        )
        if head.status_code == 200:
            was_pending = pending.pop(f.name, None) is not None
            lm = http_date_to_iso(head.headers.get("last-modified"))
            items.append(Item(f"{f.name}|available", {"last_modified_utc": lm}, was_pending))
        else:
            pending.setdefault(f.name, f.nominal.isoformat())

    listed = {f.name for f in files}
    expiry = timedelta(hours=cfg.gdelt_pending_expiry_hours)
    for name in sorted(set(pending) - listed):
        head = _head_status(client, cfg.gdelt_file_prefix + name)
        if head.status_code == 200:
            del pending[name]
            lm = http_date_to_iso(head.headers.get("last-modified"))
            items.append(Item(f"{name}|available", {"last_modified_utc": lm}))
        elif newest - nominal_of(name) > expiry:
            del pending[name]
            items.append(Item(f"{name}|expired", {"hours_waited": expiry.total_seconds() / 3600}))
    return ProbeOutput(items, {"pending": pending, "last_nominal": newest.isoformat()})


def probe_eia_wpsr(client: httpx.Client, cfg: CaptureConfig, st: SourceState) -> ProbeOutput:
    first = _get(client, cfg.eia_table1_url, follow_redirects=False)
    location = first.headers.get("location")
    if not first.is_redirect or location is None:
        raise SourceAccessError(f"{cfg.eia_table1_url}: se esperaba una redirección firmada")
    target = str(first.url.join(location))
    gt, lt = parse_cloudfront_policy(target)
    resp = _get(client, target)
    lm = http_date_to_iso(resp.headers.get("last-modified"))
    week = parse_table1_week(resp.text)
    details: dict[str, object] = {
        "week_ending": week,
        "last_modified_utc": lm,
        "cf_policy_date_greater_than_utc": _iso(gt),
        "cf_policy_date_less_than_utc": _iso(lt),
    }
    return ProbeOutput([Item(f"{week}|{lm}|{_iso(gt)}", details)])


def probe_cftc_cot(client: httpx.Client, cfg: CaptureConfig, st: SourceState) -> ProbeOutput:
    params = {
        "$select": "report_date_as_yyyy_mm_dd,:created_at",
        "cftc_contract_market_code": cfg.cftc_contract_code,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "1",
    }
    rows = _get(client, cfg.cftc_url, params=params).json()
    if not isinstance(rows, list) or len(rows) != 1:
        raise SchemaError(f"respuesta inesperada de la API COT: {str(rows)[:120]}")
    report = str(rows[0]["report_date_as_yyyy_mm_dd"])[:10]
    created = ensure_utc(datetime.fromisoformat(str(rows[0][":created_at"]))).isoformat()
    return ProbeOutput(
        [Item(f"{report}|{created}", {"report_date": report, "created_at_utc": created})]
    )


PROBES: Mapping[str, Probe] = {
    "gdelt": probe_gdelt,
    "eia_wpsr": probe_eia_wpsr,
    "cftc_cot": probe_cftc_cot,
}


# ------------------------------------------------------------------ ejecución


@dataclass
class CaptureResult:
    n_new: int = 0
    errors: list[str] = field(default_factory=list)


def load_state(path: Path) -> CaptureState:
    if not path.exists():
        return CaptureState()
    try:
        return CaptureState.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as exc:
        raise SchemaError(f"state.json ilegible: {path}") from exc


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def run_capture(  # noqa: PLR0913 - dependencias inyectadas explícitamente
    data_dir: Path,
    client: httpx.Client,
    cfg: CaptureConfig,
    *,
    now: Callable[[], datetime],
    commit: str,
    run_id: str,
    probes: Mapping[str, Probe] = PROBES,
) -> CaptureResult:
    """Ejecuta todas las sondas y añade las observaciones nuevas (append-only)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = data_dir / "state.json"
    state = load_state(state_path)
    result = CaptureResult()
    by_month: dict[str, list[str]] = defaultdict(list)

    def emit(obs: Mapping[str, object], observed: datetime) -> None:
        by_month[observed.strftime("%Y-%m")].append(
            json.dumps(obs, sort_keys=True, ensure_ascii=False)
        )

    for source, probe in probes.items():
        started = ensure_utc(now())
        st = state.sources.setdefault(source, SourceState())
        base = {
            "source": source,
            "request_started_utc": started.isoformat(),
            "previous_check_utc": _iso(st.last_check_utc),
            "code_commit": commit,
            "run_id": run_id,
        }
        try:
            out = probe(client, cfg, st)
        except PROBE_ERRORS as exc:
            observed = ensure_utc(now())
            msg = f"{type(exc).__name__}: {exc}"
            result.errors.append(f"{source}: {msg}")
            emit(
                {**base, "observed_at_utc": observed.isoformat(), "item": None, "error": msg},
                observed,
            )
            continue
        observed = ensure_utc(now())
        seen = list(st.seen)
        for item in out.items:
            if item.key in seen:
                continue
            seen.append(item.key)
            result.n_new += 1
            obs = {
                **base,
                "observed_at_utc": observed.isoformat(),
                "item": item.key,
                "lower_bound_valid": item.lower_bound_valid and st.last_check_utc is not None,
                "error": None,
                "details": item.details,
            }
            emit(obs, observed)
        st.seen = seen[-cfg.max_seen :]
        st.last_check_utc = started
        st.extra = out.extra

    obs_dir = data_dir / "observations"
    obs_dir.mkdir(exist_ok=True)
    for month, lines in sorted(by_month.items()):
        with (obs_dir / f"{month}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    _write_atomic(state_path, state.model_dump_json(indent=1) + "\n")
    return result

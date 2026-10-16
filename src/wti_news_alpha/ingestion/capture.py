"""Captura prospectiva de la disponibilidad real de las fuentes (D12).

Cada ejecución (cron de GitHub Actions, ~5 min) consulta fuentes públicas sin
clave y añade una observación cuando aparece un ítem nuevo:

- ``gdelt``: cuándo aparece cada fichero en ``lastupdate.txt`` y cuándo responde
  de verdad (``Last-Modified``); pueden diferir (anunciado pero 404).
- ``eia_wpsr``: semana de ``table1.csv`` del WPSR, su ``Last-Modified`` y la
  ventana de publicación de la política CloudFront de la redirección.
- ``cftc_cot``: última ``report_date`` de CL (067651) y su ``:created_at``.

La disponibilidad real cae en ``(previous_check_utc, observed_at_utc]``: es
una cota por intervalo, no un instante exacto (el cron se retrasa). Las
observaciones son append-only en ``data_dir/observations/YYYY-MM.jsonl``; el
estado (ítems vistos y último chequeo) va en ``data_dir/state.json``. Un fallo
de una fuente se registra como observación con ``error`` y no detiene las
demás; el llamador debe terminar con código ≠ 0 si hubo errores.
"""

import base64
import binascii
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from wti_news_alpha.errors import SchemaError, SourceAccessError, WNAError
from wti_news_alpha.timeutils import ensure_utc

GDELT_LASTUPDATE_URL = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
GDELT_FILE_PREFIX = "https://data.gdeltproject.org/gdeltv2/"
EIA_TABLE1_URL = "https://ir.eia.gov/wpsr/table1.csv"
CFTC_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
CFTC_CL_CODE = "067651"
USER_AGENT = "WTI-News-Alpha research capture (non-commercial)"
MAX_SEEN = 200  # ítems recordados por fuente (lastupdate solo muestra los últimos)

_LASTUPDATE_RE = re.compile(
    r"^(\d+) ([0-9a-f]{32}) https?://data\.gdeltproject\.org/gdeltv2/(\d{14}\.\w+\.(?:csv|CSV)\.zip)$"
)
_TABLE1_WEEK_RE = re.compile(r'^"STUB_1","(\d{1,2})/(\d{1,2})/(\d{2})"')

Item = tuple[str, dict[str, object]]
Probe = Callable[[httpx.Client], list[Item]]


# ------------------------------------------------------------------ parsers


@dataclass(frozen=True)
class GdeltFile:
    size: int
    md5: str
    name: str


def parse_lastupdate(text: str) -> list[GdeltFile]:
    """Parsea ``lastupdate.txt``: exactamente 3 líneas ``size md5 url``."""
    lines = [x for x in text.splitlines() if x.strip()]
    out: list[GdeltFile] = []
    for line in lines:
        m = _LASTUPDATE_RE.match(line.strip())
        if m is None:
            raise SchemaError(f"línea inesperada en lastupdate.txt: {line!r}")
        out.append(GdeltFile(int(m.group(1)), m.group(2), m.group(3)))
    if len(out) != 3:
        raise SchemaError(f"lastupdate.txt con {len(out)} ficheros (se esperaban 3)")
    return out


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
    """Ventana (DateGreaterThan, DateLessThan) de una URL firmada de CloudFront."""
    policy = parse_qs(urlparse(location).query).get("Policy")
    if not policy:
        return None, None
    b64 = policy[0].replace("-", "+").replace("_", "=").replace("~", "/")
    try:
        stmt = json.loads(base64.b64decode(b64, validate=True))
        cond = stmt["Statement"][0]["Condition"]
    except (binascii.Error, ValueError, KeyError, IndexError, TypeError) as exc:
        raise SchemaError("Policy de CloudFront ilegible") from exc

    def epoch(key: str) -> datetime | None:
        v = cond.get(key, {}).get("AWS:EpochTime")
        return None if v is None else datetime.fromtimestamp(int(v), tz=UTC)

    return epoch("DateGreaterThan"), epoch("DateLessThan")


def parse_table1_week(text: str) -> str:
    """Semana del dato (primera columna de fecha de la cabecera) en ISO."""
    m = _TABLE1_WEEK_RE.match(text.lstrip("﻿"))
    if m is None:
        raise SchemaError(f"cabecera inesperada en table1.csv: {text[:80]!r}")
    month, day, yy = (int(g) for g in m.groups())
    return date(2000 + yy, month, day).isoformat()


# ------------------------------------------------------------------ sondas


def _get(client: httpx.Client, url: str, **kw: object) -> httpx.Response:
    try:
        resp = client.get(url, **kw)  # type: ignore[arg-type]
    except httpx.HTTPError as exc:
        raise SourceAccessError(f"{url}: {type(exc).__name__}") from exc
    if resp.status_code >= 400:
        raise SourceAccessError(f"{url}: HTTP {resp.status_code}")
    return resp


def probe_gdelt(client: httpx.Client) -> list[Item]:
    """Dos eventos por fichero: ``<name>|listed`` (aparece en ``lastupdate.txt``) y
    ``<name>|available`` (el fichero responde 200). Un fichero anunciado puede dar
    404 durante minutos o no llegar nunca: el 404 no es error, se reintenta en la
    siguiente ejecución mientras siga anunciado.
    """
    resp = _get(client, GDELT_LASTUPDATE_URL)
    lu_lm = http_date_to_iso(resp.headers.get("last-modified"))
    items: list[Item] = []
    for f in parse_lastupdate(resp.text):
        try:
            head = client.head(GDELT_FILE_PREFIX + f.name)
        except httpx.HTTPError as exc:
            raise SourceAccessError(f"{f.name}: HEAD {type(exc).__name__}") from exc
        if head.status_code not in (200, 404):
            raise SourceAccessError(f"{f.name}: HEAD HTTP {head.status_code}")
        listed = {
            "size": f.size,
            "md5": f.md5,
            "lastupdate_last_modified_utc": lu_lm,
            "head_status_when_listed": head.status_code,
        }
        items.append((f"{f.name}|listed", listed))
        if head.status_code == 200:
            lm = http_date_to_iso(head.headers.get("last-modified"))
            items.append((f"{f.name}|available", {"last_modified_utc": lm}))
    return items


def probe_eia_wpsr(client: httpx.Client) -> list[Item]:
    first = _get(client, EIA_TABLE1_URL, follow_redirects=False)
    location = first.headers.get("location")
    if not first.is_redirect or location is None:
        raise SourceAccessError(f"{EIA_TABLE1_URL}: se esperaba una redirección firmada")
    not_before, not_after = parse_cloudfront_policy(location)
    resp = _get(client, location)
    lm = http_date_to_iso(resp.headers.get("last-modified"))
    week = parse_table1_week(resp.text)
    details: dict[str, object] = {
        "week_ending": week,
        "last_modified_utc": lm,
        "release_not_before_utc": None if not_before is None else not_before.isoformat(),
        "release_not_after_utc": None if not_after is None else not_after.isoformat(),
    }
    return [(f"{week}|{lm}", details)]


def probe_cftc_cot(client: httpx.Client) -> list[Item]:
    params = {
        "$select": "report_date_as_yyyy_mm_dd,:created_at",
        "cftc_contract_market_code": CFTC_CL_CODE,
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "1",
    }
    rows = _get(client, CFTC_URL, params=params).json()
    if not isinstance(rows, list) or len(rows) != 1:
        raise SchemaError(f"respuesta inesperada de la API COT: {str(rows)[:120]}")
    report = str(rows[0]["report_date_as_yyyy_mm_dd"])[:10]
    created = ensure_utc(datetime.fromisoformat(str(rows[0][":created_at"])))
    return [(report, {"created_at_utc": created.isoformat()})]


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


def _load_state(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    try:
        sources = json.loads(path.read_text(encoding="utf-8"))["sources"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SchemaError(f"state.json ilegible: {path}") from exc
    if not isinstance(sources, dict):
        raise SchemaError(f"state.json ilegible: {path}")
    return sources


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def run_capture(  # noqa: PLR0913 - dependencias inyectadas explícitamente
    data_dir: Path,
    client: httpx.Client,
    *,
    now: Callable[[], datetime],
    commit: str,
    run_id: str,
    probes: Mapping[str, Probe] = PROBES,
) -> CaptureResult:
    """Ejecuta todas las sondas y añade las observaciones nuevas (append-only)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = data_dir / "state.json"
    state = _load_state(state_path)
    result = CaptureResult()
    lines: list[str] = []
    for source, probe in probes.items():
        observed = ensure_utc(now()).isoformat()
        src_state = state.setdefault(source, {"last_check_utc": None, "seen": []})
        prev = src_state["last_check_utc"]
        seen = list(src_state["seen"])  # type: ignore[call-overload]
        base = {
            "source": source,
            "observed_at_utc": observed,
            "previous_check_utc": prev,
            "code_commit": commit,
            "run_id": run_id,
        }
        try:
            items = probe(client)
        except WNAError as exc:
            result.errors.append(f"{source}: {type(exc).__name__}: {exc}")
            lines.append(json.dumps({**base, "item": None, "error": str(exc)}, sort_keys=True))
            continue
        for item, details in items:
            if item in seen:
                continue
            seen.append(item)
            result.n_new += 1
            obs = {**base, "item": item, "error": None, "details": details}
            lines.append(json.dumps(obs, sort_keys=True, ensure_ascii=False))
        src_state["seen"] = seen[-MAX_SEEN:]
        src_state["last_check_utc"] = observed
    if lines:
        month = ensure_utc(now()).strftime("%Y-%m")
        obs_dir = data_dir / "observations"
        obs_dir.mkdir(exist_ok=True)
        with (obs_dir / f"{month}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    _write_atomic(state_path, json.dumps({"sources": state}, indent=1, sort_keys=True) + "\n")
    return result

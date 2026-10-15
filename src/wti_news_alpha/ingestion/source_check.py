"""Comprobación de acceso de red a cada fuente (F0/F1).

Lógica pura (``classify``, ``render_markdown``) separada de la I/O (``probe``).
Las credenciales se leen del entorno y nunca aparecen en resultados ni
informes; el logger de httpx (que registra la URL con la query) se limita a
WARNING para que una clave en la query no llegue a ningún log.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import httpx

from wti_news_alpha.config import SourceSpec
from wti_news_alpha.timeutils import ensure_utc

BODY_PREVIEW_BYTES = 2048
_KEY_MISSING_MARKERS = ("api_key", "api key", "apikey")
_NOISY_HTTP_LOGGERS = ("httpx", "httpcore")


class AccessStatus(StrEnum):
    OK = "ok"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    UNREACHABLE = "unreachable"
    ERROR = "error"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    url: str
    status: AccessStatus
    http_code: int | None
    key_present: bool
    detail: str


def classify(
    http_code: int | None, body_head: str, *, keyed_source: bool, key_present: bool
) -> AccessStatus:
    """Clasifica la respuesta de una sonda.

    ``http_code`` None significa que no hubo respuesta HTTP (red/TLS/timeout).
    Un 2xx solo indica que el endpoint respondió; el contenido se audita en F1.
    """
    if http_code is None:
        return AccessStatus.UNREACHABLE
    if 200 <= http_code < 300:
        return AccessStatus.OK
    if http_code == 429:
        return AccessStatus.RATE_LIMITED
    mentions_key = any(m in body_head.lower() for m in _KEY_MISSING_MARKERS)
    auth_like = (
        http_code == 401
        or (http_code in (400, 403) and mentions_key)
        or (http_code == 403 and keyed_source)
    )
    if auth_like:
        # Con clave presente, un rechazo significa clave inválida o sin permisos.
        return AccessStatus.BLOCKED if key_present else AccessStatus.AUTH_REQUIRED
    if http_code == 403:
        return AccessStatus.BLOCKED
    return AccessStatus.ERROR


def _auth_kwargs(
    source: SourceSpec, env: Mapping[str, str]
) -> tuple[dict[str, str], tuple[str, str] | None, bool]:
    """Devuelve (params, basic_auth, key_present). La coherencia la valida ``SourceSpec``."""
    key = env.get(source.auth_env, "") if source.auth_env else ""
    if not key:
        return {}, None, False
    if source.auth == "query_param" and source.auth_param:
        return {source.auth_param: key}, None, True
    return {}, (key, ""), True


def probe(source: SourceSpec, client: httpx.Client, env: Mapping[str, str]) -> ProbeResult:
    """Hace un GET a ``source.probe_url`` leyendo como máximo ``BODY_PREVIEW_BYTES``."""
    params, basic_auth, key_present = _auth_kwargs(source, env)
    keyed = source.auth != "none"
    # httpx sustituye (no fusiona) la query de la URL si se pasa ``params=``:
    # se fusiona explícitamente para conservar p. ej. ``series_id``.
    url = httpx.URL(source.probe_url).copy_merge_params(params)
    try:
        with client.stream("GET", url, auth=basic_auth) as resp:
            head = next(resp.iter_bytes(BODY_PREVIEW_BYTES), b"")
            code: int | None = resp.status_code
    except httpx.TooManyRedirects:
        return ProbeResult(
            source.name, source.probe_url, AccessStatus.ERROR, None, key_present, "TooManyRedirects"
        )
    except httpx.HTTPError as exc:
        # No es un fallo silencioso: queda registrado como UNREACHABLE con su causa.
        # Solo se guarda el tipo: el mensaje podría incluir la URL con la clave.
        return ProbeResult(
            source.name,
            source.probe_url,
            AccessStatus.UNREACHABLE,
            None,
            key_present,
            type(exc).__name__,
        )
    text = head.decode("utf-8", errors="replace")
    status = classify(code, text, keyed_source=keyed, key_present=key_present)
    return ProbeResult(source.name, source.probe_url, status, code, key_present, f"HTTP {code}")


def probe_all(
    sources: Sequence[SourceSpec], env: Mapping[str, str], timeout_s: float = 30.0
) -> list[ProbeResult]:
    for name in _NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
        return [probe(s, client, env) for s in sources]


def _cell(text: str) -> str:
    return text.replace("|", r"\|")


def render_markdown(results: Sequence[ProbeResult], checked_at: datetime) -> str:
    """Informe Markdown en español. ``checked_at`` debe ser tz-aware."""
    ts = ensure_utc(checked_at).isoformat(timespec="seconds")
    lines = [
        "# Acceso a fuentes de datos",
        "",
        f"Comprobado: {ts} (UTC). Generado por `wna sources check`.",
        "",
        "| Fuente | Estado | HTTP | Detalle | Clave en entorno | URL sondeada |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        code = "—" if r.http_code is None else str(r.http_code)
        key = "sí" if r.key_present else "no"
        lines.append(
            f"| {_cell(r.name)} | {r.status.value} | {code} | {_cell(r.detail)} | {key} "
            f"| {_cell(r.url)} |"
        )
    lines.append("")
    return "\n".join(lines)

"""Comprobación de acceso de red a cada fuente (F0/F1).

Lógica pura (``classify``, ``render_markdown``) separada de la I/O (``probe``).
Las credenciales se leen del entorno y nunca aparecen en resultados ni informes.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import httpx

from wti_news_alpha.config import SourceSpec
from wti_news_alpha.timeutils import ensure_utc

BODY_PREVIEW_BYTES = 2048
_KEY_MISSING_MARKERS = ("api_key", "api key", "apikey", "no api_key")


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


def classify(http_code: int | None, body_head: str, *, key_present: bool) -> AccessStatus:
    """Clasifica la respuesta de una sonda.

    ``http_code`` None significa que no hubo respuesta HTTP (red/TLS/timeout).
    """
    if http_code is None:
        return AccessStatus.UNREACHABLE
    if 200 <= http_code < 300:
        return AccessStatus.OK
    if http_code == 429:
        return AccessStatus.RATE_LIMITED
    mentions_key = any(m in body_head.lower() for m in _KEY_MISSING_MARKERS)
    if http_code == 401 or (http_code in (400, 403) and mentions_key):
        # Con clave presente, un 401/403 significa clave inválida o sin permisos.
        return AccessStatus.BLOCKED if key_present else AccessStatus.AUTH_REQUIRED
    if http_code == 403:
        return AccessStatus.BLOCKED
    return AccessStatus.ERROR


def _auth_kwargs(
    source: SourceSpec, env: Mapping[str, str]
) -> tuple[dict[str, str], tuple[str, str] | None, bool]:
    """Devuelve (params, basic_auth, key_present) para la sonda."""
    key = env.get(source.auth_env, "") if source.auth_env else ""
    if not key:
        return {}, None, False
    if source.auth == "query_param" and source.auth_param:
        return {source.auth_param: key}, None, True
    if source.auth == "basic_user":
        return {}, (key, ""), True
    return {}, None, False


def probe(source: SourceSpec, client: httpx.Client, env: Mapping[str, str]) -> ProbeResult:
    """Hace un GET a ``source.probe_url`` leyendo como máximo ``BODY_PREVIEW_BYTES``."""
    params, basic_auth, key_present = _auth_kwargs(source, env)
    try:
        with client.stream("GET", source.probe_url, params=params, auth=basic_auth) as resp:
            head = next(resp.iter_bytes(BODY_PREVIEW_BYTES), b"")
            code: int | None = resp.status_code
    except httpx.HTTPError as exc:
        # No es un fallo silencioso: el error queda registrado como UNREACHABLE.
        return ProbeResult(
            source.name,
            source.probe_url,
            AccessStatus.UNREACHABLE,
            None,
            key_present,
            type(exc).__name__,
        )
    text = head.decode("utf-8", errors="replace")
    status = classify(code, text, key_present=key_present)
    return ProbeResult(source.name, source.probe_url, status, code, key_present, f"HTTP {code}")


def probe_all(
    sources: Sequence[SourceSpec], env: Mapping[str, str], timeout_s: float = 30.0
) -> list[ProbeResult]:
    with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
        return [probe(s, client, env) for s in sources]


def render_markdown(results: Sequence[ProbeResult], checked_at: datetime) -> str:
    """Informe Markdown en español. ``checked_at`` debe ser tz-aware."""
    ts = ensure_utc(checked_at).isoformat(timespec="seconds")
    lines = [
        "# Acceso a fuentes de datos",
        "",
        f"Comprobado: {ts} (UTC). Generado por `wna sources check`.",
        "",
        "| Fuente | Estado | HTTP | Clave en entorno | URL sondeada |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        code = "—" if r.http_code is None else str(r.http_code)
        key = "sí" if r.key_present else "no"
        lines.append(f"| {r.name} | {r.status.value} | {code} | {key} | {r.url} |")
    lines.append("")
    return "\n".join(lines)

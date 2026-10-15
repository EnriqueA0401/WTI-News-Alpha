"""Tests de la comprobación de acceso a fuentes (sin red: httpx.MockTransport)."""

import logging
from datetime import UTC, datetime

import httpx
import pytest

from wti_news_alpha.config import SourceSpec
from wti_news_alpha.ingestion.source_check import (
    AccessStatus,
    ProbeResult,
    classify,
    probe,
    probe_all,
    render_markdown,
)


@pytest.mark.parametrize(
    ("code", "body", "keyed", "key_present", "expected"),
    [
        (200, "", False, False, AccessStatus.OK),
        (204, "", True, True, AccessStatus.OK),
        (429, "", False, False, AccessStatus.RATE_LIMITED),
        (403, '{"error":{"code":"API_KEY_MISSING"}}', True, False, AccessStatus.AUTH_REQUIRED),
        (400, "Variable api_key is not set", True, False, AccessStatus.AUTH_REQUIRED),
        (401, "", False, False, AccessStatus.AUTH_REQUIRED),
        (401, "", True, True, AccessStatus.BLOCKED),
        (403, "api_key invalid", True, True, AccessStatus.BLOCKED),
        # Fuente con clave, sin clave en entorno, 403 sin marcador: falta la clave.
        (403, "Forbidden", True, False, AccessStatus.AUTH_REQUIRED),
        # Regresión: un 400 sin mención de clave es un error de petición, no de clave.
        (400, "Variable series_id has not been set", True, True, AccessStatus.ERROR),
        (403, "Access Denied", False, False, AccessStatus.BLOCKED),
        (400, "bad request", False, False, AccessStatus.ERROR),
        (500, "", False, False, AccessStatus.ERROR),
        (None, "", False, False, AccessStatus.UNREACHABLE),
    ],
)
def test_classify(
    code: int | None, body: str, keyed: bool, key_present: bool, expected: AccessStatus
) -> None:
    assert classify(code, body, keyed_source=keyed, key_present=key_present) == expected


def _spec(**kw: str | None) -> SourceSpec:
    base: dict[str, str | None] = {
        "name": "s",
        "description": "d",
        "docs_url": "https://example.org/docs",
        "terms_url": None,
        "probe_url": "https://example.org/probe",
        "auth": "none",
    }
    base.update(kw)
    return SourceSpec.model_validate(base)


SECRET = "s3cr3t-key"  # noqa: S105 - valor sintético de test


def test_probe_query_param_key_is_sent_but_not_reported() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    spec = _spec(auth="query_param", auth_env="K", auth_param="api_key")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        res = probe(spec, client, {"K": SECRET})
    assert seen[0].url.params["api_key"] == SECRET
    assert res.status is AccessStatus.OK
    assert res.key_present
    assert SECRET not in repr(res)
    assert SECRET not in render_markdown([res], datetime(2026, 1, 1, tzinfo=UTC))


def test_probe_keeps_existing_query_params_regression() -> None:
    # Regresión: httpx descartaba series_id/file_type al añadir api_key (FRED 400).
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    spec = _spec(
        auth="query_param",
        auth_env="K",
        auth_param="api_key",
        probe_url="https://example.org/series?series_id=DCOILWTICO&file_type=json",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert probe(spec, client, {"K": SECRET}).status is AccessStatus.OK
    assert dict(seen[0].url.params) == {
        "series_id": "DCOILWTICO",
        "file_type": "json",
        "api_key": SECRET,
    }


def test_probe_basic_user_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200)

    spec = _spec(auth="basic_user", auth_env="K")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert probe(spec, client, {"K": SECRET}).status is AccessStatus.OK


def test_probe_without_key_sends_no_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        assert "api_key" not in request.url.params
        return httpx.Response(403, text='{"error":"API_KEY_MISSING"}')

    spec = _spec(auth="query_param", auth_env="K", auth_param="api_key")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        res = probe(spec, client, {})
    assert res.status is AccessStatus.AUTH_REQUIRED
    assert not res.key_present


def test_probe_key_never_reaches_httpx_logs(caplog: pytest.LogCaptureFixture) -> None:
    # Regresión (revisión adversarial): httpx registra la URL con la query a INFO.
    spec = _spec(auth="query_param", auth_env="K", auth_param="api_key")
    original = httpx.Client.__init__

    def patched(self: httpx.Client, **kw: object) -> None:
        original(self, transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    with caplog.at_level(logging.INFO), pytest.MonkeyPatch.context() as mp:
        mp.setattr(httpx.Client, "__init__", patched)
        logging.getLogger("httpx").setLevel(logging.INFO)
        probe_all([spec], {"K": SECRET})
    assert SECRET not in caplog.text


def test_probe_too_many_redirects_is_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": str(request.url)})

    with httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True, max_redirects=2
    ) as client:
        res = probe(_spec(), client, {})
    assert res.status is AccessStatus.ERROR
    assert res.detail == "TooManyRedirects"


def test_probe_network_error_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        res = probe(_spec(), client, {})
    assert res.status is AccessStatus.UNREACHABLE
    assert res.http_code is None
    assert res.detail == "ConnectError"


def test_probe_all_uses_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(429))
    original = httpx.Client.__init__

    def patched(self: httpx.Client, **kw: object) -> None:
        original(self, transport=transport)

    monkeypatch.setattr(httpx.Client, "__init__", patched)
    results = probe_all([_spec(name="a"), _spec(name="b")], {})
    assert [r.status for r in results] == [AccessStatus.RATE_LIMITED] * 2


def test_render_markdown_rows() -> None:
    rows = [
        ProbeResult("a", "https://a", AccessStatus.OK, 200, False, "HTTP 200"),
        ProbeResult("b", "https://b", AccessStatus.UNREACHABLE, None, True, "ConnectError"),
    ]
    md = render_markdown(rows, datetime(2026, 10, 15, 3, 0, tzinfo=UTC))
    assert "2026-10-15T03:00:00+00:00" in md
    assert "| a | ok | 200 | HTTP 200 | no | https://a |" in md
    assert "| b | unreachable | — | ConnectError | sí | https://b |" in md


def test_render_markdown_escapes_pipes() -> None:
    rows = [ProbeResult("a|b", "https://x", AccessStatus.OK, 200, False, "x|y")]
    md = render_markdown(rows, datetime(2026, 10, 15, tzinfo=UTC))
    assert r"| a\|b | ok | 200 | x\|y |" in md

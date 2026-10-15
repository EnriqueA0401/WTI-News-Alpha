"""Tests de la comprobación de acceso a fuentes (sin red: httpx.MockTransport)."""

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
    ("code", "body", "key_present", "expected"),
    [
        (200, "", False, AccessStatus.OK),
        (204, "", True, AccessStatus.OK),
        (429, "", False, AccessStatus.RATE_LIMITED),
        (403, '{"error":{"code":"API_KEY_MISSING"}}', False, AccessStatus.AUTH_REQUIRED),
        (400, "Variable api_key is not set", False, AccessStatus.AUTH_REQUIRED),
        (401, "", False, AccessStatus.AUTH_REQUIRED),
        (401, "", True, AccessStatus.BLOCKED),
        (403, "api_key invalid", True, AccessStatus.BLOCKED),
        (403, "Access Denied", False, AccessStatus.BLOCKED),
        (400, "bad request", False, AccessStatus.ERROR),
        (500, "", False, AccessStatus.ERROR),
        (None, "", False, AccessStatus.UNREACHABLE),
    ],
)
def test_classify(code: int | None, body: str, key_present: bool, expected: AccessStatus) -> None:
    assert classify(code, body, key_present=key_present) == expected


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


def test_probe_query_param_without_param_name_sends_nothing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert not request.url.params
        return httpx.Response(200)

    spec = _spec(auth="query_param", auth_env="K")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert not probe(spec, client, {"K": SECRET}).key_present


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
    assert "| a | ok | 200 | no | https://a |" in md
    assert "| b | unreachable | — | sí | https://b |" in md

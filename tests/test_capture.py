"""Tests de la captura prospectiva (D12, ADR-016). Respuestas HTTP SINTÉTICAS de test."""

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from wti_news_alpha.config import load_capture_config
from wti_news_alpha.errors import SchemaError, SourceAccessError
from wti_news_alpha.ingestion import capture as c

CFG = load_capture_config()
T0 = datetime(2026, 10, 16, 0, 20, 5, tzinfo=UTC)
LM = "Thu, 15 Oct 2026 15:12:40 GMT"


def lastupdate(slot: str = "20261016001500") -> str:
    return (
        f"151563 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d http://data.gdeltproject.org/gdeltv2/{slot}.export.CSV.zip\n"
        f"264851 0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d http://data.gdeltproject.org/gdeltv2/{slot}.mentions.CSV.zip\n"
        f"8612325 9a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d http://data.gdeltproject.org/gdeltv2/{slot}.gkg.csv.zip\n"
    )


TABLE1 = (
    '"STUB_1","10/9/26","10/2/26","Difference","Percent Change"\n'
    '"Crude Oil","698.560","707.117","-8.557","-1.200"\n'
)


def cf_policy(cond: object) -> str:
    stmt = {"Statement": [{"Resource": "https://ir.eia.gov/secure/wpsr/*", "Condition": cond}]}
    raw = base64.b64encode(json.dumps(stmt).encode()).decode()
    return raw.replace("+", "-").replace("=", "_").replace("/", "~")


GOOD_COND = {
    "DateLessThan": {"AWS:EpochTime": 1792588800},
    "DateGreaterThan": {"AWS:EpochTime": 1792080000},
}
LOCATION = (
    "https://ir.eia.gov/secure/wpsr/table1.csv?Policy="
    + cf_policy(GOOD_COND)
    + "&Signature=abc&Key-Pair-Id=K"
)
COT_ROWS = [
    {
        "report_date_as_yyyy_mm_dd": "2026-10-06T00:00:00.000",
        ":created_at": "2026-10-09T19:30:07.543Z",
    }
]


class Server:
    """Transporte simulado configurable por prueba."""

    def __init__(self) -> None:
        self.lastupdate = lastupdate()
        self.head: dict[str, int] = {}  # fragmento de nombre → estado HEAD
        self.over: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        url = str(request.url)
        for key, resp in self.over.items():
            if key in url:
                return resp
        if url.endswith("lastupdate.txt"):
            return httpx.Response(200, text=self.lastupdate, headers={"Last-Modified": LM})
        if "data.gdeltproject.org/gdeltv2/" in url:
            status = next((v for k, v in self.head.items() if k in url), 200)
            return httpx.Response(
                status, headers={"Last-Modified": "Fri, 16 Oct 2026 00:04:00 GMT"}
            )
        if url == CFG.eia_table1_url:
            return httpx.Response(302, headers={"Location": LOCATION})
        if url.startswith("https://ir.eia.gov/secure/wpsr/table1.csv"):
            return httpx.Response(200, text=TABLE1, headers={"Last-Modified": LM})
        if "publicreporting.cftc.gov" in url:
            return httpx.Response(200, json=COT_ROWS)
        return httpx.Response(599)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def clock(start: datetime) -> Callable[[], datetime]:
    state = {"t": start}

    def now() -> datetime:
        state["t"] += timedelta(seconds=1)
        return state["t"]

    return now


def run(tmp: Path, srv: Server, t: datetime, run_id: str = "1") -> c.CaptureResult:
    with srv.client() as cl:
        return c.run_capture(tmp, cl, CFG, now=clock(t), commit="abc", run_id=run_id)


def read_obs(data_dir: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for p in sorted((data_dir / "observations").glob("*.jsonl")):
        out += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    return out


def keys(obs: list[dict[str, object]], source: str = "gdelt") -> list[str]:
    return [str(o["item"]) for o in obs if o["source"] == source]


def st() -> c.SourceState:
    return c.SourceState()


# ------------------------------------------------------------------ parsers


def test_parse_lastupdate() -> None:
    files = c.parse_lastupdate(lastupdate())
    assert [f.name for f in files][2] == "20261016001500.gkg.csv.zip"
    assert files[2].size == 8612325
    assert files[2].nominal == datetime(2026, 10, 16, 0, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    "bad", ["", "1 2\n", "x 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d http://a/b.zip\n"]
)
def test_parse_lastupdate_rejects_malformed(bad: str) -> None:
    with pytest.raises(SchemaError):
        c.parse_lastupdate(bad)


def test_parse_cloudfront_policy() -> None:
    gt, lt = c.parse_cloudfront_policy(LOCATION)
    assert gt == datetime(2026, 10, 15, 16, 0, tzinfo=UTC)
    assert lt == datetime(2026, 10, 21, 13, 20, tzinfo=UTC)
    assert c.parse_cloudfront_policy("https://ir.eia.gov/x.csv") == (None, None)


@pytest.mark.parametrize(
    "policy",
    [
        "%%%",
        cf_policy({"DateGreaterThan": {"AWS:EpochTime": "abc"}}),
        cf_policy([1, 2]),
        cf_policy({"DateGreaterThan": {"AWS:EpochTime": 10**15}}),
    ],
)
def test_parse_cloudfront_policy_invalid_is_schema_error(policy: str) -> None:
    # Regresión (revisión adversarial): ValueError/AttributeError escapaban sin tipar.
    with pytest.raises(SchemaError, match="Policy"):
        c.parse_cloudfront_policy(f"https://ir.eia.gov/x.csv?Policy={policy}")


def test_parse_table1_week() -> None:
    assert c.parse_table1_week(TABLE1) == "2026-10-09"
    assert c.parse_table1_week("﻿" + TABLE1) == "2026-10-09"
    with pytest.raises(SchemaError, match="cabecera"):
        c.parse_table1_week('"X","nope"\n')
    with pytest.raises(SchemaError, match="fecha"):
        c.parse_table1_week('"STUB_1","13/40/26"\n')


def test_http_date_to_iso() -> None:
    assert c.http_date_to_iso(LM) == "2026-10-15T15:12:40+00:00"
    assert c.http_date_to_iso(None) is None
    assert c.http_date_to_iso("Thu, 15 Oct 2026 15:12:40 -0000") == "2026-10-15T15:12:40+00:00"
    with pytest.raises(SchemaError):
        c.http_date_to_iso("garbage")


# ------------------------------------------------------------------ sondas


def test_probe_gdelt_listed_and_available() -> None:
    with Server().client() as cl:
        out = c.probe_gdelt(cl, CFG, st())
    items = {i.key: i for i in out.items}
    listed = items["20261016001500.gkg.csv.zip|listed|9a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"]
    assert listed.details["head_status_when_listed"] == 200
    available = items["20261016001500.gkg.csv.zip|available"]
    assert available.details["last_modified_utc"] == "2026-10-16T00:04:00+00:00"
    # Visto en la misma ejecución que el anuncio: la cota inferior no vale.
    assert available.lower_bound_valid is False
    assert out.extra == {"pending": {}, "last_nominal": "2026-10-16T00:15:00+00:00"}


def test_probe_gdelt_pending_until_available_after_delisting() -> None:
    # Regresión (revisión adversarial): un 404 anunciado no se re-comprobaba al salir del anuncio.
    srv = Server()
    srv.head = {"20261016001500.gkg": 404}
    with srv.client() as cl:
        out1 = c.probe_gdelt(cl, CFG, st())
    assert "20261016001500.gkg.csv.zip" in out1.extra["pending"]
    assert "20261016001500.gkg.csv.zip|available" not in {i.key for i in out1.items}
    srv.lastupdate = lastupdate("20261016003000")
    srv.head = {"20261016003000.gkg": 404}
    with srv.client() as cl:
        out2 = c.probe_gdelt(cl, CFG, c.SourceState(extra=out1.extra))
    late = next(i for i in out2.items if i.key == "20261016001500.gkg.csv.zip|available")
    assert late.lower_bound_valid is True
    assert "20261016001500.gkg.csv.zip" not in out2.extra["pending"]
    assert "20261016003000.gkg.csv.zip" in out2.extra["pending"]


def test_probe_gdelt_listed_404_then_available_has_valid_lower_bound() -> None:
    srv = Server()
    srv.head = {"20261016001500.gkg": 404}
    with srv.client() as cl:
        out1 = c.probe_gdelt(cl, CFG, st())
    srv.head = {}
    with srv.client() as cl:
        out2 = c.probe_gdelt(cl, CFG, c.SourceState(extra=out1.extra))
    item = next(i for i in out2.items if i.key == "20261016001500.gkg.csv.zip|available")
    assert item.lower_bound_valid is True


def test_probe_gdelt_pending_expires() -> None:
    srv = Server()
    srv.lastupdate = lastupdate("20261017120000")
    srv.head = {"20261016001500.gkg": 404}
    state = c.SourceState(
        extra={"pending": {"20261016001500.gkg.csv.zip": "2026-10-16T00:15:00+00:00"}}
    )
    with srv.client() as cl:
        out = c.probe_gdelt(cl, CFG, state)
    assert "20261016001500.gkg.csv.zip|expired" in {i.key for i in out.items}
    assert out.extra["pending"] == {}


def test_probe_gdelt_pending_kept_before_expiry() -> None:
    srv = Server()
    srv.lastupdate = lastupdate("20261016010000")
    srv.head = {"20261016001500.gkg": 404}
    state = c.SourceState(
        extra={"pending": {"20261016001500.gkg.csv.zip": "2026-10-16T00:15:00+00:00"}}
    )
    with srv.client() as cl:
        out = c.probe_gdelt(cl, CFG, state)
    assert "20261016001500.gkg.csv.zip" in out.extra["pending"]


def test_probe_gdelt_records_gap() -> None:
    # Regresión (revisión adversarial): las ejecuciones retrasadas perdían lotes sin registro.
    srv = Server()
    srv.lastupdate = lastupdate("20261016010000")
    state = c.SourceState(extra={"last_nominal": "2026-10-16T00:15:00+00:00"})
    with srv.client() as cl:
        out = c.probe_gdelt(cl, CFG, state)
    gap = next(i for i in out.items if i.key.startswith("gap|"))
    assert gap.key == "gap|20261016001500|20261016010000"
    assert gap.details["missing_slots"] == 2


def test_probe_gdelt_head_errors() -> None:
    srv = Server()
    srv.head = {"20261016001500.gkg": 500}
    with srv.client() as cl, pytest.raises(SourceAccessError, match="HEAD HTTP 500"):
        c.probe_gdelt(cl, CFG, st())

    def h(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            raise httpx.ReadTimeout("slow", request=request)
        return Server()(request)

    with (
        httpx.Client(transport=httpx.MockTransport(h)) as cl,
        pytest.raises(SourceAccessError, match="ReadTimeout"),
    ):
        c.probe_gdelt(cl, CFG, st())


def test_probe_eia() -> None:
    with Server().client() as cl:
        (item,) = c.probe_eia_wpsr(cl, CFG, st()).items
    assert item.key == "2026-10-09|2026-10-15T15:12:40+00:00|2026-10-15T16:00:00+00:00"
    assert item.details["cf_policy_date_greater_than_utc"] == "2026-10-15T16:00:00+00:00"
    assert item.details["week_ending"] == "2026-10-09"


def test_probe_eia_relative_location() -> None:
    srv = Server()
    rel = LOCATION.removeprefix("https://ir.eia.gov")
    srv.over = {CFG.eia_table1_url: httpx.Response(302, headers={"Location": rel})}
    with srv.client() as cl:
        (item,) = c.probe_eia_wpsr(cl, CFG, st()).items
    assert item.details["week_ending"] == "2026-10-09"


def test_probe_eia_without_redirect_fails() -> None:
    srv = Server()
    srv.over = {CFG.eia_table1_url: httpx.Response(200, text=TABLE1)}
    with srv.client() as cl, pytest.raises(SourceAccessError, match="redirección"):
        c.probe_eia_wpsr(cl, CFG, st())


def test_probe_cftc() -> None:
    with Server().client() as cl:
        (item,) = c.probe_cftc_cot(cl, CFG, st()).items
    assert item.key == "2026-10-06|2026-10-09T19:30:07.543000+00:00"
    assert item.details["report_date"] == "2026-10-06"


def test_probe_cftc_empty_fails() -> None:
    srv = Server()
    srv.over = {"publicreporting": httpx.Response(200, json=[])}
    with srv.client() as cl, pytest.raises(SchemaError):
        c.probe_cftc_cot(cl, CFG, st())


def test_network_error_is_source_access_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(boom)) as cl,
        pytest.raises(SourceAccessError, match="ConnectError"),
    ):
        c.probe_cftc_cot(cl, CFG, st())


# ------------------------------------------------------------------ ejecución


def test_run_capture_first_then_unchanged(tmp_path: Path) -> None:
    srv = Server()
    r1 = run(tmp_path, srv, T0)
    after_first = c.load_state(tmp_path / "state.json")
    r2 = run(tmp_path, srv, T0 + timedelta(minutes=5), "2")
    assert r1.n_new == 8  # 3 GDELT x (anunciado + disponible) + 1 EIA + 1 COT
    assert (r1.errors, r2.n_new) == ([], 0)
    obs = read_obs(tmp_path)
    first = obs[0]
    assert first["previous_check_utc"] is None
    assert first["lower_bound_valid"] is False
    started = datetime.fromisoformat(str(first["request_started_utc"]))
    observed = datetime.fromisoformat(str(first["observed_at_utc"]))
    assert started < observed  # cota superior = respuesta recibida
    assert after_first.sources["gdelt"].last_check_utc == started


def test_run_capture_bounds_for_new_item(tmp_path: Path) -> None:
    srv = Server()
    run(tmp_path, srv, T0)
    prev_start = c.load_state(tmp_path / "state.json").sources["gdelt"].last_check_utc
    srv.lastupdate = lastupdate("20261016003000")
    run(tmp_path, srv, T0 + timedelta(minutes=15), "2")
    listed = next(
        o
        for o in read_obs(tmp_path)
        if str(o["item"]).startswith("20261016003000.gkg.csv.zip|listed")
    )
    assert prev_start is not None
    assert listed["previous_check_utc"] == prev_start.isoformat()
    assert listed["lower_bound_valid"] is True
    assert datetime.fromisoformat(str(listed["observed_at_utc"])) > T0 + timedelta(minutes=15)


def test_run_capture_unexpected_format_is_recorded_and_others_kept(tmp_path: Path) -> None:
    # Regresión (revisión adversarial): un KeyError en COT tiraba toda la ejecución.
    srv = Server()
    srv.over = {"publicreporting": httpx.Response(200, json=[{"report_date_as_yyyy_mm_dd": "x"}])}
    r = run(tmp_path, srv, T0)
    assert r.errors[0].startswith("cftc_cot: KeyError")
    assert r.n_new == 7
    err = next(o for o in read_obs(tmp_path) if o["source"] == "cftc_cot")
    assert err["item"] is None
    assert "KeyError" in str(err["error"])
    assert c.load_state(tmp_path / "state.json").sources["cftc_cot"].last_check_utc is None


def test_run_capture_html_body_is_recorded(tmp_path: Path) -> None:
    srv = Server()
    srv.over = {"publicreporting": httpx.Response(200, text="<html>oops</html>")}
    r = run(tmp_path, srv, T0)
    assert r.errors[0].startswith("cftc_cot: JSONDecodeError")


def test_month_file_follows_observation_time(tmp_path: Path) -> None:
    # Regresión (revisión adversarial): el mes salía de una llamada posterior a now().
    srv = Server()
    run(tmp_path, srv, datetime(2026, 10, 31, 23, 59, 40, tzinfo=UTC))
    files = sorted(p.name for p in (tmp_path / "observations").glob("*.jsonl"))
    assert files == ["2026-10.jsonl"]


def test_state_seen_is_bounded(tmp_path: Path) -> None:
    srv = Server()
    run(tmp_path, srv, T0)
    path = tmp_path / "state.json"
    state = c.load_state(path)
    state.sources["gdelt"].seen = [f"x{i}" for i in range(CFG.max_seen + 50)]
    path.write_text(state.model_dump_json(), encoding="utf-8")
    run(tmp_path, srv, T0 + timedelta(minutes=5), "2")
    assert len(c.load_state(path).sources["gdelt"].seen) == CFG.max_seen


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        '{"sources": []}',
        '{"sources": {"gdelt": {"seen": "i1"}}}',
        '{"sources": {"gdelt": {"last_check_utc": "garbage"}}}',
        '{"sources": {"gdelt": {"last_check_utc": "2026-10-16T00:00:00"}}}',
    ],
)
def test_invalid_state_fails_loudly(tmp_path: Path, text: str) -> None:
    # Regresión (revisión adversarial): estados mal formados pasaban sin validar.
    (tmp_path / "state.json").write_text(text, encoding="utf-8")
    with pytest.raises(SchemaError, match="state"):
        run(tmp_path, Server(), T0)


def test_old_state_format_is_accepted(tmp_path: Path) -> None:
    old = {"sources": {"gdelt": {"last_check_utc": "2026-10-16T00:36:07+00:00", "seen": ["a"]}}}
    (tmp_path / "state.json").write_text(json.dumps(old), encoding="utf-8")
    r = run(tmp_path, Server(), T0 + timedelta(hours=1))
    assert r.errors == []


def test_naive_clock_rejected(tmp_path: Path) -> None:
    with Server().client() as cl, pytest.raises(Exception, match="naive"):
        c.run_capture(
            tmp_path,
            cl,
            CFG,
            now=lambda: datetime(2026, 1, 1),
            commit="abc",
            run_id="1",
        )


def test_http_error_status_is_source_access_error() -> None:
    srv = Server()
    srv.over = {"lastupdate.txt": httpx.Response(503)}
    with srv.client() as cl, pytest.raises(SourceAccessError, match="503"):
        c.probe_gdelt(cl, CFG, st())

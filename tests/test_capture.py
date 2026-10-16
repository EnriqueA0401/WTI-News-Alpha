"""Tests de la captura prospectiva (D12). Respuestas HTTP SINTÉTICAS de test."""

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from wti_news_alpha.errors import SchemaError, SourceAccessError
from wti_news_alpha.ingestion import capture as c

T0 = datetime(2026, 10, 16, 0, 20, 5, tzinfo=UTC)

LASTUPDATE = (
    "151563 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d "
    "http://data.gdeltproject.org/gdeltv2/20261016001500.export.CSV.zip\n"
    "264851 0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d "
    "http://data.gdeltproject.org/gdeltv2/20261016001500.mentions.CSV.zip\n"
    "8612325 9a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d "
    "http://data.gdeltproject.org/gdeltv2/20261016001500.gkg.csv.zip\n"
)
TABLE1 = (
    '"STUB_1","10/9/26","10/2/26","Difference","Percent Change"\n'
    '"Crude Oil","698.560","707.117","-8.557","-1.200"\n'
)


def cf_policy(gt: int, lt: int) -> str:
    stmt = {
        "Statement": [
            {
                "Resource": "https://ir.eia.gov/secure/wpsr/*",
                "Condition": {
                    "DateLessThan": {"AWS:EpochTime": lt},
                    "DateGreaterThan": {"AWS:EpochTime": gt},
                },
            }
        ]
    }
    raw = base64.b64encode(json.dumps(stmt).encode()).decode()
    return raw.replace("+", "-").replace("=", "_").replace("/", "~")


LOCATION = (
    "https://ir.eia.gov/secure/wpsr/table1.csv?Policy="
    + cf_policy(1792080000, 1792588800)
    + "&Signature=abc&Key-Pair-Id=K"
)
LM = "Thu, 15 Oct 2026 15:12:40 GMT"


def handler(overrides: dict[str, httpx.Response] | None = None):  # type: ignore[no-untyped-def]
    over = overrides or {}

    def h(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        url = str(request.url)
        for key, resp in over.items():
            if key in url:
                return resp
        if url.endswith("lastupdate.txt"):
            return httpx.Response(200, text=LASTUPDATE, headers={"Last-Modified": LM})
        if "data.gdeltproject.org/gdeltv2/2026" in url:
            return httpx.Response(200, headers={"Last-Modified": "Fri, 16 Oct 2026 00:04:00 GMT"})
        if url == c.EIA_TABLE1_URL:
            return httpx.Response(302, headers={"Location": LOCATION})
        if url.startswith("https://ir.eia.gov/secure/wpsr/table1.csv"):
            return httpx.Response(200, text=TABLE1, headers={"Last-Modified": LM})
        if "publicreporting.cftc.gov" in url:
            rows = [
                {
                    "report_date_as_yyyy_mm_dd": "2026-10-06T00:00:00.000",
                    ":created_at": "2026-10-09T19:30:07.543Z",
                }
            ]
            return httpx.Response(200, json=rows)
        return httpx.Response(599)

    return h


def client(overrides: dict[str, httpx.Response] | None = None) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler(overrides)))


# ------------------------------------------------------------------ parsers


def test_parse_lastupdate() -> None:
    files = c.parse_lastupdate(LASTUPDATE)
    assert [f.name for f in files] == [
        "20261016001500.export.CSV.zip",
        "20261016001500.mentions.CSV.zip",
        "20261016001500.gkg.csv.zip",
    ]
    assert files[2].size == 8612325


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


def test_parse_cloudfront_policy_absent_or_invalid() -> None:
    assert c.parse_cloudfront_policy("https://ir.eia.gov/x.csv") == (None, None)
    with pytest.raises(SchemaError, match="Policy"):
        c.parse_cloudfront_policy("https://ir.eia.gov/x.csv?Policy=%%%")


def test_parse_table1_week() -> None:
    assert c.parse_table1_week(TABLE1) == "2026-10-09"
    with pytest.raises(SchemaError):
        c.parse_table1_week('"X","nope"\n')


def test_http_date_to_iso() -> None:
    assert c.http_date_to_iso(LM) == "2026-10-15T15:12:40+00:00"
    assert c.http_date_to_iso(None) is None
    assert c.http_date_to_iso("Thu, 15 Oct 2026 15:12:40 -0000") == "2026-10-15T15:12:40+00:00"
    with pytest.raises(SchemaError):
        c.http_date_to_iso("garbage")


# ------------------------------------------------------------------ sondas


def test_probe_gdelt() -> None:
    with client() as cl:
        items = dict(c.probe_gdelt(cl))
    assert len(items) == 6
    listed = items["20261016001500.gkg.csv.zip|listed"]
    assert listed["lastupdate_last_modified_utc"] == "2026-10-15T15:12:40+00:00"
    assert listed["md5"] == "9a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"
    assert listed["head_status_when_listed"] == 200
    available = items["20261016001500.gkg.csv.zip|available"]
    assert available["last_modified_utc"] == "2026-10-16T00:04:00+00:00"


def test_probe_gdelt_listed_but_missing_is_not_an_error() -> None:
    # Observado en vivo (2026-10-16 00:30 UTC): el GKG anunciado daba 404 varios minutos.
    with client({"20261016001500.gkg": httpx.Response(404)}) as cl:
        items = dict(c.probe_gdelt(cl))
    assert "20261016001500.gkg.csv.zip|listed" in items
    assert "20261016001500.gkg.csv.zip|available" not in items
    assert items["20261016001500.gkg.csv.zip|listed"]["head_status_when_listed"] == 404


def test_run_capture_records_late_availability(tmp_path: Path) -> None:
    with client({"20261016001500.gkg": httpx.Response(404)}) as cl:
        r1 = c.run_capture(tmp_path, cl, now=clock(T0), commit="abc", run_id="1")
    with client() as cl:
        r2 = c.run_capture(
            tmp_path, cl, now=clock(T0 + timedelta(minutes=5)), commit="abc", run_id="2"
        )
    assert (r1.n_new, r2.n_new) == (7, 1)
    late = next(
        o for o in read_obs(tmp_path) if o["item"] == "20261016001500.gkg.csv.zip|available"
    )
    assert late["run_id"] == "2"


def test_probe_eia() -> None:
    with client() as cl:
        ((item, details),) = c.probe_eia_wpsr(cl)
    assert item == "2026-10-09|2026-10-15T15:12:40+00:00"
    assert details["week_ending"] == "2026-10-09"
    assert details["release_not_before_utc"] == "2026-10-15T16:00:00+00:00"


def test_probe_cftc() -> None:
    with client() as cl:
        ((item, details),) = c.probe_cftc_cot(cl)
    assert item == "2026-10-06"
    assert details["created_at_utc"] == "2026-10-09T19:30:07.543000+00:00"


def test_probe_http_error_is_source_access_error() -> None:
    with client({"lastupdate.txt": httpx.Response(503)}) as cl, pytest.raises(SourceAccessError):
        c.probe_gdelt(cl)


def test_probe_eia_without_redirect_fails() -> None:
    with (
        client({c.EIA_TABLE1_URL: httpx.Response(200, text=TABLE1)}) as cl,
        pytest.raises(SourceAccessError, match="redirección"),
    ):
        c.probe_eia_wpsr(cl)


def test_probe_cftc_empty_fails() -> None:
    with (
        client({"publicreporting": httpx.Response(200, json=[])}) as cl,
        pytest.raises(SchemaError),
    ):
        c.probe_cftc_cot(cl)


# ------------------------------------------------------------------ ejecución


def read_obs(data_dir: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for p in sorted((data_dir / "observations").glob("*.jsonl")):
        out += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    return out


def clock(start: datetime):  # type: ignore[no-untyped-def]
    state = {"t": start}

    def now() -> datetime:
        state["t"] += timedelta(seconds=1)
        return state["t"]

    return now


def test_run_capture_first_then_unchanged(tmp_path: Path) -> None:
    with client() as cl:
        r1 = c.run_capture(tmp_path, cl, now=clock(T0), commit="abc", run_id="1")
        r2 = c.run_capture(
            tmp_path, cl, now=clock(T0 + timedelta(minutes=5)), commit="abc", run_id="2"
        )
    assert r1.n_new == 8  # 3 GDELT x (anunciado + disponible) + 1 EIA + 1 COT
    assert r1.errors == []
    assert r2.n_new == 0
    obs = read_obs(tmp_path)
    assert len(obs) == 8
    first = obs[0]
    assert first["source"] == "gdelt"
    assert first["previous_check_utc"] is None
    assert first["code_commit"] == "abc"
    assert first["run_id"] == "1"
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["sources"]["gdelt"]["last_check_utc"].startswith("2026-10-16T00:25")


def test_run_capture_new_item_records_interval(tmp_path: Path) -> None:
    with client() as cl:
        c.run_capture(tmp_path, cl, now=clock(T0), commit="abc", run_id="1")
    newer = LASTUPDATE.replace("20261016001500", "20261016003000")
    t1 = T0 + timedelta(minutes=15)
    with client({"lastupdate.txt": httpx.Response(200, text=newer)}) as cl:
        r = c.run_capture(tmp_path, cl, now=clock(t1), commit="abc", run_id="2")
    assert r.n_new == 6
    gkg = next(o for o in read_obs(tmp_path) if o["item"] == "20261016003000.gkg.csv.zip|listed")
    prev = datetime.fromisoformat(str(gkg["previous_check_utc"]))
    seen = datetime.fromisoformat(str(gkg["observed_at_utc"]))
    assert T0 < prev < seen
    assert seen > t1


def test_run_capture_records_source_error_and_continues(tmp_path: Path) -> None:
    with client({"publicreporting": httpx.Response(500)}) as cl:
        r = c.run_capture(tmp_path, cl, now=clock(T0), commit="abc", run_id="1")
    assert [e.split(":")[0] for e in r.errors] == ["cftc_cot"]
    assert r.n_new == 7
    err = next(o for o in read_obs(tmp_path) if o["source"] == "cftc_cot")
    assert err["item"] is None
    assert "500" in str(err["error"])


def test_state_seen_is_bounded(tmp_path: Path) -> None:
    with client() as cl:
        c.run_capture(tmp_path, cl, now=clock(T0), commit="abc", run_id="1")
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sources"]["gdelt"]["seen"] = [f"x{i}" for i in range(c.MAX_SEEN + 50)]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with client() as cl:
        c.run_capture(tmp_path, cl, now=clock(T0 + timedelta(minutes=5)), commit="abc", run_id="2")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["sources"]["gdelt"]["seen"]) == c.MAX_SEEN


def test_corrupt_state_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    with client() as cl, pytest.raises(SchemaError, match="state"):
        c.run_capture(tmp_path, cl, now=clock(T0), commit="abc", run_id="1")


def test_naive_clock_rejected(tmp_path: Path) -> None:
    with client() as cl, pytest.raises(Exception, match="naive"):
        c.run_capture(
            tmp_path,
            cl,
            now=lambda: datetime(2026, 1, 1),
            commit="abc",
            run_id="1",
        )


def test_state_with_wrong_shape_fails(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text('{"sources": []}', encoding="utf-8")
    with client() as cl, pytest.raises(SchemaError, match="state"):
        c.run_capture(tmp_path, cl, now=clock(T0), commit="abc", run_id="1")


def test_gdelt_head_error_fails() -> None:
    with (
        client({"20261016001500.gkg": httpx.Response(500)}) as cl,
        pytest.raises(SourceAccessError, match="HEAD"),
    ):
        c.probe_gdelt(cl)


def test_network_error_is_source_access_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(boom)) as cl,
        pytest.raises(SourceAccessError, match="ConnectError"),
    ):
        c.probe_cftc_cot(cl)


def test_gdelt_head_network_error() -> None:
    def h(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            raise httpx.ReadTimeout("slow", request=request)
        return handler()(request)

    with (
        httpx.Client(transport=httpx.MockTransport(h)) as cl,
        pytest.raises(SourceAccessError, match="ReadTimeout"),
    ):
        c.probe_gdelt(cl)

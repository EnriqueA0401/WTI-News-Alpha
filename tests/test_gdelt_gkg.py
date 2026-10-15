"""Tests de la ingesta GDELT GKG por streaming (ADR-011). Datos SINTÉTICOS de test."""

import gzip
import hashlib
import io
import json
import logging
import os
import stat
import zipfile
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest
import yaml

from wti_news_alpha.config import (
    GdeltConfig,
    GkgFilterConfig,
    load_gdelt_config,
    load_yaml_model,
)
from wti_news_alpha.errors import RawIntegrityError, SchemaError, SourceAccessError
from wti_news_alpha.ingestion import gdelt_gkg as g
from wti_news_alpha.ingestion.rawstore import RawStore

CFG: GdeltConfig = load_gdelt_config()
FLT = CFG.gkg_filter
TS = datetime(2024, 6, 10, 14, 15, tzinfo=UTC)
DAY = date(2024, 6, 10)


# ------------------------------------------------------------------ helpers


def line(
    *,
    ts: datetime = TS,
    themes: str = "",
    orgs: str = "",
    title: str | None = None,
    record_id: str = "r",
) -> str:
    f = [""] * g.N_GKG_FIELDS
    f[0] = f"{ts:%Y%m%d%H%M%S}-{zlib.crc32(record_id.encode())}"
    f[1] = ts.strftime("%Y%m%d%H%M%S")
    f[2] = "1"
    f[3] = "news.example"
    f[4] = f"https://news.example/{record_id}"
    f[7] = themes
    f[13] = orgs
    f[17] = "wc:10,c1.1:2"
    if title is not None:
        f[26] = f"<PAGE_TITLE>{title}</PAGE_TITLE>"
    return "\t".join(f)


def labels(df: pl.DataFrame) -> list[str]:
    return [u.rsplit("/", 1)[-1] for u in df["v2_document_identifier"].to_list()]


def gkg_text(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def zip_bytes(text: str | bytes, ts: datetime = TS, member: str | None = None) -> bytes:
    buf = io.BytesIO()
    name = member if member is not None else f"{ts:%Y%m%d%H%M%S}.gkg.csv"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, text if isinstance(text, bytes) else text.encode())
    return buf.getvalue()


def entry_for(content: bytes, ts: datetime = TS) -> g.IndexEntry:
    return g.IndexEntry(
        file_ts=ts,
        kind="gkg",
        size=len(content),
        md5=hashlib.md5(content).hexdigest(),  # noqa: S324 - md5 del índice GDELT
        url=f"http://data.gdeltproject.org/gdeltv2/{ts:%Y%m%d%H%M%S}.gkg.csv.zip",
    )


class Server:
    """Transporte simulado: ruta → lista de respuestas (la última se repite)."""

    def __init__(self, routes: dict[str, list[httpx.Response]]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(name)
        queue = self.routes[name]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def ok(content: bytes, lm: str | None = "Mon, 10 Jun 2024 14:05:00 GMT") -> httpx.Response:
    headers = {"Last-Modified": lm} if lm else {}
    return httpx.Response(200, content=content, headers=headers)


def no_sleep(_: float) -> None:
    return None


# ------------------------------------------------------------------ índice maestro

GOOD_INDEX = """\
150383 297a16b493de7cf6ca809a7cc31d0b93 http://data.gdeltproject.org/gdeltv2/20240610141500.export.CSV.zip
318084 bb27f78ba45f69a17ea6ed7755e9f8ff http://data.gdeltproject.org/gdeltv2/20240610141500.mentions.CSV.zip
10768507 ea8dde0beb0ba98810a92db068c0ce99 http://data.gdeltproject.org/gdeltv2/20240610141500.gkg.csv.zip
http://data.gdeltproject.org/gdeltv2/
10768507 ea8dde0beb0ba98810a92db068c0ce98 http://data.gdeltproject.org/gdeltv2/20240611000000.gkg.csv.zip
"""


def test_parse_masterfilelist_counts_malformed() -> None:
    entries, malformed = g.parse_masterfilelist(GOOD_INDEX, max_malformed=1)
    assert malformed == 1
    assert [e.kind for e in entries] == ["export", "mentions", "gkg", "gkg"]
    gkg = entries[2]
    assert gkg.file_ts == TS
    assert gkg.size == 10768507
    assert gkg.name == "20240610141500.gkg.csv.zip"


@pytest.mark.parametrize(
    "bad",
    [
        "12 nothex http://data.gdeltproject.org/gdeltv2/20240610141500.gkg.csv.zip",
        "x ea8dde0beb0ba98810a92db068c0ce99 http://data.gdeltproject.org/gdeltv2/20240610141500.gkg.csv.zip",
        "1 ea8dde0beb0ba98810a92db068c0ce99 http://data.gdeltproject.org/gdeltv2/20240610141500.foo.csv.zip",
        "1 ea8dde0beb0ba98810a92db068c0ce99 http://evil.example/gdeltv2/20240610141500.gkg.csv.zip",
    ],
)
def test_parse_masterfilelist_malformed_over_limit_fails(bad: str) -> None:
    with pytest.raises(SchemaError, match="mal formadas"):
        g.parse_masterfilelist(bad + "\n", max_malformed=0)


def test_parse_masterfilelist_off_grid_fails() -> None:
    txt = "1 ea8dde0beb0ba98810a92db068c0ce99 http://data.gdeltproject.org/gdeltv2/20240610141600.gkg.csv.zip\n"
    with pytest.raises(SchemaError, match="rejilla"):
        g.parse_masterfilelist(txt, max_malformed=0)


def test_parse_masterfilelist_duplicate_fails() -> None:
    txt = "1 ea8dde0beb0ba98810a92db068c0ce99 http://data.gdeltproject.org/gdeltv2/20240610141500.gkg.csv.zip\n"
    with pytest.raises(SchemaError, match="duplicad"):
        g.parse_masterfilelist(txt * 2, max_malformed=0)


def test_gkg_entries_by_day_half_open_and_empty_days() -> None:
    entries, _ = g.parse_masterfilelist(GOOD_INDEX, max_malformed=1)
    days = g.gkg_entries_by_day(entries, date(2024, 6, 9), date(2024, 6, 12))
    assert list(days) == [date(2024, 6, 9), date(2024, 6, 10), date(2024, 6, 11)]
    assert days[date(2024, 6, 9)] == []
    assert [e.name for e in days[DAY]] == ["20240610141500.gkg.csv.zip"]
    assert len(days[date(2024, 6, 11)]) == 1
    only = g.gkg_entries_by_day(entries, DAY, date(2024, 6, 11))
    assert list(only) == [DAY]


def test_gkg_entries_by_day_rejects_empty_range() -> None:
    with pytest.raises(SchemaError, match="rango"):
        g.gkg_entries_by_day([], DAY, DAY)


# ------------------------------------------------------------------ filtro


def test_filter_theme_tokens_and_exclusions() -> None:
    text = gkg_text(
        line(record_id="keep_env_oil", themes="ENV_OIL;TAX_X;"),
        line(record_id="keep_oilprice", themes="ECON_OILPRICE;"),
        line(record_id="keep_wb", themes="WB_2298_REFINERIES;"),
        line(record_id="drop_soil", themes="WB_1913_SOIL_MANAGEMENT;"),
        line(record_id="drop_olive", themes="TAX_FOODSTAPLES_OLIVE_OIL;"),
        line(record_id="drop_turmoil", themes="POLITICAL_TURMOIL;"),
        line(record_id="drop_empty"),
    )
    df, st = g.filter_gkg(text, TS, FLT)
    assert labels(df) == ["keep_env_oil", "keep_oilprice", "keep_wb"]
    assert df["match_theme"].to_list() == [True, True, True]
    assert st.rows_in == 7
    assert st.kept == 3
    assert st.rejected == 0


def test_filter_title_and_org() -> None:
    text = gkg_text(
        line(record_id="t", title="Crude prices jump after OPEC+ cut"),
        line(record_id="t_no", title="Spoiled milk recall"),
        line(record_id="o", orgs="organization of the petroleum exporting countries;un"),
        line(record_id="o_no", orgs="world bank"),
    )
    df, st = g.filter_gkg(text, TS, FLT)
    assert labels(df) == ["t", "o"]
    assert df["match_title"].to_list() == [True, False]
    assert df["match_org"].to_list() == [False, True]
    assert st.match_title == 1
    assert st.match_org == 1


def test_filter_keeps_raw_columns_order_and_provenance() -> None:
    text = gkg_text(line(record_id="a"), line(record_id="b", themes="ENV_OIL;"))
    df, _ = g.filter_gkg(text, TS, FLT)
    assert df.columns == [*g.GKG_COLUMNS, *g.EXTRA_COLUMNS]
    assert df.schema == g.OUTPUT_SCHEMA
    assert df["row_in_file"].to_list() == [1]
    assert df["source_file_ts"].to_list() == [TS]
    assert df["v2_gcam"].to_list() == ["wc:10,c1.1:2"]
    assert df["v1_counts"].to_list() == [""]


def test_filter_rejects_ragged_lines_and_flags_relevance() -> None:
    good = line(record_id="ok", themes="ENV_OIL;")
    broken = line(record_id="split", title="Oil\nprices")  # salto de línea dentro del registro
    text = gkg_text(good, broken, "garbage without tabs")
    df, st = g.filter_gkg(text, TS, FLT)
    assert labels(df) == ["ok"]
    assert st.rows_in == 1
    assert st.rejected == 3
    assert st.rejected_maybe_relevant == 1


def test_filter_counts_date_mismatch() -> None:
    other = datetime(2024, 6, 10, 14, 0, tzinfo=UTC)
    _, st = g.filter_gkg(gkg_text(line(ts=other), line()), TS, FLT)
    assert st.date_mismatch == 1


def test_filter_empty_file() -> None:
    df, st = g.filter_gkg("", TS, FLT)
    assert df.height == 0
    assert df.schema == g.OUTPUT_SCHEMA
    assert st.rows_in == 0


def test_filter_rejects_naive_ts() -> None:
    with pytest.raises(Exception, match=r"naive|zona"):
        g.filter_gkg("", datetime(2024, 6, 10), FLT)


def test_decode_zip_checks_single_expected_member() -> None:
    text = gkg_text(line())
    assert g.decode_gkg_zip(zip_bytes(text), "20240610141500.gkg.csv") == (text, 0)
    with pytest.raises(SchemaError, match="miembro"):
        g.decode_gkg_zip(zip_bytes(text, member="other.csv"), "20240610141500.gkg.csv")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("20240610141500.gkg.csv", "a")
        z.writestr("x", "b")
    with pytest.raises(SchemaError, match="miembro"):
        g.decode_gkg_zip(buf.getvalue(), "20240610141500.gkg.csv")
    with pytest.raises(SchemaError, match="zip"):
        g.decode_gkg_zip(b"not a zip", "20240610141500.gkg.csv")


def test_decode_zip_invalid_utf8_is_replaced_and_counted() -> None:
    raw = "x\ufffd".encode() + b"ab\xffcd\xfe\n"  # un U+FFFD legítimo y dos bytes inválidos
    text, n = g.decode_gkg_zip(zip_bytes(raw), "20240610141500.gkg.csv")
    assert n == 2
    assert text == "x\ufffdab\ufffdcd\ufffd\n"


def test_decode_zip_corrupt_deflate_is_schema_error() -> None:
    # Regresión (revisión adversarial): zlib.error escapaba como excepción no tipada.
    good = zip_bytes(gkg_text(line(), line(), line()) * 50)
    data_start = 30 + len("20240610141500.gkg.csv")
    bad = bytearray(good)
    for i in range(data_start + 5, data_start + 40):
        bad[i] ^= 0xFF
    with pytest.raises(SchemaError, match="zip"):
        g.decode_gkg_zip(bytes(bad), "20240610141500.gkg.csv")


# ------------------------------------------------------------------ descarga de un fichero


def test_fetch_and_filter_ok_records_last_modified() -> None:
    content = zip_bytes(gkg_text(line(themes="ENV_OIL;"), line()))
    e = entry_for(content)
    srv = Server({e.name: [ok(content)]})
    with srv.client() as c:
        df, rec = g.fetch_and_filter(c, e, CFG, sleep=no_sleep)
    assert df is not None
    assert df.height == 1
    assert rec.status == "ok"
    assert rec.last_modified_utc == "2024-06-10T14:05:00+00:00"
    assert (rec.rows_in, rec.kept, rec.http_status) == (2, 1, 200)
    assert rec.md5 == e.md5


def test_fetch_uses_https_prefix() -> None:
    content = zip_bytes(gkg_text(line()))
    e = entry_for(content)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return ok(content)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        g.fetch_and_filter(c, e, CFG, sleep=no_sleep)
    assert seen == [f"https://data.gdeltproject.org/gdeltv2/{e.name}"]


def test_fetch_missing_last_modified_is_none() -> None:
    content = zip_bytes(gkg_text(line()))
    e = entry_for(content)
    with Server({e.name: [ok(content, lm=None)]}).client() as c:
        _, rec = g.fetch_and_filter(c, e, CFG, sleep=no_sleep)
    assert rec.last_modified_utc is None


def test_fetch_404_is_recorded_not_silent() -> None:
    e = entry_for(b"x")
    with Server({e.name: [httpx.Response(404)]}).client() as c:
        df, rec = g.fetch_and_filter(c, e, CFG, sleep=no_sleep)
    assert df is None
    assert rec.status == "missing_404"
    assert rec.http_status == 404


def test_fetch_md5_mismatch_fails() -> None:
    content = zip_bytes(gkg_text(line()))
    e = entry_for(content)
    bad = g.IndexEntry(e.file_ts, e.kind, e.size, "0" * 32, e.url)
    with (
        Server({e.name: [ok(content)]}).client() as c,
        pytest.raises(RawIntegrityError, match="md5"),
    ):
        g.fetch_and_filter(c, bad, CFG, sleep=no_sleep)


def test_fetch_size_mismatch_fails() -> None:
    content = zip_bytes(gkg_text(line()))
    e = entry_for(content)
    bad = g.IndexEntry(e.file_ts, e.kind, e.size + 1, e.md5, e.url)
    with (
        Server({e.name: [ok(content)]}).client() as c,
        pytest.raises(RawIntegrityError, match="tamaño"),
    ):
        g.fetch_and_filter(c, bad, CFG, sleep=no_sleep)


def test_fetch_retries_transient_then_succeeds() -> None:
    content = zip_bytes(gkg_text(line()))
    e = entry_for(content)
    srv = Server({e.name: [httpx.Response(503), httpx.Response(429), ok(content)]})
    slept: list[float] = []
    with srv.client() as c:
        _, rec = g.fetch_and_filter(c, e, CFG, sleep=slept.append)
    assert rec.status == "ok"
    assert slept == [2.0, 4.0]
    assert rec.attempts == 3


def test_fetch_persistent_network_error_fails() -> None:
    e = entry_for(b"x")
    calls: list[int] = []

    def boom(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("down", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(boom)) as c,
        pytest.raises(SourceAccessError, match="ConnectError"),
    ):
        g.fetch_and_filter(c, e, CFG, sleep=no_sleep)
    assert len(calls) == len(CFG.ingest.retry_backoff_s) + 1


def test_fetch_persistent_5xx_fails() -> None:
    e = entry_for(b"x")
    with (
        Server({e.name: [httpx.Response(500)]}).client() as c,
        pytest.raises(SourceAccessError, match="500"),
    ):
        g.fetch_and_filter(c, e, CFG, sleep=no_sleep)


def test_fetch_other_4xx_fails_without_retry() -> None:
    e = entry_for(b"x")
    srv = Server({e.name: [httpx.Response(403)]})
    with srv.client() as c, pytest.raises(SourceAccessError, match="403"):
        g.fetch_and_filter(c, e, CFG, sleep=no_sleep)
    assert len(srv.calls) == 1


# ------------------------------------------------------------------ día completo


IDX = g.IndexRef("gdelt/index/m.txt", "f" * 64, "2026-10-15T05:00:00+00:00")


def make_store(tmp_path: Path, commit: str = "abc", flt: GkgFilterConfig = FLT) -> g.GkgDayStore:
    return g.GkgDayStore(tmp_path / "raw", tmp_path / "manifests", flt, commit, IDX)


def day_fixture() -> tuple[list[g.IndexEntry], Server]:
    t1 = datetime(2024, 6, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2024, 6, 10, 0, 15, tzinfo=UTC)
    t3 = datetime(2024, 6, 10, 0, 30, tzinfo=UTC)
    c1 = zip_bytes(gkg_text(line(ts=t1, record_id="a1", themes="ENV_OIL;"), line(ts=t1)), t1)
    c2 = zip_bytes(gkg_text(line(ts=t2, record_id="b1", title="Brent rises")), t2)
    e1, e2, e3 = entry_for(c1, t1), entry_for(c2, t2), entry_for(b"zz", t3)
    srv = Server({e1.name: [ok(c1)], e2.name: [ok(c2)], e3.name: [httpx.Response(404)]})
    return [e1, e2, e3], srv


def run_day(store: g.GkgDayStore, entries: list[g.IndexEntry], srv: Server) -> g.DayOutput:
    return g.process_day(DAY, entries, CFG, store.spec(), client_factory=srv.client, sleep=no_sleep)


def test_process_day_writes_only_to_staging(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    entries, srv = day_fixture()
    out = run_day(store, list(reversed(entries)), srv)
    staged = Path(out.staged_path)
    assert staged.parent == store.staging_dir
    assert not (store.raw_root / out.rel_path).exists()
    assert out.rel_path == "gdelt_gkg_oil/gkg-oil-v2/2024/06/20240610.parquet"
    assert not staged.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    df = pl.read_parquet(staged)
    assert labels(df) == ["a1", "b1"]
    assert df.schema == g.OUTPUT_SCHEMA
    assert out.n_rows == 2
    assert [r.status for r in out.files] == ["ok", "ok", "missing_404"]
    assert out.sha256 == hashlib.sha256(staged.read_bytes()).hexdigest()


def test_process_day_is_deterministic(tmp_path: Path) -> None:
    e1, s1 = day_fixture()
    e2, s2 = day_fixture()
    a = run_day(make_store(tmp_path / "a"), e1, s1)
    b = run_day(make_store(tmp_path / "b"), e2, s2)
    assert a.sha256 == b.sha256


def test_process_day_reject_threshold_fails(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    content = zip_bytes(gkg_text(*[line(record_id=str(i)) for i in range(5)], "broken\tline"))
    e = entry_for(content)
    ingest = CFG.ingest.model_copy(update={"reject_lines_tolerance_per_file": 5})
    cfg = CFG.model_copy(update={"ingest": ingest})
    with pytest.raises(SchemaError, match="rechazadas"):
        g.process_day(
            DAY, [e], cfg, store.spec(), client_factory=Server({e.name: [ok(content)]}).client
        )
    assert not list(tmp_path.rglob("*.parquet"))


def test_file_reject_threshold_fails() -> None:
    # Regresión (revisión adversarial): un fichero pequeño mal formado pasaba el umbral diario.
    content = zip_bytes(gkg_text(line(), "x", "y", "z"))
    e = entry_for(content)
    with (
        Server({e.name: [ok(content)]}).client() as c,
        pytest.raises(SchemaError, match="en el fichero"),
    ):
        g.fetch_and_filter(c, e, CFG, sleep=no_sleep)


def test_file_reject_within_tolerance_passes() -> None:
    content = zip_bytes(gkg_text(*[line(record_id=str(i)) for i in range(3)], "frag"))
    e = entry_for(content)
    with Server({e.name: [ok(content)]}).client() as c:
        _, rec = g.fetch_and_filter(c, e, CFG, sleep=no_sleep)
    assert (rec.rows_in, rec.rejected) == (2, 2)


def test_process_day_too_many_404_fails(tmp_path: Path) -> None:
    # Regresión (revisión adversarial): un 404 dejaba el día "done" para siempre sin límite.
    store = make_store(tmp_path)
    n = CFG.ingest.max_missing_404_per_day + 1
    entries = [
        entry_for(b"x", datetime(2024, 6, 10, i // 4, 15 * (i % 4), tzinfo=UTC)) for i in range(n)
    ]
    srv = Server({e.name: [httpx.Response(404)] for e in entries})
    with pytest.raises(SourceAccessError, match="404"):
        run_day(store, entries, srv)


def test_process_day_empty_day_writes_empty_output(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    out = run_day(store, [], Server({}))
    assert out.n_rows == 0
    assert pl.read_parquet(out.staged_path).schema == g.OUTPUT_SCHEMA


def read_manifest(store: g.GkgDayStore) -> list[dict[str, object]]:
    path = store.manifest_path(DAY)
    return [json.loads(x) for x in gzip.decompress(path.read_bytes()).decode().splitlines()]


def test_publish_and_status(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    assert store.status(DAY) == "todo"
    entries, srv = day_fixture()
    out = run_day(store, entries, srv)
    assert store.publish(out) is False
    assert not Path(out.staged_path).exists()
    path = store.raw_root / out.rel_path
    assert not path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert store.status(DAY) == "done"
    (rec,) = read_manifest(store)
    assert rec["sha256"] == out.sha256
    assert rec["filter_version"] == "gkg-oil-v2"
    assert rec["filter_sha256"] == g.filter_sha256(FLT)
    assert rec["code_commit"] == "abc"
    assert rec["index"] == {
        "rel_path": "gdelt/index/m.txt",
        "sha256": "f" * 64,
        "last_file_ts": "2026-10-15T05:00:00+00:00",
    }
    assert rec["polars_version"] == pl.__version__
    assert rec["adopted"] is False
    assert rec["n_no_last_modified"] == 0
    files = rec["files"]
    assert isinstance(files, list)
    assert [f["status"] for f in files] == ["ok", "ok", "missing_404"]
    assert files[0]["last_modified_utc"] == "2024-06-10T14:05:00+00:00"
    with pytest.raises(RawIntegrityError, match="ya registrado"):
        store.record(out)


def test_publish_adopts_identical_orphan_and_rejects_different(tmp_path: Path) -> None:
    # Regresión (revisión adversarial): una salida huérfana quedaba irrecuperable.
    store = make_store(tmp_path)
    entries, srv = day_fixture()
    out = run_day(store, entries, srv)
    dest = store.raw_root / out.rel_path
    dest.parent.mkdir(parents=True)
    os.link(out.staged_path, dest)  # simula muerte entre enlace y registro
    assert store.status(DAY) == "orphan"
    assert store.publish(out) is True
    assert store.status(DAY) == "done"
    assert read_manifest(store)[0]["adopted"] is True

    other = make_store(tmp_path / "o")
    entries, srv = day_fixture()
    out2 = run_day(other, entries, srv)
    d2 = other.raw_root / out2.rel_path
    d2.parent.mkdir(parents=True)
    d2.write_bytes(b"different")
    with pytest.raises(RawIntegrityError, match="contenido distinto"):
        other.publish(out2)


def test_publish_rejects_altered_staging(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    entries, srv = day_fixture()
    out = run_day(store, entries, srv)
    forged = g.DayOutput(
        out.day, out.rel_path, out.staged_path, "0" * 64, out.n_bytes, out.n_rows, out.files
    )
    with pytest.raises(RawIntegrityError, match="distinto"):
        store.publish(forged)


def test_record_rejects_sha_mismatch(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    entries, srv = day_fixture()
    out = run_day(store, entries, srv)
    store.publish(out)
    forged = g.DayOutput(date(2024, 6, 12), out.rel_path, "", "0" * 64, 0, 0, ())
    with pytest.raises(RawIntegrityError, match="distinto"):
        store.record(forged)


def test_status_detects_tampered_and_missing(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    entries, srv = day_fixture()
    out = run_day(store, entries, srv)
    store.publish(out)
    path = store.raw_root / out.rel_path
    os.chmod(path, 0o644)
    path.write_bytes(b"tampered")
    with pytest.raises(RawIntegrityError, match="checksum"):
        store.status(DAY)
    path.unlink()
    with pytest.raises(RawIntegrityError, match="falta"):
        store.status(DAY)


def test_status_detects_filter_change_without_new_version(tmp_path: Path) -> None:
    # Regresión (revisión adversarial): editar el filtro sin cambiar version no se detectaba.
    store = make_store(tmp_path)
    entries, srv = day_fixture()
    store.publish(run_day(store, entries, srv))
    changed = FLT.model_copy(update={"theme_tokens": (*FLT.theme_tokens, "LNG")})
    with pytest.raises(RawIntegrityError, match="filtro cambió"):
        make_store(tmp_path, flt=changed).status(DAY)


def test_manifest_duplicate_or_corrupt_fails(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    entries, srv = day_fixture()
    store.publish(run_day(store, entries, srv))
    mpath = store.manifest_path(DAY)
    good = mpath.read_bytes()
    mpath.write_bytes(good * 2)
    with pytest.raises(RawIntegrityError, match="duplicad"):
        store.status(DAY)
    mpath.write_bytes(good[:-10])  # gzip truncado
    with pytest.raises(RawIntegrityError, match="ilegible"):
        store.status(DAY)


def test_lock_is_exclusive_and_cleans_staging(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.staging_dir.mkdir(parents=True)
    leftover = store.staging_dir / "20240610-x.parquet"
    leftover.write_bytes(b"x")
    with store.lock():
        assert not leftover.exists()
        with pytest.raises(RawIntegrityError, match="otra ejecución"), make_store(tmp_path).lock():
            pass


# ------------------------------------------------------------------ ejecución completa


def runner(
    tmp_path: Path,
    srv: Server,
    *,
    commit: str = "abc",
    free_gb: Callable[[], float] = lambda: 100.0,
    allow_dirty: bool = False,
) -> Callable[[dict[date, list[g.IndexEntry]]], g.RunSummary]:
    store = make_store(tmp_path, commit)

    def run(days: dict[date, list[g.IndexEntry]]) -> g.RunSummary:
        with ThreadPoolExecutor(max_workers=2) as ex:
            return g.ingest_gkg(
                days,
                CFG,
                store,
                executor=ex,
                client_factory=srv.client,
                free_disk_gb=free_gb,
                logger=logging.getLogger("test"),
                sleep=no_sleep,
                allow_dirty=allow_dirty,
            )

    return run


def test_ingest_gkg_end_to_end_and_idempotent(tmp_path: Path) -> None:
    entries, srv = day_fixture()
    other = date(2024, 6, 11)
    run = runner(tmp_path, srv)
    s1 = run({DAY: entries, other: []})
    assert s1.written == [DAY, other]
    assert s1.failed == {}
    assert not list((tmp_path / "staging").rglob("*.parquet"))
    n_calls = len(srv.calls)
    s2 = run({DAY: entries, other: []})
    assert s2.already_done == [DAY, other]
    assert s2.written == []
    assert len(srv.calls) == n_calls  # sin red


def test_ingest_gkg_failed_day_reported_others_continue(tmp_path: Path) -> None:
    entries, srv = day_fixture()
    bad = g.IndexEntry(entries[0].file_ts, "gkg", entries[0].size, "0" * 32, entries[0].url)
    run = runner(tmp_path, srv)
    s = run({DAY: [bad], date(2024, 6, 11): []})
    assert list(s.failed) == [DAY]
    assert "md5" in s.failed[DAY]
    assert s.written == [date(2024, 6, 11)]
    assert not s.ok


def test_ingest_gkg_stops_on_low_disk(tmp_path: Path) -> None:
    _, srv = day_fixture()
    s = runner(tmp_path, srv, free_gb=lambda: 0.5)({DAY: []})
    assert s.not_run == [DAY]
    assert s.stopped_reason is not None
    assert not s.ok


def test_ingest_gkg_refuses_dirty_tree(tmp_path: Path) -> None:
    _, srv = day_fixture()
    with pytest.raises(RawIntegrityError, match="dirty"):
        runner(tmp_path, srv, commit="abc-dirty")({DAY: []})
    s = runner(tmp_path, srv, commit="abc-dirty", allow_dirty=True)({DAY: []})
    assert s.written == [DAY]


def test_ingest_gkg_adopts_identical_orphan(tmp_path: Path) -> None:
    entries, srv = day_fixture()
    store = make_store(tmp_path)
    out = run_day(store, entries, srv)
    dest = store.raw_root / out.rel_path
    dest.parent.mkdir(parents=True)
    os.link(out.staged_path, dest)
    s = runner(tmp_path, srv)({DAY: entries})
    assert s.adopted == [DAY]
    assert s.ok


def test_ingest_gkg_different_orphan_is_failure(tmp_path: Path) -> None:
    entries, srv = day_fixture()
    dest = tmp_path / "raw/gdelt_gkg_oil/gkg-oil-v2/2024/06/20240610.parquet"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"other")
    s = runner(tmp_path, srv)({DAY: entries})
    assert "contenido distinto" in s.failed[DAY]


def test_ingest_gkg_tampered_done_day_is_failure(tmp_path: Path) -> None:
    entries, srv = day_fixture()
    run = runner(tmp_path, srv)
    run({DAY: entries})
    path = tmp_path / "raw/gdelt_gkg_oil/gkg-oil-v2/2024/06/20240610.parquet"
    os.chmod(path, 0o644)
    path.write_bytes(b"x")
    s = run({DAY: entries})
    assert "checksum" in s.failed[DAY]


def test_unexpected_exception_cancels_pending_and_propagates(tmp_path: Path) -> None:
    def broken_factory() -> httpx.Client:
        raise RuntimeError("bug")

    store = make_store(tmp_path)
    days: dict[date, list[g.IndexEntry]] = {date(2024, 6, d): [] for d in range(1, 20)}
    with ThreadPoolExecutor(max_workers=1) as ex, pytest.raises(RuntimeError, match="bug"):
        g.ingest_gkg(
            days,
            CFG,
            store,
            executor=ex,
            client_factory=broken_factory,
            free_disk_gb=lambda: 100.0,
            logger=logging.getLogger("test"),
        )
    assert not list(tmp_path.rglob("*.parquet"))


def test_summary_counts() -> None:
    s = g.RunSummary(already_done=[DAY], written=[], failed={}, not_run=[])
    assert s.ok


# ------------------------------------------------------------------ pipeline e infraestructura


def index_store(tmp_path: Path, index_text: str) -> tuple[Path, Path]:
    raw, man = tmp_path / "raw", tmp_path / "manifests"
    idx_srv = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=index_text))
    )
    with idx_srv as c:
        RawStore(raw, man, "abc").fetch(c, "gdelt", "https://x/master.txt", "gdelt/index/m.txt")
    return raw, man


def pipeline(raw: Path, man: Path, srv: Server, end: date = date(2024, 6, 11)) -> g.RunSummary:
    return g.run_pipeline(
        DAY,
        end,
        CFG,
        raw_root=raw,
        manifest_dir=man,
        index_rel="gdelt/index/m.txt",
        commit="abc",
        logger=logging.getLogger("test"),
        workers=2,
        executor_factory=lambda n: ThreadPoolExecutor(max_workers=n),
        client_factory=srv.client,
    )


def test_run_pipeline_verifies_index_and_ingests(tmp_path: Path) -> None:
    entries, srv = day_fixture()
    late = entry_for(b"q", datetime(2024, 6, 12, 1, 0, tzinfo=UTC))
    index_text = "".join(f"{e.size} {e.md5} {e.url}\n" for e in [*entries, late])
    raw, man = index_store(tmp_path, index_text)
    s = pipeline(raw, man, srv)
    assert s.written == [DAY]
    assert s.ok
    out = pl.read_parquet(raw / "gdelt_gkg_oil/gkg-oil-v2/2024/06/20240610.parquet")
    assert labels(out) == ["a1", "b1"]
    (rec,) = read_manifest(g.GkgDayStore(raw, man, FLT, "abc", IDX))
    index = rec["index"]
    assert isinstance(index, dict)
    assert index["last_file_ts"] == "2024-06-12T01:00:00+00:00"


def test_run_pipeline_rejects_end_beyond_index_coverage(tmp_path: Path) -> None:
    # Regresión (revisión adversarial): días fuera del índice quedaban "done" vacíos.
    entries, srv = day_fixture()
    index_text = "".join(f"{e.size} {e.md5} {e.url}\n" for e in entries)
    raw, man = index_store(tmp_path, index_text)
    with pytest.raises(SchemaError, match="cobertura"):
        pipeline(raw, man, srv, end=date(2024, 6, 11))


def test_run_pipeline_rejects_index_without_gkg(tmp_path: Path) -> None:
    raw, man = index_store(tmp_path, GOOD_INDEX.split("\n", maxsplit=1)[0] + "\n")
    with pytest.raises(SchemaError, match="no contiene"):
        pipeline(raw, man, Server({}))


def test_run_pipeline_rejects_unregistered_index(tmp_path: Path) -> None:
    with pytest.raises(RawIntegrityError, match="no está"):
        g.run_pipeline(
            DAY,
            date(2024, 6, 11),
            CFG,
            raw_root=tmp_path / "raw",
            manifest_dir=tmp_path / "m",
            index_rel="gdelt/index/m.txt",
            commit="abc",
            logger=logging.getLogger("test"),
        )


def test_infrastructure_helpers(tmp_path: Path) -> None:
    with g.make_client(5.0, 2) as c:
        assert c.timeout.read == 5.0
        assert c.follow_redirects is False
    assert g.disk_free_gb(tmp_path / "x") > 0
    ex = g.default_executor(1)
    ex.shutdown()


def test_last_modified_invalid_header_fails() -> None:
    content = zip_bytes(gkg_text(line()))
    e = entry_for(content)
    with (
        Server({e.name: [ok(content, lm="not a date")]}).client() as c,
        pytest.raises(SchemaError, match="Last-Modified"),
    ):
        g.fetch_and_filter(c, e, CFG, sleep=no_sleep)


def test_last_modified_minus_zero_is_utc() -> None:
    # Regresión (revisión adversarial): "-0000" daba un datetime naive y el día fallaba siempre.
    content = zip_bytes(gkg_text(line()))
    e = entry_for(content)
    with Server({e.name: [ok(content, lm="Mon, 10 Jun 2024 14:05:00 -0000")]}).client() as c:
        _, rec = g.fetch_and_filter(c, e, CFG, sleep=no_sleep)
    assert rec.last_modified_utc == "2024-06-10T14:05:00+00:00"


def test_gdelt_config_validation(tmp_path: Path) -> None:
    base = yaml.safe_load((Path(g.__file__).parents[3] / "config/gdelt.yaml").read_text())
    for mutate, msg in [
        (lambda d: d["gkg_filter"].update(title_regex="(?<=x)y"), "regex"),
        (lambda d: d["gkg_filter"].update(theme_tokens=["OIL", "OIL"]), "duplicados"),
        (lambda d: d["gkg_filter"].update(theme_tokens=["oil"]), "pattern"),
        (lambda d: d["gkg_filter"].update(version="V 1"), "pattern"),
    ]:
        d = yaml.safe_load(yaml.safe_dump(base))
        mutate(d)
        path = tmp_path / "gdelt.yaml"
        path.write_text(yaml.safe_dump(d), encoding="utf-8")
        with pytest.raises(SchemaError, match=msg):
            load_yaml_model(path, GdeltConfig)


def test_parse_masterfilelist_skips_blank_lines() -> None:
    entries, malformed = g.parse_masterfilelist("\n   \n" + GOOD_INDEX, max_malformed=1)
    assert (len(entries), malformed) == (4, 1)


def test_filter_last_line_without_newline_is_a_record() -> None:
    df, st = g.filter_gkg(line(themes="ENV_OIL;"), TS, FLT)
    assert (st.rows_in, df.height) == (1, 1)


def test_filter_rejects_record_followed_by_malformed_id_line() -> None:
    # Regresión (revisión adversarial): un fragmento que empieza como GKGRECORDID colaba
    # la primera mitad del registro partido.
    good = line(record_id="a", themes="ENV_OIL;")
    partial = line(record_id="b", themes="ENV_OIL;")
    fragment = "20240610141500-7\trest of title</PAGE_TITLE>"
    df, st = g.filter_gkg(gkg_text(good, partial, fragment), TS, FLT)
    assert labels(df) == ["a"]
    assert st.rejected == 2


def test_filter_url_criterion() -> None:
    text = gkg_text(
        line(record_id="markets-oil-prices-fall"),
        line(record_id="soil-health"),
        line(record_id="opec_meeting"),
    )
    df, st = g.filter_gkg(text, TS, FLT)
    assert labels(df) == ["markets-oil-prices-fall", "opec_meeting"]
    assert df["match_url"].to_list() == [True, True]
    assert st.match_url == 2

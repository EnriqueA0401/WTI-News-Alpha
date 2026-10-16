"""Tests de la capa de noticias normalizada (FASE_02_noticias.md). Datos SINTÉTICOS."""

import gzip
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from wti_news_alpha.config import CONFIG_DIR, NewsConfig, load_news_config
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.normalization import gdelt_news as gn

D = date.fromisoformat
LAG = timedelta(minutes=15)
STR_COLS = [
    "gkg_record_id",
    "v21_date",
    "v2_source_collection_identifier",
    "v2_source_common_name",
    "v2_document_identifier",
    "v1_counts",
    "v21_counts",
    "v1_themes",
    "v2_enhanced_themes",
    "v1_locations",
    "v2_enhanced_locations",
    "v1_persons",
    "v2_enhanced_persons",
    "v1_organizations",
    "v2_enhanced_organizations",
    "v15_tone",
    "v21_enhanced_dates",
    "v21_sharing_image",
    "v21_related_images",
    "v21_social_image_embeds",
    "v21_social_video_embeds",
    "v21_quotations",
    "v21_all_names",
    "v21_amounts",
    "v21_translation_info",
    "v2_extras_xml",
]


def _raw(rows: list[dict[str, object]]) -> pl.DataFrame:
    data: dict[str, list[object]] = {
        c: []
        for c in [
            *STR_COLS,
            "source_file_ts",
            "row_in_file",
            "match_theme",
            "match_title",
            "match_org",
            "match_url",
        ]
    }
    for i, r in enumerate(rows):
        for c in STR_COLS:
            data[c].append(r.get(c, ""))
        data["source_file_ts"].append(r["ts"])
        data["row_in_file"].append(r.get("row", i))
        for c in ("match_theme", "match_title", "match_org", "match_url"):
            data[c].append(c == "match_theme")
    schema = {c: pl.Utf8 for c in STR_COLS} | {
        "source_file_ts": pl.Datetime("us", "UTC"),
        "row_in_file": pl.UInt32,
        "match_theme": pl.Boolean,
        "match_title": pl.Boolean,
        "match_org": pl.Boolean,
        "match_url": pl.Boolean,
    }
    return pl.DataFrame(data, schema=schema)


def _pub(ts: str) -> str:
    return f"<PAGE_PRECISEPUBTIMESTAMP>{ts}</PAGE_PRECISEPUBTIMESTAMP>"


T0 = datetime(2024, 3, 10, 6, 0, tzinfo=UTC)  # domingo del cambio de horario en EE. UU.


def _entry(
    lm: dict[datetime, datetime | None] | None = None, day: str = "2024-03-10"
) -> gn.DayEntry:
    return gn.DayEntry(
        D(day),
        "gkg-oil-v2",
        "x.parquet",
        "0" * 64,
        datetime(2026, 10, 15, tzinfo=UTC),
        lm if lm is not None else {T0: T0 - timedelta(minutes=5)},
    )


def test_first_available_at_and_fields() -> None:
    raw = _raw(
        [
            {
                "ts": T0,
                "v15_tone": "-2.5,1,3",
                "v2_document_identifier": "https://a/b",
                "v2_extras_xml": "<PAGE_TITLE>Oil jumps</PAGE_TITLE>" + _pub("20240310055000"),
            }
        ]
    )
    out, n_bad = gn.normalize_day(raw, _entry(), LAG)
    r = out.row(0, named=True)
    assert out.columns == list(gn.OUTPUT_COLUMNS)
    assert r["first_available_at"] == datetime(2024, 3, 10, 6, 15, tzinfo=UTC)
    assert r["published_at"] == datetime(2024, 3, 10, 5, 50, tzinfo=UTC)
    assert r["title"] == "Oil jumps"
    assert r["tone"] == -2.5
    assert r["late_file"] is False
    assert r["exclude_reason"] is None
    assert n_bad == 0


def test_flags_late_file_and_future_publication() -> None:
    raw = _raw(
        [
            {
                "ts": T0,
                "v15_tone": "1",
                "v2_extras_xml": _pub("20240310070000"),
            },
            {
                "ts": T0,
                "v15_tone": "1",
                "v2_extras_xml": _pub("20241399000000"),
            },
            {"ts": T0, "v15_tone": ""},
        ]
    )
    out, n_bad = gn.normalize_day(raw, _entry({T0: T0 + timedelta(minutes=35)}), LAG)
    assert out["late_file"].to_list() == [True, True, True]
    assert out["exclude_reason"].to_list() == ["published_after_available", None, None]
    assert out["published_at"].null_count() == 2
    assert out["tone"].to_list()[2] is None
    assert n_bad == 1


def test_missing_last_modified_is_not_late() -> None:
    out, _ = gn.normalize_day(_raw([{"ts": T0, "v15_tone": "1"}]), _entry({T0: None}), LAG)
    assert out["late_file"].to_list() == [False]
    assert out["file_last_modified_utc"].to_list() == [None]


def test_bad_tone_raises() -> None:
    with pytest.raises(SchemaError, match="tonos"):
        gn.normalize_day(_raw([{"ts": T0, "v15_tone": "abc,1"}]), _entry(), LAG)


def test_row_from_file_not_in_manifest_raises() -> None:
    raw = _raw([{"ts": T0 + timedelta(minutes=15), "v15_tone": "1"}])
    with pytest.raises(SchemaError, match="fuera del manifiesto"):
        gn.normalize_day(raw, _entry(), LAG)


def test_ingested_before_available_raises() -> None:
    e = gn.DayEntry(
        D("2024-03-10"), "v", "x", "0" * 64, datetime(2024, 3, 10, 6, 5, tzinfo=UTC), {T0: None}
    )
    with pytest.raises(SchemaError, match="incoherente"):
        gn.normalize_day(_raw([{"ts": T0, "v15_tone": "1"}]), e, LAG)


def test_config() -> None:
    c = load_news_config(CONFIG_DIR)
    assert c.availability_lag_minutes.require("l") == 15
    data = c.model_dump(by_alias=True)
    data["sources"][1]["start"] = D("2020-06-01")
    with pytest.raises(ValueError, match="solapados"):
        NewsConfig.model_validate(data)
    data = c.model_dump(by_alias=True)
    data["sources"][0]["end"] = data["sources"][0]["start"]
    with pytest.raises(ValueError, match="vacío"):
        NewsConfig.model_validate(data)


# ---------------------------------------------------------------- integración


def _write_day(
    root: Path, mroot: Path, version: str, day: date, rows: list[dict[str, object]]
) -> None:
    rel = f"gdelt_gkg_oil/{version}/{day:%Y/%m/%Y%m%d}.parquet"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    _raw(rows).write_parquet(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    ts = {r["ts"] for r in rows}
    line = {
        "day": str(day),
        "filter_version": version,
        "path": rel,
        "sha256": sha,
        "recorded_at_utc": "2026-10-15T00:00:00+00:00",
        "files": [
            {"name": f"{t:%Y%m%d%H%M%S}.gkg.csv.zip", "last_modified_utc": None} for t in sorted(ts)
        ],  # type: ignore[str-format]
    }
    mpath = mroot / "gdelt_gkg_oil" / version / f"{day:%Y-%m}.jsonl.gz"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(mpath, "at", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def _cfg(sources: list[tuple[str, str, str]]) -> NewsConfig:
    data = load_news_config(CONFIG_DIR).model_dump(by_alias=True)
    data["sources"] = [{"filter_version": v, "start": D(a), "end": D(b)} for v, a, b in sources]
    return NewsConfig.model_validate(data)


def _run(tmp_path: Path, cfg: NewsConfig) -> object:
    return gn.run_normalize(
        raw_root=tmp_path / "raw",
        out_root=tmp_path / "n",
        manifest_dir=tmp_path / "m",
        cfg=cfg,
        commit="abc",
    )


def _day_rows(day: date) -> list[dict[str, object]]:
    t = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return [{"ts": t, "v15_tone": "1", "row": 1}, {"ts": t, "v15_tone": "2", "row": 0}]


def test_run_end_to_end(tmp_path: Path) -> None:
    for v, d in [
        ("gkg-oil-v3", D("2018-01-31")),
        ("gkg-oil-v3", D("2018-02-01")),
        ("gkg-oil-v2", D("2024-01-01")),
    ]:
        _write_day(tmp_path / "raw", tmp_path / "m", v, d, _day_rows(d))
    cfg = _cfg(
        [("gkg-oil-v3", "2018-01-31", "2018-02-02"), ("gkg-oil-v2", "2024-01-01", "2024-01-02")]
    )
    res = _run(tmp_path, cfg)
    assert sorted(res.outputs) == ["201801.parquet", "201802.parquet", "202401.parquet"]  # type: ignore[attr-defined]
    assert res.manifest["totals"]["rows"] == 6  # type: ignore[attr-defined]
    out = pl.read_parquet(tmp_path / "n" / "gdelt_news" / "gdelt-news-v1" / "201801.parquet")
    assert out["row_in_file"].to_list() == [0, 1]
    assert out["filter_version"].to_list() == ["gkg-oil-v3"] * 2


def test_run_missing_day_raises(tmp_path: Path) -> None:
    _write_day(
        tmp_path / "raw", tmp_path / "m", "gkg-oil-v3", D("2018-01-31"), _day_rows(D("2018-01-31"))
    )
    with pytest.raises(RawIntegrityError, match="faltan 1 días"):
        _run(tmp_path, _cfg([("gkg-oil-v3", "2018-01-31", "2018-02-02")]))


def test_run_altered_day_raises(tmp_path: Path) -> None:
    d = D("2018-01-31")
    _write_day(tmp_path / "raw", tmp_path / "m", "gkg-oil-v3", d, _day_rows(d))
    (tmp_path / "raw" / "gdelt_gkg_oil/gkg-oil-v3/2018/01/20180131.parquet").write_bytes(b"x")
    with pytest.raises(RawIntegrityError, match="integridad"):
        _run(tmp_path, _cfg([("gkg-oil-v3", "2018-01-31", "2018-02-01")]))


def test_duplicate_manifest_day_raises(tmp_path: Path) -> None:
    d = D("2018-01-31")
    _write_day(tmp_path / "raw", tmp_path / "m", "gkg-oil-v3", d, _day_rows(d))
    mpath = tmp_path / "m/gdelt_gkg_oil/gkg-oil-v3/2018-01.jsonl.gz"
    with gzip.open(mpath, "rt") as fh:
        lines = fh.read()
    with gzip.open(mpath, "wt") as fh:
        fh.write(lines + lines)
    with pytest.raises(RawIntegrityError, match="duplicado"):
        gn.load_day_entries(tmp_path / "m", "gkg-oil-v3")


def test_wrong_version_in_manifest_raises(tmp_path: Path) -> None:
    d = D("2018-01-31")
    _write_day(tmp_path / "raw", tmp_path / "m", "gkg-oil-v3", d, _day_rows(d))
    src = tmp_path / "m/gdelt_gkg_oil/gkg-oil-v3/2018-01.jsonl.gz"
    dst = tmp_path / "m/gdelt_gkg_oil/gkg-oil-v2/2018-01.jsonl.gz"
    dst.parent.mkdir(parents=True)
    dst.write_bytes(src.read_bytes())
    with pytest.raises(RawIntegrityError, match="versión"):
        gn.load_day_entries(tmp_path / "m", "gkg-oil-v2")


def test_duplicate_row_keys_raise(tmp_path: Path) -> None:
    d = D("2018-01-31")
    t = datetime(2018, 1, 31, tzinfo=UTC)
    _write_day(
        tmp_path / "raw",
        tmp_path / "m",
        "gkg-oil-v3",
        d,
        [{"ts": t, "v15_tone": "1", "row": 0}, {"ts": t, "v15_tone": "1", "row": 0}],
    )
    with pytest.raises(SchemaError, match="duplicadas"):
        _run(tmp_path, _cfg([("gkg-oil-v3", "2018-01-31", "2018-02-01")]))

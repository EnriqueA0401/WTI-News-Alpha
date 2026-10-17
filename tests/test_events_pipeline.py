"""Tests de la capa de eventos (FASE_03.md). Noticias SINTÉTICAS."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from wti_news_alpha.config import CONFIG_DIR, load_event_rules_config, load_events_config
from wti_news_alpha.errors import RawIntegrityError
from wti_news_alpha.events import pipeline as ep

T0 = datetime(2024, 1, 31, 22, 0, tzinfo=UTC)
LOC_SA = "1#Saudi Arabia#SA#SA#25#45#SA"


def _news(rows: list[tuple[int, str, str, str | None]]) -> pl.DataFrame:
    """(minutos desde T0, medio, url, título) con temas y localización fijos."""
    ts = [T0 + timedelta(minutes=15 * (m // 15)) for m, *_ in rows]
    return pl.DataFrame(
        {
            "source_file_ts": ts,
            "row_in_file": list(range(len(rows))),
            "first_available_at": [t + timedelta(minutes=15) for t in ts],
            "exclude_reason": [None] * len(rows),
            "source_common_name": [r[1] for r in rows],
            "url": [r[2] for r in rows],
            "title": [r[3] for r in rows],
            "themes": ["ENV_OIL"] * len(rows),
            "organizations": [""] * len(rows),
            "locations": [LOC_SA] * len(rows),
            "tone": [0.0] * len(rows),
        },
        schema_overrides={
            "source_file_ts": pl.Datetime("us", "UTC"),
            "first_available_at": pl.Datetime("us", "UTC"),
            "exclude_reason": pl.Utf8,
            "title": pl.Utf8,
            "row_in_file": pl.UInt32,
        },
    )


def _setup(tmp_path: Path) -> None:
    base = tmp_path / "norm" / "gdelt_news" / "gdelt-news-v2"
    base.mkdir(parents=True)
    t1 = "OPEC+ agrees to deepen output cuts through 2026"
    months = {
        "202401.parquet": _news(
            [
                (0, "a.com", "https://a.com/1", t1),
                (30, "b.com", "https://b.com/1", t1),  # re-publicación
                (45, "c.com", "https://c.com/2", "Saudi Arabia pledges deeper OPEC output cuts"),
                (60, "d.com", "https://d.com/3", "Local bakery opens new store downtown"),
            ]
        ),
        "202402.parquet": _news(
            [(180, "e.com", "https://e.com/4", "OPEC+ ministers confirm production cuts")]
        ),
    }
    outputs = {}
    for name, df in months.items():
        df.write_parquet(base / name)
        outputs[name] = hashlib.sha256((base / name).read_bytes()).hexdigest()
    mdir = tmp_path / "manifests" / "normalized" / "gdelt_news"
    mdir.mkdir(parents=True)
    (mdir / "gdelt-news-v2.json").write_text(json.dumps({"outputs": outputs}))


def _run(tmp_path: Path) -> object:
    return ep.run_build(
        out_root=tmp_path / "norm",
        manifest_dir=tmp_path / "manifests",
        cfg=load_events_config(CONFIG_DIR),
        rcfg=load_event_rules_config(CONFIG_DIR),
        commit="abc",
    )


def test_build_end_to_end(tmp_path: Path) -> None:
    _setup(tmp_path)
    res = _run(tmp_path)
    base = tmp_path / "norm" / "events" / "events-v2"
    jan = pl.read_parquet(base / "articles_202401.parquet").sort("row_in_file")
    feb = pl.read_parquet(base / "articles_202402.parquet")
    assert jan["relevance"].to_list() == ["direct", "direct", "direct", "indirect"]  # tema ENV_OIL
    assert jan["dup_kind"].to_list() == [None, "syndicated_title", None, None]
    # Los tres primeros y el de febrero (cruza el mes, < 24 h) son el mismo evento OPEP+/SA.
    ev = jan["event_id"].to_list()[0]
    assert jan["event_id"].to_list() == [ev, ev, ev, None]
    assert feb["event_id"].to_list() == [ev]
    assert feb["event_seq"].to_list() == [3]
    assert feb["event_sources_so_far"].to_list() == [3]
    events = pl.read_parquet(base / "events.parquet")
    assert events.height == 1
    assert events.row(0, named=True)["n_articles_final"] == 4
    assert events.row(0, named=True)["n_unique_articles_final"] == 3
    assert res.manifest["n_articles"] == 5  # type: ignore[attr-defined]
    assert res.manifest["dup_counts"] == {"none": 4, "syndicated_title": 1}  # type: ignore[attr-defined]


def test_build_detects_altered_news(tmp_path: Path) -> None:
    _setup(tmp_path)
    (tmp_path / "norm" / "gdelt_news" / "gdelt-news-v2" / "202401.parquet").write_bytes(b"x")
    with pytest.raises(RawIntegrityError, match="integridad"):
        _run(tmp_path)


def test_build_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(RawIntegrityError, match="manifiesto"):
        _run(tmp_path)

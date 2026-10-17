"""Cargador de datos de modelado (revisión F6). Datos SINTÉTICOS."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from wti_news_alpha.evaluation import sealed_guard as sg
from wti_news_alpha.models import data

UTC_US = pl.Datetime("us", "UTC")
WIN = sg.Window(datetime(2025, 7, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC))


def test_loader_keeps_research_rows_and_feature_allowlist(tmp_path: Path) -> None:
    t = [datetime(2025, 6, 30, tzinfo=UTC), datetime(2025, 7, 2, tzinfo=UTC)]
    (tmp_path / "f").mkdir()
    (tmp_path / "l").mkdir()
    pl.DataFrame(
        {"decision_time": t, "mkt_close": [70.0, 71.0], "feature_version": ["v", "v"]},
        schema_overrides={"decision_time": UTC_US},
    ).write_parquet(tmp_path / "f" / "a.parquet")
    pl.DataFrame(
        {
            "decision_time": t,
            "ret_15m": [0.1, 0.2],
            "ret_60m": [0.1, 0.2],
            "label_end_15m": [x + timedelta(minutes=16) for x in t],
            "label_end_60m": [x + timedelta(minutes=61) for x in t],
        },
        schema_overrides={
            "decision_time": UTC_US,
            "label_end_15m": UTC_US,
            "label_end_60m": UTC_US,
        },
    ).write_parquet(tmp_path / "l" / "a.parquet")
    (tmp_path / "repo").mkdir()
    guard = sg.SealedGuard(
        WIN, repo=tmp_path / "repo", prereg="p.yaml", log=tmp_path / "repo" / "l.jsonl"
    )
    rf = data.load_research_frame(
        features_dir=tmp_path / "f", labels_dir=tmp_path / "l", guard=guard
    )
    assert rf.df["decision_time"].to_list() == [t[0]]
    assert rf.allowed == frozenset({"mkt_close"})

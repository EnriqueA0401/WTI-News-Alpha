"""Controles sintéticos del pipeline completo (CLAUDE.md §8.5): positivo, negativo y canario.
Datos SINTÉTICOS (``models.synthetic``)."""

from datetime import timedelta

import polars as pl
import pytest

from wti_news_alpha.errors import LeakageError
from wti_news_alpha.labels import splits
from wti_news_alpha.models import compare, pipeline, synthetic

M3 = pipeline.ModelSpec("M3", ("mkt", "wpsr", "cal"))
M4 = pipeline.ModelSpec("M4", ("mkt", "wpsr", "cal", "news"))


def _folds(df: pl.DataFrame) -> list[splits.Fold]:
    start = df["decision_time"].min() + timedelta(days=120)  # type: ignore[operator]
    end = df["decision_time"].max() + timedelta(days=1)  # type: ignore[operator]
    return splits.walk_forward(
        pipeline.purge_frame(df, 15),
        first_test=start,
        end=end,
        step="quarter",
        embargo=timedelta(days=1),
    )


def _compare(df: pl.DataFrame) -> compare.Comparison:
    folds = _folds(df)
    p3 = pipeline.run_walk_forward(df, M3, folds, horizon_min=15)
    p4 = pipeline.run_walk_forward(df, M4, folds, horizon_min=15)
    j = p3.join(p4, on="decision_time", suffix="_4", validate="1:1")
    blocks = j["decision_time"].dt.strftime("%Y-%m-%d").to_list()
    return compare.compare_losses(
        j["loss"].to_list(), j["loss_4"].to_list(), blocks, n_boot=500, seed=1
    )


def test_positive_control_m4_detects_planted_news_signal() -> None:
    res = _compare(synthetic.make_dataset(n_days=400, signal=0.8, seed=11))
    assert res.mean_diff > 0
    assert res.p_dm < 0.01
    assert res.p_bootstrap < 0.01


def test_negative_control_no_signal_no_improvement() -> None:
    res = _compare(synthetic.make_dataset(n_days=400, signal=0.0, seed=12))
    assert res.p_dm > 0.01
    assert res.mean_diff < 0.01  # sin mejora material (log loss por fila)


def test_leakage_canary_is_blocked_and_forced_is_near_perfect() -> None:
    df = synthetic.make_dataset(n_days=300, signal=0.0, seed=13)
    leak = df.with_columns(
        leak_future=pl.col("ret_15m").sign(), leak_available_at=pl.col("label_end_15m")
    )
    spec = pipeline.ModelSpec("canary", ("mkt", "leak"))
    folds = _folds(leak)
    with pytest.raises(LeakageError):
        pipeline.run_walk_forward(leak, spec, folds, horizon_min=15)
    forced = pipeline.run_walk_forward(
        leak, spec, folds, horizon_min=15, unsafe_skip_pit_check_for_canary=True
    )
    base = pipeline.run_walk_forward(df, M3, folds, horizon_min=15)
    assert forced["loss"].mean() < 0.6 * base["loss"].mean()  # type: ignore[operator]

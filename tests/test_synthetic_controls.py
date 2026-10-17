"""Controles sintéticos del pipeline completo (CLAUDE.md §8.5): positivo, negativo y canario.
Datos SINTÉTICOS (``models.synthetic``)."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from wti_news_alpha.errors import LeakageError
from wti_news_alpha.labels import splits
from wti_news_alpha.models import compare, pipeline, synthetic

M3 = pipeline.ModelSpec("M3", ("mkt", "wpsr", "cal"))
M4 = pipeline.ModelSpec("M4", ("mkt", "wpsr", "cal", "news"))
SEALED = datetime(2025, 7, 1, tzinfo=UTC)


def _folds(df: pl.DataFrame) -> list[splits.Fold]:
    start = df["decision_time"].min().replace(day=1, hour=0) + timedelta(days=150)  # type: ignore[union-attr]
    start = start.replace(day=1)
    end = df["decision_time"].max() + timedelta(days=1)  # type: ignore[operator]
    return splits.walk_forward(
        pipeline.purge_frame(df, 15),
        first_test=start,
        end=end,
        step="quarter",
        embargo=timedelta(days=1),
        sealed_start=SEALED,
        horizon="15m",
    )


def _run(
    df: pl.DataFrame, spec: pipeline.ModelSpec, allowed: frozenset[str] = synthetic.FEATURES
) -> pl.DataFrame:
    return pipeline.run_walk_forward(
        df, spec, _folds(df), horizon_min=15, allowed=allowed, sealed_start=SEALED
    ).predictions


def _compare(a: pl.DataFrame, b: pl.DataFrame) -> compare.Comparison:
    j = a.join(b, on="decision_time", suffix="_b", validate="1:1")
    blocks = j["decision_time"].dt.strftime("%Y-%m-%d").to_list()
    return compare.compare_losses(
        j["loss"].to_list(), j["loss_b"].to_list(), blocks, n_boot=500, seed=1
    )


def test_positive_control_m4_detects_planted_category_signal() -> None:
    df = synthetic.make_dataset(n_days=450, signal=0.5, seed=11)
    p3, p4 = _run(df, M3), _run(df, M4)
    res = _compare(p3, p4)
    assert res.mean_diff > 0
    assert res.p_dm < 0.01
    assert res.p_bootstrap < 0.01
    # M3 no ve la señal: no mejora a la climatología (M0).
    m0 = pipeline.climatology_loss(df, _folds(df), horizon_min=15)
    assert _compare(m0, p3).p_dm > 0.01


def test_negative_control_no_signal_no_improvement() -> None:
    df = synthetic.make_dataset(n_days=450, signal=0.0, seed=12)
    p3, p4 = _run(df, M3), _run(df, M4)
    res = _compare(p3, p4)
    assert res.p_dm > 0.01
    assert res.mean_diff < 0.005
    m0 = pipeline.climatology_loss(df, _folds(df), horizon_min=15)
    assert _compare(m0, p4).p_dm > 0.01  # ni M4 mejora a M0 sin señal


def test_leakage_canary_is_blocked_and_forced_is_near_perfect() -> None:
    df = synthetic.make_dataset(n_days=300, signal=0.0, seed=13)
    z = pl.col("ret_15m") / (pl.col("mkt_rv_60m") * 0.5)
    leak = df.with_columns(leak_future_z=z, leak_available_at=pl.col("label_end_15m"))
    spec = pipeline.ModelSpec("canary", ("mkt", "leak"))
    allowed = synthetic.FEATURES | {"leak_future_z"}
    with pytest.raises(LeakageError):
        _run(leak, spec, allowed)
    forced = pipeline.run_walk_forward(
        leak,
        spec,
        _folds(leak),
        horizon_min=15,
        allowed=allowed,
        sealed_start=SEALED,
        unsafe_skip_pit_check_for_canary=True,
    ).predictions
    assert forced["loss"].mean() < 0.1  # type: ignore[operator]


def test_label_columns_are_never_features_even_with_group_prefix() -> None:
    df = synthetic.make_dataset(n_days=200, signal=0.0, seed=14)
    sneaky = df.with_columns(mkt_ret_fwd=pl.col("ret_15m"))
    cols = pipeline.feature_columns(sneaky, M3, synthetic.FEATURES)
    assert "mkt_ret_fwd" not in cols

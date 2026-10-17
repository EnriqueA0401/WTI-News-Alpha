"""Línea de modelos: validaciones de entrada (FASE_06.md, revisión F6). Datos SINTÉTICOS."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from wti_news_alpha.errors import SchemaError, SealedTestError
from wti_news_alpha.labels import splits
from wti_news_alpha.models import pipeline, synthetic

SEALED = datetime(2025, 7, 1, tzinfo=UTC)
M3 = pipeline.ModelSpec("M3", ("mkt", "wpsr", "cal"))


def _df() -> pl.DataFrame:
    return synthetic.make_dataset(n_days=200, signal=0.0, seed=5)


def _folds(
    df: pl.DataFrame, first: datetime = datetime(2019, 4, 1, tzinfo=UTC), h: int = 15
) -> list[splits.Fold]:
    end = df["decision_time"].max() + timedelta(days=1)  # type: ignore[operator]
    return splits.walk_forward(
        pipeline.purge_frame(df, 15),
        first_test=first,
        end=end,
        step="quarter",
        embargo=timedelta(days=1),
        sealed_start=SEALED,
        horizon=f"{h}m",
    )


def _run(df: pl.DataFrame, folds: list[splits.Fold]) -> pipeline.WalkForwardResult:
    return pipeline.run_walk_forward(
        df, M3, folds, horizon_min=15, allowed=synthetic.FEATURES, sealed_start=SEALED
    )


def test_feature_and_availability_checks() -> None:
    df = _df()
    with pytest.raises(SchemaError, match="ninguna feature"):
        pipeline.feature_columns(df, pipeline.ModelSpec("x", ("cot",)), synthetic.FEATURES)
    with pytest.raises(SchemaError, match="sin columna de disponibilidad"):
        pipeline.check_features_pit(
            df.drop("wpsr_available_at"), pipeline.ModelSpec("x", ("wpsr",))
        )


def test_folds_must_match_frame_horizon_and_research_period() -> None:
    df = _df()
    folds = _folds(df)
    with pytest.raises(SchemaError, match="otro DataFrame"):
        _run(df.reverse(), folds)
    with pytest.raises(SchemaError, match="purgado para 60m"):
        _run(df, _folds(df, h=60))
    late = df.with_columns(decision_time=pl.col("decision_time") + timedelta(days=7 * 365))
    with pytest.raises(SealedTestError):
        _run(late, folds)


def test_folds_without_data_or_classes() -> None:
    df = _df()
    with pytest.raises(SchemaError, match="ningún fold"):
        _run(df, [])
    # El primer fold (train < 100 filas) se omite y se informa; el resto se ejecuta.
    res = _run(df, _folds(df, first=datetime(2019, 1, 1, tzinfo=UTC)))
    assert res.skipped == [0]
    assert res.predictions["fold"].min() > 0
    flat = df.with_columns(ret_15m=pl.lit(0.0))
    with pytest.raises(SchemaError, match="no válido"):
        _run(flat, _folds(flat))
    up = df.with_columns(ret_15m=pl.col("ret_15m").abs())
    with pytest.raises(SchemaError, match="3 clases"):
        _run(up, _folds(up))
    with pytest.raises(SchemaError, match="M0"):
        pipeline.climatology_loss(df, [], horizon_min=15)

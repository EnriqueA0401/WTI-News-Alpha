"""Línea de modelos: validaciones de entrada (FASE_06.md). Datos SINTÉTICOS."""

from datetime import timedelta

import polars as pl
import pytest

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.labels import splits
from wti_news_alpha.models import pipeline, synthetic


def _df() -> pl.DataFrame:
    return synthetic.make_dataset(n_days=200, signal=0.0, seed=5)


def _folds(df: pl.DataFrame, first_days: int = 120) -> list[splits.Fold]:
    start = df["decision_time"].min() + timedelta(days=first_days)  # type: ignore[operator]
    end = df["decision_time"].max() + timedelta(days=1)  # type: ignore[operator]
    return splits.walk_forward(
        pipeline.purge_frame(df, 15),
        first_test=start,
        end=end,
        step="quarter",
        embargo=timedelta(days=1),
    )


def test_feature_and_availability_checks() -> None:
    df = _df()
    with pytest.raises(SchemaError, match="ninguna feature"):
        pipeline.feature_columns(df, pipeline.ModelSpec("x", ("cot",)))
    with pytest.raises(SchemaError, match="sin columna de disponibilidad"):
        pipeline.check_features_pit(
            df.drop("wpsr_available_at"), pipeline.ModelSpec("x", ("wpsr",))
        )


def test_folds_without_data_or_classes() -> None:
    df = _df()
    spec = pipeline.ModelSpec("M3", ("mkt", "wpsr", "cal"))
    with pytest.raises(SchemaError, match="ningún fold"):
        pipeline.run_walk_forward(df, spec, [], horizon_min=15)
    # El primer fold (train < 100 filas) se omite; el resto se ejecuta.
    out = pipeline.run_walk_forward(df, spec, _folds(df, first_days=1), horizon_min=15)
    assert out["fold"].min() > 0
    flat = df.with_columns(ret_15m=pl.lit(0.0))
    with pytest.raises(SchemaError, match="no válido"):
        pipeline.run_walk_forward(flat, spec, _folds(flat), horizon_min=15)
    up = df.with_columns(ret_15m=pl.col("ret_15m").abs())
    with pytest.raises(SchemaError, match="3 clases"):
        pipeline.run_walk_forward(up, spec, _folds(up), horizon_min=15)

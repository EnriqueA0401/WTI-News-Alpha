"""M1 (logística regularizada): imputación y escalado ajustados solo con el train (FASE_07.md).
Casos a mano y datos SINTÉTICOS."""

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from wti_news_alpha.errors import ModelFitError, SchemaError
from wti_news_alpha.labels import splits
from wti_news_alpha.models import pipeline, synthetic

NAN = float("nan")
SEALED = datetime(2025, 7, 1, tzinfo=UTC)


def test_linear_design_hand_case() -> None:
    xtr = np.array([[1.0, NAN], [3.0, 2.0], [5.0, 4.0]])
    xte = np.array([[NAN, 10.0]])
    d = pipeline.linear_design(xtr, xte)
    assert (d.n_imputed_train, d.n_imputed_test) == (1, 1)
    # Columnas: col0, col1, faltante(col0), faltante(col1).
    # col0: mediana 3, media 3, desv. sqrt(8/3); col1 imputada [3, 2, 4]: media 3, desv. sqrt(2/3).
    assert d.test[0] == pytest.approx([0.0, 7 / math.sqrt(2 / 3), 1.0, -(1 / 3) / math.sqrt(2 / 9)])
    assert d.train[:, 0] == pytest.approx([-2 / math.sqrt(8 / 3), 0.0, 2 / math.sqrt(8 / 3)])
    assert d.train[:, 2] == pytest.approx([0.0, 0.0, 0.0])  # constante en train: solo centrada
    with pytest.raises(SchemaError, match="sin datos en el train"):
        pipeline.linear_design(np.array([[NAN], [NAN]]), np.array([[1.0]]))
    with pytest.raises(SchemaError, match="infinitos"):
        pipeline.linear_design(np.array([[1.0], [2.0]]), np.array([[math.inf]]))


def _folds(df: pl.DataFrame, h: int = 15) -> list[splits.Fold]:
    return splits.walk_forward(
        pipeline.purge_frame(df, h),
        first_test=datetime(2019, 4, 1, tzinfo=UTC),
        end=df["decision_time"].max() + timedelta(days=1),  # type: ignore[operator]
        step="quarter",
        embargo=timedelta(days=1),
        sealed_start=SEALED,
        horizon=f"{h}m",
    )


def test_logit_runs_in_walk_forward_and_nonconvergence_is_an_error() -> None:
    df = synthetic.make_dataset(n_days=200, signal=0.0, seed=5)
    m1 = pipeline.ModelSpec("M1", ("mkt",), learner="logit")
    kw = {"horizon_min": 15, "allowed": synthetic.FEATURES, "sealed_start": SEALED}
    res = pipeline.run_walk_forward(df, m1, _folds(df), **kw)  # type: ignore[arg-type]
    assert res.predictions.height > 0
    assert res.predictions["loss"].is_finite().all()
    probs = res.predictions.select(["p_down", "p_neutral", "p_up"]).sum_horizontal()
    assert probs.to_numpy() == pytest.approx(1.0)
    again = pipeline.run_walk_forward(df, m1, _folds(df), **kw)  # type: ignore[arg-type]
    assert again.predictions.equals(res.predictions)
    bad = pipeline.ModelSpec("M1", ("mkt",), learner="logit", params={"C": 1e6, "max_iter": 1})
    with pytest.raises(ModelFitError, match="no converge"):
        pipeline.run_walk_forward(df, bad, _folds(df), **kw)  # type: ignore[arg-type]

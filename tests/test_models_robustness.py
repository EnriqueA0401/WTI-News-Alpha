"""F8: subgrupos con BH, Brier, parte de SHAP y objetivo de magnitud. Casos a mano y datos
SINTÉTICOS."""

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from wti_news_alpha.errors import SchemaError, SealedTestError
from wti_news_alpha.labels import splits
from wti_news_alpha.models import magnitude, pipeline, robustness, synthetic

SEALED = datetime(2025, 7, 1, tzinfo=UTC)
T0 = datetime(2019, 1, 1, tzinfo=UTC)


def _j() -> pl.DataFrame:
    """60 días, 2 filas/día; en las filas 'news' B pierde 0,2 menos (con ruido diario)."""
    rows = []
    for day in range(60):
        for k in range(2):
            noise = 0.05 * ((day % 3) - 1)
            rows.append(
                {
                    "decision_time": T0 + timedelta(days=day, hours=k),
                    "news": k == 0,
                    "A": 1.0,
                    "B": 1.0 - (0.2 + noise if k == 0 else noise),
                }
            )
    return pl.DataFrame(rows).with_columns(pl.col("decision_time").dt.replace_time_zone("UTC"))


def test_subgroup_tests_with_bh_and_too_few_days() -> None:
    groups = {
        "news": pl.col("news"),
        "no_news": ~pl.col("news"),
        "first_10_days": pl.col("decision_time") < T0 + timedelta(days=10),
    }
    res = {
        r["subgroup"]: r
        for r in robustness.subgroup_tests(_j(), groups, "A", "B", n_boot=200, seed=1)
    }
    assert res["news"]["mean_diff_a_minus_b"] == pytest.approx(0.2)
    assert res["news"]["p_dm"] < 0.01
    assert res["no_news"]["mean_diff_a_minus_b"] == pytest.approx(0.0, abs=1e-12)
    assert res["first_10_days"]["status"].startswith("no evaluado")
    assert "q_bh" not in res["first_10_days"]
    assert res["news"]["q_bh"] == pytest.approx(min(1.0, 2 * res["news"]["p_dm"]))


def test_brier_hand_case() -> None:
    proba = np.array([[0.2, 0.5, 0.3], [1.0, 0.0, 0.0]])
    y = np.array([1, -1])
    # (0,04 + 0,25 + 0,49) = 0,78 ; 0
    assert robustness.brier_rows(proba, y) == pytest.approx([0.78, 0.0])
    with pytest.raises(SchemaError):
        robustness.brier_rows(proba, np.array([1]))


def test_shap_shares_hand_case() -> None:
    contribs = np.array([[[1.0, -3.0, 0.0, 9.0]], [[-1.0, 1.0, 4.0, 9.0]]])  # última: sesgo
    shares = robustness.shap_shares(contribs, ["mkt_a", "mkt_b", "news_c"])
    # medias |.|: 1, 2, 2 → total 5
    assert shares == pytest.approx({"mkt": 0.6, "news": 0.4})
    with pytest.raises(SchemaError, match="desalineadas"):
        robustness.shap_shares(contribs, ["mkt_a"])
    with pytest.raises(SchemaError, match="nulo"):
        robustness.shap_shares(np.zeros((1, 2)), ["mkt_a"])


def _folds(
    df: pl.DataFrame, first: datetime = datetime(2019, 4, 1, tzinfo=UTC)
) -> list[splits.Fold]:
    return splits.walk_forward(
        pipeline.purge_frame(df, 15),
        first_test=first,
        end=df["decision_time"].max() + timedelta(days=1),  # type: ignore[operator]
        step="quarter",
        embargo=timedelta(days=1),
        sealed_start=SEALED,
        horizon="15m",
    )


def test_magnitude_runs_and_validates() -> None:
    df = synthetic.make_dataset(n_days=200, signal=0.0, seed=5)
    spec = pipeline.ModelSpec("M3", ("mkt", "wpsr", "cal"))
    kw = {"horizon_min": 15, "allowed": synthetic.FEATURES, "sealed_start": SEALED}
    res = magnitude.run_magnitude(df, spec, _folds(df), **kw)  # type: ignore[arg-type]
    assert res["loss"].is_finite().all()
    assert (res["target_bps"] >= 0).all()
    exp = (res["pred_bps"] - res["target_bps"]) ** 2
    assert res["loss"].to_numpy() == pytest.approx(exp.to_numpy())
    again = magnitude.run_magnitude(df, spec, _folds(df), **kw)  # type: ignore[arg-type]
    assert again.equals(res)
    with pytest.raises(SchemaError, match="solo con XGBoost"):
        magnitude.run_magnitude(
            df,
            pipeline.ModelSpec("M1", ("mkt",), learner="logit"),
            _folds(df),
            **kw,  # type: ignore[arg-type]
        )
    early = magnitude.run_magnitude(df, spec, _folds(df, T0), **kw)  # type: ignore[arg-type]
    assert early["fold"].min() > 0  # el primer fold (train < 100 filas) se omite
    with pytest.raises(SchemaError, match="ningún fold"):
        magnitude.run_magnitude(df, spec, [], **kw)  # type: ignore[arg-type]
    late = df.with_columns(decision_time=pl.col("decision_time") + timedelta(days=7 * 365))
    with pytest.raises(SealedTestError):
        magnitude.run_magnitude(late, spec, _folds(df), **kw)  # type: ignore[arg-type]

"""Prueba principal de H1 (ADR-044): serie diaria, criterios por fold y veredicto.
Datos SINTÉTICOS calculados a mano (revisión F7)."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.models import compare, h1

T0 = datetime(2019, 1, 1, tzinfo=UTC)


def _frame(d4: list[float], *, rows_per_day: int = 2) -> pl.DataFrame:
    """40 días, 2 filas por día; folds 0 (días 0-19) y 1 (días 20-39). M3 pierde 1."""
    n = len(d4)
    times = [T0 + timedelta(days=i // rows_per_day, hours=i % rows_per_day) for i in range(n)]
    return pl.DataFrame(
        {
            "decision_time": pl.Series(times, dtype=pl.Datetime("us", "UTC")),
            "fold": [0 if (i // rows_per_day) < 20 else 1 for i in range(n)],
            "M3": [1.0] * n,
            "M4": [1.0 - x for x in d4],
        }
    )


def _daily(*per_fold: tuple[float, float]) -> pl.DataFrame:
    """20 días por fold con d diario alternando entre los dos valores (2 filas iguales por día)."""
    d = [pair[k % 2] for pair in per_fold for k in range(20)]
    return _frame([x for x in d for _ in range(2)])


def test_daily_diffs_sign_and_fold_integrity() -> None:
    j = _frame([0.1, 0.3] * 40)
    daily = h1.daily_diffs(j, "M3", "M4")
    assert daily.height == 40
    assert daily["d"].to_list() == pytest.approx([0.2] * 40)  # positivo: M4 pierde menos
    assert daily["n_rows"].to_list() == [2] * 40
    bad = j.with_columns(fold=pl.when(pl.int_range(pl.len()) == 1).then(9).otherwise("fold"))
    with pytest.raises(SchemaError, match="más de un fold"):
        h1.daily_diffs(bad, "M3", "M4")


def test_primary_summary_hand_case() -> None:
    # Fold 0: d diario 0,15/0,25 (media 0,2); fold 1: 0,05/0,15 (media 0,1).
    s = h1.primary_summary(_daily((0.15, 0.25), (0.05, 0.15)), "M3", "M4", n_boot=200, seed=1)
    assert s["mean_diff_a_minus_b"] == pytest.approx(0.15)
    assert s["d_by_fold"] == pytest.approx({"0": 0.2, "1": 0.1})
    assert s["d_by_fold_row_weighted"] == pytest.approx({"0": 0.2, "1": 0.1})
    assert s["share_folds_d_positive"] == 1.0
    assert s["leave_one_fold_out_mean_d"] == pytest.approx({"0": 0.1, "1": 0.2})


def test_verdict_planted_signal_and_no_signal() -> None:
    sig = h1.primary_summary(_daily((0.15, 0.25), (0.15, 0.25)), "M3", "M4", n_boot=200, seed=1)
    # Sin señal: d alterna de signo por día → media 0.
    flat = h1.primary_summary(_daily((0.1, -0.1), (0.1, -0.1)), "M3", "M4", n_boot=200, seed=1)
    v = h1.verdict({"h15": flat, "h60": sig})
    assert v["h60"]["pass"]
    assert not v["h15"]["pass"]
    assert not v["h15"]["criteria"]["2_mean_d_pos_and_ci_excludes_0"]
    assert v["H1_supported"]
    assert v["h15"]["holm_p_dm"] == compare.holm({"h15": flat["p_dm"], "h60": sig["p_dm"]})["h15"]
    assert not h1.verdict({"h15": flat})["H1_supported"]
    # Un fold negativo de dos (50 % < 60 %) y dejar fuera el positivo deja media < 0.
    mixed = h1.primary_summary(_daily((0.25, 0.35), (-0.05, -0.15)), "M3", "M4", n_boot=200, seed=1)
    c = h1.verdict({"h60": mixed})["h60"]["criteria"]
    assert not c["3_d_pos_in_60pct_folds"]
    assert not c["4_leave_one_fold_out_all_pos"]


def test_holm_and_compare_reject_non_finite() -> None:
    with pytest.raises(SchemaError, match=r"\[0, 1\]"):
        compare.holm({"h15": float("nan"), "h60": 0.01})
    with pytest.raises(SchemaError, match=r"\[0, 1\]"):
        compare.holm({"h15": -0.5})
    with pytest.raises(SchemaError, match="no finitas"):
        compare.compare_losses(
            [float("nan")] + [1.0] * 39,
            [1.0] * 40,
            [f"d{i:02d}" for i in range(40)],
            n_boot=10,
            seed=1,
        )


def test_check_prereg() -> None:
    doc = {
        "status": "approved",
        "models": {"M3": {"groups": ["mkt", "cal"]}, "M4": {"groups": ["mkt", "cal", "news"]}},
    }
    text = "semilla 20261017 · α = 0,05"  # noqa: RUF001
    models = {"M3": ("cal", "mkt"), "M4": ("mkt", "cal", "news")}
    h1.check_prereg(doc, text, required_text=("semilla 20261017",), models=models)
    with pytest.raises(SchemaError, match="no aparece"):
        h1.check_prereg(doc, text, required_text=("semilla 1",), models=models)
    with pytest.raises(SchemaError, match="grupos de M4"):
        h1.check_prereg(doc, text, required_text=(), models={"M4": ("mkt",)})

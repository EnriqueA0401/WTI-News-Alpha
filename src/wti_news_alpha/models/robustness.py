"""F8: robustez y análisis secundarios de H1 (CLAUDE.md §11.3; pre-registro, secondary).

- Subgrupos definidos solo con features PIT de la fila (o con el resultado ``y``, marcado
  como descriptivo). Cada uno compara A frente a B con ``compare.compare_losses`` en sus filas;
  con menos de ``compare.MIN_DAYS`` días se informa como no evaluado (no se fuerza).
- Corrección BH sobre todos los subgrupos evaluados (``eda.event_study.bh_qvalues``).
- ``brier_rows``: Brier multiclase por fila (métrica secundaria de calibración).
- ``shap_shares``: parte de la media de |SHAP| por grupo de features (asociación, no causa).
"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import polars as pl

from wti_news_alpha.eda.event_study import bh_qvalues
from wti_news_alpha.errors import SchemaError
from wti_news_alpha.models import compare


def subgroup_tests(  # noqa: PLR0913 - comparación A/B explícita
    j: pl.DataFrame, groups: Mapping[str, pl.Expr], a: str, b: str, *, n_boot: int, seed: int
) -> list[dict[str, Any]]:
    """Una fila por subgrupo: d medio diario, IC, p DM y q BH (o motivo si no se evalúa)."""
    out: list[dict[str, Any]] = []
    for name, expr in groups.items():
        sub = j.filter(expr)
        days = sub["decision_time"].dt.strftime("%Y-%m-%d").to_list()
        n_days = len(set(days))
        row: dict[str, Any] = {"subgroup": name, "n_rows": sub.height, "n_days": n_days}
        if n_days < compare.MIN_DAYS:
            row["status"] = f"no evaluado: {n_days} días < {compare.MIN_DAYS}"
        else:
            c = compare.compare_losses(
                sub[a].to_list(), sub[b].to_list(), days, n_boot=n_boot, seed=seed
            )
            row |= {
                "status": "ok",
                "mean_diff_a_minus_b": c.mean_diff,
                "ci95": [c.lo, c.hi],
                "p_dm": c.p_dm,
            }
        out.append(row)
    tested = [r for r in out if r["status"] == "ok"]
    for r, q in zip(tested, bh_qvalues([r["p_dm"] for r in tested]), strict=True):
        r["q_bh"] = q
    return out


def brier_rows(proba: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Brier multiclase por fila: suma de (p_k - 1[y = k])²; columnas en orden -1, 0, 1."""
    if proba.shape != (len(y), 3):
        raise SchemaError("probabilidades y etiquetas desalineadas (Brier)")
    onehot = np.zeros_like(proba)
    onehot[np.arange(len(y)), y.astype(int) + 1] = 1.0
    out: np.ndarray = ((proba - onehot) ** 2).sum(axis=1)
    return out


def shap_shares(contribs: np.ndarray, cols: Sequence[str]) -> dict[str, float]:
    """Parte de la media de |SHAP| por grupo (prefijo de columna); sin la columna de sesgo.

    ``contribs``: (filas, clases, columnas + 1) o (filas, columnas + 1), como devuelve
    ``Booster.predict(pred_contribs=True)``.
    """
    c = np.abs(contribs)
    if c.shape[-1] != len(cols) + 1:
        raise SchemaError("contribuciones SHAP desalineadas con las columnas")
    per_col = c[..., :-1].reshape(-1, len(cols)).mean(axis=0)
    total = float(per_col.sum())
    if total <= 0:
        raise SchemaError("SHAP total nulo")
    acc: dict[str, float] = {}
    for col, v in zip(cols, per_col, strict=True):
        g = col.split("_", 1)[0]
        acc[g] = acc.get(g, 0.0) + float(v) / total
    return dict(sorted(acc.items()))

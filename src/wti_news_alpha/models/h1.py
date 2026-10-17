"""Prueba principal de H1 (``preregistration/H1.yaml``, congelado en ADR-044).

- ``d = pérdida(A) - pérdida(B)`` por fila (A = M3, B = M4: positivo, M4 mejor). Su **media por
  día UTC** es la serie de la prueba (DM con HAC e IC por bootstrap de bloques móviles de días,
  ADR-043). Un día no puede pertenecer a dos folds de test (``SchemaError``).
- Criterios 3 y 4 del pre-registro sobre la serie diaria: d medio por fold (media de días) y
  media sin cada fold. Se informa además el d por fold ponderado por filas (revisión F7).
- ``verdict``: Holm sobre los p de DM de los horizontes principales; un horizonte pasa si
  cumple los 4 criterios; H1 se sostiene si pasa al menos uno.
- ``check_prereg``: los parámetros del script deben coincidir con el pre-registro (textos y
  grupos de modelos); si no, falla.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import polars as pl

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.models import compare

ALPHA = 0.05
FOLD_SHARE = 0.6


def daily_diffs(j: pl.DataFrame, a: str, b: str) -> pl.DataFrame:
    """``day``, ``d`` (media diaria de a - b), ``fold`` y ``n_rows``, ordenado por día."""
    daily = (
        j.with_columns(
            day=pl.col("decision_time").dt.strftime("%Y-%m-%d"), _d=pl.col(a) - pl.col(b)
        )
        .group_by("day")
        .agg(
            pl.col("_d").mean().alias("d"),
            pl.col("fold").first(),
            pl.col("fold").n_unique().alias("_nf"),
            pl.len().alias("n_rows"),
        )
        .sort("day")
    )
    if daily["_nf"].max() != 1:
        raise SchemaError("un día de test aparece en más de un fold")
    return daily.drop("_nf")


def primary_summary(j: pl.DataFrame, a: str, b: str, *, n_boot: int, seed: int) -> dict[str, Any]:
    days = j["decision_time"].dt.strftime("%Y-%m-%d").to_list()
    c = compare.compare_losses(j[a].to_list(), j[b].to_list(), days, n_boot=n_boot, seed=seed)
    daily = daily_diffs(j, a, b)
    dl, groups = daily["d"].to_list(), [str(f) for f in daily["fold"].to_list()]
    by_fold = compare.group_means(dl, groups)
    rows = j.group_by("fold").agg((pl.col(a) - pl.col(b)).mean().alias("d")).sort("fold")
    return {
        "mean_diff_a_minus_b": c.mean_diff,
        "ci95": [c.lo, c.hi],
        "p_dm": c.p_dm,
        "p_bootstrap": c.p_bootstrap,
        "dm_stat": c.dm_stat,
        "n_days": c.n_blocks,
        "n_obs": c.n_obs,
        "d_by_fold": by_fold,
        "d_by_fold_row_weighted": {str(f): v for f, v in rows.iter_rows()},
        "share_folds_d_positive": sum(v > 0 for v in by_fold.values()) / len(by_fold),
        "leave_one_fold_out_mean_d": compare.leave_one_group_out(dl, groups),
    }


def verdict(primary: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    holm_p = compare.holm({k: float(v["p_dm"]) for k, v in primary.items()})
    out: dict[str, Any] = {}
    for k, p in primary.items():
        crit = {
            "1_holm_p_dm_lt_0.05": holm_p[k] < ALPHA,
            "2_mean_d_pos_and_ci_excludes_0": p["mean_diff_a_minus_b"] > 0 and p["ci95"][0] > 0,
            "3_d_pos_in_60pct_folds": p["share_folds_d_positive"] >= FOLD_SHARE,
            "4_leave_one_fold_out_all_pos": all(
                v > 0 for v in p["leave_one_fold_out_mean_d"].values()
            ),
        }
        out[k] = {"holm_p_dm": holm_p[k], "criteria": crit, "pass": all(crit.values())}
    out["H1_supported"] = any(v["pass"] for v in out.values())
    return out


def check_prereg(
    doc: Mapping[str, Any],
    text: str,
    *,
    required_text: Sequence[str],
    models: Mapping[str, Sequence[str]],
) -> None:
    """Falla si un parámetro del script no aparece en el pre-registro o los grupos difieren."""
    for t in required_text:
        if t not in text:
            raise SchemaError(f"'{t}' no aparece en el pre-registro")
    for name, groups in models.items():
        pre = set(doc["models"][name]["groups"])
        if pre != set(groups):
            raise SchemaError(
                f"grupos de {name}: script {sorted(groups)} ≠ pre-registro {sorted(pre)}"
            )

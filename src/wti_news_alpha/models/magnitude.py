"""Objetivo secundario de H1: tamaño del movimiento (``preregistration/H1.yaml``, secondary).

- Objetivo: ``|ret_h|`` en puntos básicos (por 1e4, solo por estabilidad numérica); pérdida:
  error cuadrático por fila. Mismas filas que la dirección (``_z`` no nulo) y mismos folds.
- XGBoost regresión con los hiperparámetros fijos de la dirección (``XGB_PARAMS``); ajuste solo
  con el train de cada fold. Mismas validaciones que ``pipeline.run_walk_forward``.
"""

from datetime import datetime

import polars as pl
import xgboost as xgb

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.features import pit
from wti_news_alpha.labels import splits
from wti_news_alpha.models import pipeline

BPS = 1e4


def run_magnitude(  # noqa: PLR0913 - todo lo que define la corrida es explícito
    df: pl.DataFrame,
    spec: pipeline.ModelSpec,
    folds: list[splits.Fold],
    *,
    horizon_min: int,
    allowed: frozenset[str],
    sealed_start: datetime,
) -> pl.DataFrame:
    """``decision_time``, ``fold``, ``target_bps``, ``pred_bps`` y ``loss`` OOS por fila."""
    if spec.learner != "xgb":
        raise SchemaError(f"{spec.name}: magnitud solo con XGBoost")
    pipeline.validate_run(df, spec, folds, horizon_min=horizon_min, sealed_start=sealed_start)
    cols = pipeline.feature_columns(df, spec, allowed)
    d = pipeline.add_z(df, horizon_min).with_columns(_t=pl.col(f"ret_{horizon_min}m").abs() * BPS)
    out = []
    for f in folds:
        tr = d.filter(f.train & pl.col("_z").is_not_null())
        te = d.filter(f.test & pl.col("_z").is_not_null())
        if tr.height < pipeline.MIN_TRAIN or te.height == 0:
            continue
        model = xgb.XGBRegressor(objective="reg:squarederror", **spec.params)
        model.fit(tr.select(cols).to_numpy(), tr["_t"].to_numpy())
        pred = model.predict(te.select(cols).to_numpy())
        out.append(
            te.select(pit.DECISION, pl.col("_t").alias("target_bps")).with_columns(
                fold=pl.lit(f.fold_id), pred_bps=pl.Series(pred, dtype=pl.Float64)
            )
        )
    if not out:
        raise SchemaError(f"{spec.name}: ningún fold con datos (magnitud)")
    res = pl.concat(out).with_columns(loss=(pl.col("pred_bps") - pl.col("target_bps")) ** 2)
    return res.with_columns(model=pl.lit(spec.name))

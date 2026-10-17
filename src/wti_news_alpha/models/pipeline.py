"""Línea de modelos M3/M4 en walk-forward (Documento §9; ADR-040).

- **Objetivo principal:** dirección en 3 clases a horizonte h. ``z = ret_h / sigma_h``, con
  ``sigma_h = mkt_rv_60m * sqrt(h / 60)`` (volatilidad previa, PIT). El umbral τ se ajusta
  **solo con el train** de cada fold (``labels.fit_threshold``, 1/3 neutral).
- **Fugas:** antes de entrenar, ``pit.check_no_leakage`` sobre las columnas
  ``*_available_at`` de los grupos usados. Una feature sin su ``available_at`` no se acepta.
- **Features permitidas (revisión F6):** solo columnas de la capa de features publicada
  (``allowed``), nunca columnas de etiqueta aunque compartan prefijo.
- **Folds:** deben venir del mismo DataFrame (hash de ``decision_time``) y del mismo horizonte.
- **Sellado:** ninguna fila de ``df`` puede estar en el sellado (``SealedTestError``).
- **Modelo:** XGBoost multiclase con hiperparámetros fijos y semilla (sin búsqueda en esta
  fase). Las pérdidas OOS se guardan por fila para comparar modelos sobre las mismas filas.
  Los folds omitidos (train < ``MIN_TRAIN``) se devuelven en ``skipped``.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import polars as pl
import xgboost as xgb

from wti_news_alpha.errors import SchemaError, SealedTestError
from wti_news_alpha.features import pit
from wti_news_alpha.labels import labels, splits

CLASSES = (-1, 0, 1)
EPS = 1e-12
MIN_TRAIN = 100


@dataclass(frozen=True)
class ModelSpec:
    name: str
    groups: tuple[str, ...]  # prefijos de columnas: "mkt", "wpsr", "cot", "fred", "cal", "news"
    params: dict[str, Any] = field(
        default_factory=lambda: {
            # Regularizado (revisión F6: con 200 árboles de profundidad 3, M3 quedaba peor que
            # la climatología con ruido puro). Fijos antes de cualquier corrida real.
            "n_estimators": 150,
            "max_depth": 2,
            "learning_rate": 0.03,
            "min_child_weight": 100,
            "reg_lambda": 10.0,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "random_state": 7,
            "n_jobs": 4,
            "tree_method": "hist",
        }
    )


def feature_columns(df: pl.DataFrame, spec: ModelSpec, allowed: frozenset[str]) -> list[str]:
    """Columnas numéricas de los grupos del modelo, sin las de disponibilidad, y solo de las
    permitidas (columnas de la capa de features)."""
    cols = [
        c
        for c in df.columns
        if c in allowed
        and c.split("_", 1)[0] in spec.groups
        and not c.endswith(pit.SUFFIX)
        and df.schema[c].is_numeric()
    ]
    if not cols:
        raise SchemaError(f"{spec.name}: ninguna feature de los grupos {spec.groups}")
    return sorted(cols)


def check_features_pit(df: pl.DataFrame, spec: ModelSpec) -> None:
    """Cada grupo usado debe tener su ``<grupo>*_available_at`` y no puede haber fugas."""
    avail = [c for c in df.columns if c.endswith(pit.SUFFIX) and c.split("_", 1)[0] in spec.groups]
    groups_with = {c.split("_", 1)[0] for c in avail}
    missing = set(spec.groups) - groups_with - {"cal"}  # calendario: conocido de antemano
    if missing:
        raise SchemaError(f"{spec.name}: grupos sin columna de disponibilidad: {sorted(missing)}")
    pit.check_no_leakage(df.select(pit.DECISION, *avail))


def add_z(df: pl.DataFrame, horizon_min: int) -> pl.DataFrame:
    sigma = pl.col("mkt_rv_60m") * math.sqrt(horizon_min / 60)
    ret = pl.col(f"ret_{horizon_min}m")
    return df.with_columns(_z=pl.when(sigma > 0).then(ret / sigma))


def log_loss_rows(proba: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Log loss por fila; ``y`` en {-1, 0, 1}, columnas de ``proba`` en ese orden."""
    idx = y.astype(int) + 1
    p = np.clip(proba[np.arange(len(y)), idx], EPS, 1.0)
    out: np.ndarray = -np.log(p)
    return out


@dataclass(frozen=True)
class WalkForwardResult:
    predictions: pl.DataFrame
    skipped: list[int]


def run_walk_forward(  # noqa: PLR0913 - todo lo que define la corrida es explícito
    df: pl.DataFrame,
    spec: ModelSpec,
    folds: list[splits.Fold],
    *,
    horizon_min: int,
    allowed: frozenset[str],
    sealed_start: datetime,
    neutral_share: float = 1 / 3,
    unsafe_skip_pit_check_for_canary: bool = False,
) -> WalkForwardResult:
    """Predicciones OOS por fila de test: ``decision_time``, ``fold``, ``y``, ``p_*``, ``loss``.

    ``unsafe_skip_pit_check_for_canary`` solo existe para el canario de leakage de F6
    (CLAUDE.md §8.5): demuestra que, si se fuerza, la métrica sale casi perfecta.
    """
    n_sealed = df.filter(pl.col(pit.DECISION) >= sealed_start).height
    if n_sealed:
        raise SealedTestError(f"{n_sealed} filas en el periodo sellado (ADR-036)")
    fh = splits.frame_hash(df)
    for f in folds:
        if f.frame_hash != fh:
            raise SchemaError(f"fold {f.fold_id}: calculado sobre otro DataFrame u otro orden")
        if f.horizon != f"{horizon_min}m":
            raise SchemaError(f"fold {f.fold_id}: purgado para {f.horizon}, no {horizon_min}m")
    if not unsafe_skip_pit_check_for_canary:
        check_features_pit(df, spec)
    cols = feature_columns(df, spec, allowed)
    d = add_z(df, horizon_min)
    out, skipped = [], []
    for f in folds:
        tr = d.filter(f.train & pl.col("_z").is_not_null())
        te = d.filter(f.test & pl.col("_z").is_not_null())
        if tr.height < MIN_TRAIN or te.height == 0:
            skipped.append(f.fold_id)
            continue
        tau = labels.fit_threshold(tr["_z"], neutral_share=neutral_share)
        ytr = labels.three_class(tr["_z"], tau).to_numpy() + 1
        yte = labels.three_class(te["_z"], tau).to_numpy()
        if len(set(ytr.tolist())) < len(CLASSES):
            raise SchemaError(f"fold {f.fold_id}: el train no tiene las 3 clases")
        model = xgb.XGBClassifier(objective="multi:softprob", num_class=3, **spec.params)
        model.fit(tr.select(cols).to_numpy(), ytr)
        proba = model.predict_proba(te.select(cols).to_numpy())
        loss = log_loss_rows(proba, yte)
        out.append(
            te.select(pit.DECISION).with_columns(
                fold=pl.lit(f.fold_id),
                y=pl.Series(yte, dtype=pl.Int8),
                p_down=pl.Series(proba[:, 0]),
                p_neutral=pl.Series(proba[:, 1]),
                p_up=pl.Series(proba[:, 2]),
                loss=pl.Series(loss),
                tau=pl.lit(tau),
            )
        )
    if not out:
        raise SchemaError(f"{spec.name}: ningún fold con datos")
    return WalkForwardResult(pl.concat(out).with_columns(model=pl.lit(spec.name)), skipped)


def climatology_loss(
    df: pl.DataFrame, folds: list[splits.Fold], *, horizon_min: int
) -> pl.DataFrame:
    """M0: probabilidades = frecuencias de clase del train (sin features), log loss OOS."""
    d = add_z(df, horizon_min)
    out = []
    for f in folds:
        tr = d.filter(f.train & pl.col("_z").is_not_null())
        te = d.filter(f.test & pl.col("_z").is_not_null())
        if tr.height < MIN_TRAIN or te.height == 0:
            continue
        tau = labels.fit_threshold(tr["_z"], neutral_share=1 / 3)
        ytr = labels.three_class(tr["_z"], tau).to_numpy()
        yte = labels.three_class(te["_z"], tau).to_numpy()
        freq = np.array([(ytr == c).mean() for c in CLASSES])
        loss = log_loss_rows(np.tile(freq, (len(yte), 1)), yte)
        out.append(
            te.select(pit.DECISION).with_columns(fold=pl.lit(f.fold_id), loss=pl.Series(loss))
        )
    if not out:
        raise SchemaError("M0: ningún fold con datos")
    return pl.concat(out).with_columns(model=pl.lit("M0"))


def label_end(horizon_min: int) -> pl.Expr:
    """Fin del intervalo de la etiqueta del horizonte (columna de ``label-v1``)."""
    return pl.col(f"label_end_{horizon_min}m")


def purge_frame(df: pl.DataFrame, horizon_min: int) -> pl.DataFrame:
    """Columnas que necesita el splitter (``decision_time``, ``label_end``)."""
    return df.select(pit.DECISION, label_end(horizon_min).alias("label_end"))

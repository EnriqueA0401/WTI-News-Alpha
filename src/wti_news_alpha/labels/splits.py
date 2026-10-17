"""Splitter walk-forward con ventana creciente, purga y embargo (FASE_06.md, ADR-040).

Invariantes:
- Folds cronológicos: test = un trimestre (o semestre) ``[test_start, test_end)``, desde
  ``first_test`` hasta ``end``; train = todo lo anterior.
- **Purga y embargo:** una fila entra en el train solo si su ``decision_time`` y su
  ``label_end`` (fin del intervalo de la etiqueta) son anteriores a ``test_start - embargo``.
  Ningún intervalo de etiqueta del train toca el test (test de propiedad).
- Las filas son instantes de la rejilla, no artículos: la agrupación por evento de CLAUDE.md
  §7 se cubre con la purga y el embargo; los análisis por artículo o evento la aplican aparte.
- Los folds sin filas de test se omiten.
- **Sellado (revisión F6):** ``end`` no puede pasar de ``sealed_start`` y ninguna fila de
  ``df`` puede estar en el sellado (``SealedTestError``).
- Cada ``Fold`` guarda el hash de los ``decision_time`` y el horizonte de la purga: la línea
  de modelos comprueba que se usa con el mismo DataFrame y el mismo horizonte.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import polars as pl

from wti_news_alpha.errors import SchemaError, SealedTestError
from wti_news_alpha.features import pit
from wti_news_alpha.timeutils import require_utc_column

DECISION = pit.DECISION
MONTHS = {"quarter": 3, "half": 6}


@dataclass(frozen=True)
class Fold:
    fold_id: int
    test_start: datetime
    test_end: datetime
    train: pl.Series
    test: pl.Series
    frame_hash: str
    horizon: str


def frame_hash(df: pl.DataFrame) -> str:
    """Huella (sensible al orden) de ``decision_time``: las máscaras son posicionales."""
    us = df[DECISION].dt.epoch("us").to_numpy()
    return hashlib.sha256(us.tobytes()).hexdigest()


def _add_months(t: datetime, months: int) -> datetime:
    m = t.month - 1 + months
    return t.replace(year=t.year + m // 12, month=m % 12 + 1)


def walk_forward(  # noqa: PLR0913 - todo lo que define los folds es explícito
    df: pl.DataFrame,
    *,
    first_test: datetime,
    end: datetime,
    step: Literal["quarter", "half"],
    embargo: timedelta,
    sealed_start: datetime,
    horizon: str,
) -> list[Fold]:
    """Máscaras booleanas de train/test por fold, alineadas con las filas de ``df``."""
    if step not in MONTHS:
        raise SchemaError(f"step no válido: {step!r}")
    if end > sealed_start:
        raise SealedTestError(f"end {end} pasa del inicio del sellado {sealed_start} (ADR-036)")
    if first_test.day != 1 or first_test.time() != datetime.min.time():
        raise SchemaError("first_test debe ser el primer instante de un mes")
    for c in (DECISION, "label_end"):
        if c not in df.columns:
            raise SchemaError(f"falta la columna {c!r}")
        require_utc_column(df, c, allow_nulls=c == "label_end")
    if df.filter(pl.col("label_end") < pl.col(DECISION)).height:
        raise SchemaError("label_end anterior a decision_time")
    n_sealed = df.filter(pl.col(DECISION) >= sealed_start).height
    if n_sealed:
        raise SealedTestError(f"{n_sealed} filas en el periodo sellado (ADR-036)")
    fh = frame_hash(df)
    t, e = pl.col(DECISION), pl.col("label_end")
    folds: list[Fold] = []
    start = first_test
    while start < end:
        stop = min(_add_months(start, MONTHS[step]), end)
        cut = start - embargo
        test = df.select(((t >= start) & (t < stop)).alias("m"))["m"]
        # Sin label_end (etiqueta nula) la fila no se usa para entrenar ese horizonte, pero
        # la purga se decide igualmente por la cota del intervalo: aquí se exige no nula.
        train = df.select(((t < cut) & e.is_not_null() & (e < cut)).alias("m"))["m"]
        if test.any():
            folds.append(Fold(len(folds), start, stop, train, test, fh, horizon))
        start = stop
    return folds

"""Núcleo point-in-time de las features (docs/plans/FASE_04.md, CLAUDE.md §7).

Invariantes:
- Toda unión con una fuente es ``asof`` backward sobre ``available_at``: en
  ``decision_time`` t solo se ve la última fila con ``available_at ≤ t``. Una coincidencia
  exacta **se incluye** (el dato ya era visible en t).
- Cada componente deja su ``<prefijo>_available_at``. ``check_no_leakage`` exige
  ``max(*_available_at) ≤ decision_time`` en cada fila; si no, ``LeakageError``.
- Nada se rellena: un valor demasiado antiguo (``max_age``) queda nulo, y su
  ``available_at`` también.
- Todos los timestamps son ``Datetime`` en UTC.
"""

from datetime import timedelta

import polars as pl

from wti_news_alpha.errors import LeakageError, SchemaError
from wti_news_alpha.timeutils import require_utc_column

DECISION = "decision_time"
AVAILABLE = "available_at"
SUFFIX = "_available_at"


def asof_latest(
    grid: pl.DataFrame,
    source: pl.DataFrame,
    *,
    prefix: str,
    values: list[str],
    max_age: timedelta | None = None,
) -> pl.DataFrame:
    """Añade a ``grid`` el último valor de ``source`` visible en cada ``decision_time``.

    ``source`` necesita ``available_at`` único (sin empates: el orden sería arbitrario).
    Devuelve ``grid`` ordenado por ``decision_time`` con ``<prefix>_<valor>`` y
    ``<prefix>_available_at``; las mismas filas que ``grid``.
    """
    require_utc_column(grid, DECISION)
    if grid[DECISION].is_duplicated().any():
        raise SchemaError("decision_time duplicado en la rejilla")
    missing = {AVAILABLE, *values} - set(source.columns)
    if missing:
        raise SchemaError(f"faltan columnas en la fuente {prefix!r}: {sorted(missing)}")
    require_utc_column(source, AVAILABLE)
    if source[AVAILABLE].is_duplicated().any():
        raise SchemaError(f"available_at duplicado en la fuente {prefix!r}")
    at = f"{prefix}{SUFFIX}"
    right = (
        source.select(AVAILABLE, *values)
        .sort(AVAILABLE)
        .rename({v: f"{prefix}_{v}" for v in values})
        .with_columns(pl.col(AVAILABLE).alias(at))
    )
    out = grid.sort(DECISION).join_asof(
        right, left_on=DECISION, right_on=AVAILABLE, strategy="backward"
    )
    if AVAILABLE in out.columns and AVAILABLE not in grid.columns:
        out = out.drop(AVAILABLE)
    if out.height != grid.height:
        raise SchemaError(f"asof {prefix!r}: {grid.height} filas → {out.height}")
    if max_age is not None:
        stale = (pl.col(DECISION) - pl.col(at)) > max_age
        new = [f"{prefix}_{v}" for v in values] + [at]
        out = out.with_columns(pl.when(~stale).then(pl.col(c)).alias(c) for c in new)
    return out


def check_no_leakage(df: pl.DataFrame) -> None:
    """``LeakageError`` si algún ``*_available_at`` es posterior a ``decision_time``.

    Los nulos (componente ausente) se aceptan. Exige al menos una columna ``*_available_at``.
    """
    require_utc_column(df, DECISION)
    cols = [c for c in df.columns if c.endswith(SUFFIX)]
    if not cols:
        raise SchemaError("ninguna columna *_available_at que comprobar")
    bad = df.select(
        (pl.col(c) > pl.col(DECISION)).fill_null(False).sum().alias(c) for c in cols
    ).row(0, named=True)
    leaks = {c: n for c, n in bad.items() if n}
    if leaks:
        raise LeakageError(f"datos posteriores a decision_time: {leaks}")


def decision_grid(bars: pl.DataFrame, *, step: timedelta, max_bar_age: timedelta) -> pl.DataFrame:
    """Rejilla de ``decision_time`` cada ``step`` (PROTOCOL A4).

    Un instante t entra si la última barra completa (``available_at ≤ t``) tiene como mucho
    ``max_bar_age``: así no se decide con el mercado cerrado ni en la pausa diaria.
    ``step`` debe dividir la hora (alinea la rejilla con el reloj en UTC).
    """
    minutes = step / timedelta(minutes=1)
    if minutes < 1 or minutes != int(minutes) or 60 % int(minutes):
        raise SchemaError(f"step debe ser un divisor entero de 60 min, es {step}")
    require_utc_column(bars, AVAILABLE)
    if bars.height == 0:
        raise SchemaError("barras: conjunto vacío")
    ends = bars.select(AVAILABLE).unique().sort(AVAILABLE)
    every = f"{int(minutes)}m"
    grid = ends.select(
        pl.datetime_range(
            pl.col(AVAILABLE).min().dt.truncate(every),
            pl.col(AVAILABLE).max() + max_bar_age,
            interval=every,
        ).alias(DECISION)
    )
    j = grid.join_asof(
        ends.with_columns(_bar=pl.col(AVAILABLE)),
        left_on=DECISION,
        right_on=AVAILABLE,
        strategy="backward",
    )
    keep = pl.col("_bar").is_not_null() & ((pl.col(DECISION) - pl.col("_bar")) <= max_bar_age)
    return j.filter(keep).select(DECISION)

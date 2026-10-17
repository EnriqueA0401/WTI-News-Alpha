"""Features de fundamentales (WPSR), posicionamiento (COT) y macro (FRED/ALFRED).

FASE_04.md; Documento §7: "último release conocido, días desde publicación y cambios
solo tras release".

Invariantes:
- Cada fuente se convierte en un **flujo** ordenado por ``available_at`` con lo que se
  sabía en cada publicación, calculado solo con filas anteriores o simultáneas; después,
  ``pit.asof_latest``.
- WPSR: una fila por serie y semana (primera publicación). Si dos semanas salen a la
  vez (recuperación tras un cierre), vale la más reciente. El cambio semanal exige la
  semana anterior exacta; si falta, nulo.
- COT: neto de *managed money* = largos - cortos; el cambio exige el informe de 7 días antes.
- FRED/ALFRED: en t vale la última **observación** publicada, con su vintage vigente.
  Revisar una observación antigua no cambia "el último dato".
"""

from datetime import timedelta

import polars as pl

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.features import pit
from wti_news_alpha.timeutils import require_utc_column

AT = pit.AVAILABLE
DECISION = pit.DECISION
WEEK = timedelta(days=7)


def _require(df: pl.DataFrame, cols: set[str], what: str) -> None:
    missing = cols - set(df.columns)
    if missing:
        raise SchemaError(f"faltan columnas de {what}: {sorted(missing)}")
    require_utc_column(df, AT)


def _age_hours(prefix: str) -> pl.Expr:
    at = pl.col(f"{prefix}{pit.SUFFIX}")
    return ((pl.col(DECISION) - at).dt.total_microseconds() / 3.6e9).alias(f"{prefix}_age_h")


def _lag_days(prefix: str, date_col: str) -> pl.Expr:
    """Días entre la fecha de los datos y la fecha UTC de decisión (antigüedad del dato, no
    de la publicación: en las recuperaciones tras un cierre son semanas; hallazgo 4)."""
    lag = (pl.col(DECISION).dt.date() - pl.col(f"{prefix}_{date_col}")).dt.total_days()
    return lag.alias(f"{prefix}_data_lag_d")


def wpsr_stream(wpsr: pl.DataFrame, series: list[str]) -> pl.DataFrame:
    """Una fila por publicación: ``<serie>_value`` y ``<serie>_chg`` de la última semana."""
    _require(wpsr, {"series_id", "week_ending", "value", AT}, "WPSR")
    w = wpsr.filter(pl.col("series_id").is_in(series))
    if w.select("series_id", "week_ending").is_duplicated().any():
        raise SchemaError("WPSR: serie y semana duplicadas")
    prev = w.select(
        "series_id",
        (pl.col("week_ending") + WEEK).alias("week_ending"),
        pl.col("value").alias("_prev"),
        pl.col(AT).alias("_prev_at"),
    )
    w = w.join(prev, on=["series_id", "week_ending"], how="left", validate="1:1")
    # La semana anterior solo vale si ya se conocía al publicar esta (revisión F4, hallazgo 2).
    known = pl.col("_prev_at") <= pl.col(AT)
    w = w.with_columns(chg=pl.when(known).then(pl.col("value") - pl.col("_prev")))
    # "Última semana" = máximo acumulado por hora de publicación: una semana atrasada publicada
    # después no sustituye a una más reciente.
    w = w.sort("series_id", AT, "week_ending").with_columns(
        _latest=pl.col("week_ending").cum_max().over("series_id")
    )
    w = w.filter(pl.col("week_ending") == pl.col("_latest"))
    latest = w.group_by("series_id", AT, maintain_order=True).last()
    times = latest.group_by("series_id").agg(pl.col(AT).sort())
    if times[AT].n_unique() > 1:
        raise SchemaError("WPSR: las series no comparten horas de publicación")
    week = latest.group_by(AT).agg(pl.col("week_ending").max().alias("week_ending"))
    wide = latest.pivot(on="series_id", index=AT, values=["value", "chg"], sort_columns=True)
    wide = wide.join(week, on=AT, how="left", validate="1:1")
    rename = {f"{v}_{s}": f"{s}_{v}" for s in series for v in ("value", "chg")}
    wide = wide.rename({k: v for k, v in rename.items() if k in wide.columns})
    for s in series:  # series sin filas (muestra truncada): columnas nulas con su tipo
        for v in ("value", "chg"):
            if f"{s}_{v}" not in wide.columns:
                wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(f"{s}_{v}"))
    return wide.sort(AT)


def wpsr_features(
    grid: pl.DataFrame, wpsr: pl.DataFrame, series: list[str] | None = None
) -> pl.DataFrame:
    """``wpsr_<serie>_value``, ``wpsr_<serie>_chg``, ``wpsr_available_at`` y ``wpsr_age_h``."""
    ids = sorted(wpsr["series_id"].unique()) if series is None else series
    stream = wpsr_stream(wpsr, ids)
    values = [f"{s}_{v}" for s in ids for v in ("value", "chg")]
    out = pit.asof_latest(grid, stream, prefix="wpsr", values=[*values, "week_ending"])
    return out.with_columns(_age_hours("wpsr"), _lag_days("wpsr", "week_ending")).drop(
        "wpsr_week_ending"
    )


def cot_features(grid: pl.DataFrame, cot: pl.DataFrame) -> pl.DataFrame:
    """``cot_mm_net``, ``cot_mm_net_chg``, ``cot_mm_net_oi``, ``cot_available_at`` y
    ``cot_age_h``."""
    _require(cot, {"as_of_date", "mm_long", "mm_short", "open_interest", AT}, "COT")
    c = cot.sort("as_of_date").with_columns(mm_net=pl.col("mm_long") - pl.col("mm_short"))
    # Informe anterior de la secuencia: 6-8 días (los festivos desplazan el as-of; hallazgo 3).
    gap = pl.col("as_of_date") - pl.col("as_of_date").shift(1)
    weekly = (gap >= timedelta(days=6)) & (gap <= timedelta(days=8))
    later = pl.col(AT) >= pl.col(AT).shift(1)
    c = c.with_columns(
        mm_net_chg=pl.when(weekly & later).then(pl.col("mm_net") - pl.col("mm_net").shift(1)),
        # División fila a fila en Python (IEEE exacta): la de polars no es bit a bit estable
        # según el tamaño de la tabla, y rompería la reproducibilidad (614 filas: coste nulo).
        mm_net_oi=pl.struct("mm_net", "open_interest").map_elements(
            lambda r: r["mm_net"] / r["open_interest"], return_dtype=pl.Float64
        ),
    )
    values = ["mm_net", "mm_net_chg", "mm_net_oi", "as_of_date"]
    out = pit.asof_latest(grid, c, prefix="cot", values=values)
    return out.with_columns(_age_hours("cot"), _lag_days("cot", "as_of_date")).drop(
        "cot_as_of_date"
    )


def fred_stream(fred: pl.DataFrame, series_id: str) -> pl.DataFrame:
    """Flujo de "última observación publicada" de una serie, por ``available_at``."""
    # Las observaciones sin valor (festivos: "." en FRED) no son "el último dato".
    s = fred.filter((pl.col("series_id") == series_id) & pl.col("value").is_not_null())
    s = s.sort(AT, "obs_date")
    s = s.with_columns(_latest=pl.col("obs_date").cum_max())
    s = s.filter(pl.col("obs_date") == pl.col("_latest"))
    return s.group_by(AT, maintain_order=True).last().select(AT, "obs_date", "value")


def _check_no_null_revision_of_latest(fred: pl.DataFrame, series_id: str) -> None:
    """``SchemaError`` si una vintage borra (valor nulo) la observación que era la última con
    valor en ese momento: el flujo la seguiría dando por buena (revisión F4, hallazgo 7).
    En los datos reales solo se borran observaciones antiguas; si cambia, falla ruidosamente."""
    nulls = fred.filter((pl.col("series_id") == series_id) & pl.col("value").is_null())
    if nulls.height == 0:
        return
    stream = fred_stream(fred, series_id).rename({"obs_date": "_latest_obs"})
    probe = nulls.select(AT, "obs_date").sort(AT).join_asof(stream.select(AT, "_latest_obs"), on=AT)
    bad = probe.filter(pl.col("obs_date") == pl.col("_latest_obs")).height
    if bad:
        raise SchemaError(f"FRED {series_id}: {bad} vintages borran la última observación")


def fred_features(
    grid: pl.DataFrame, fred: pl.DataFrame, series: list[str] | None = None
) -> pl.DataFrame:
    """Por serie: ``fred_<s>_value``, ``fred_<s>_obs_lag_d`` (días de la observación a la
    fecha UTC de decisión) y ``fred_<s>_available_at``."""
    _require(fred, {"series_id", "obs_date", "value", AT}, "FRED")
    ids = sorted(fred["series_id"].unique()) if series is None else series
    for sid in ids:
        _check_no_null_revision_of_latest(fred, sid)
    out = grid.sort(DECISION)
    for sid in ids:
        p = f"fred_{sid}"
        out = pit.asof_latest(out, fred_stream(fred, sid), prefix=p, values=["obs_date", "value"])
        lag = (pl.col(DECISION).dt.date() - pl.col(f"{p}_obs_date")).dt.total_days()
        out = out.with_columns(lag.alias(f"{p}_obs_lag_d")).drop(f"{p}_obs_date")
    return out

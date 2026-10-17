"""Features de mercado desde barras de 1 min (FASE_04.md; Documento §7).

Entrada: barras con ``available_at`` (= fin de barra completa, UTC) y precios bid.

Invariantes:
- Por barra solo se usa el pasado: retornos respecto a la barra previa y ventanas por
  tiempo ``(t - w, t]`` hacia atrás.
- Un hueco > ``max_gap`` entre barras consecutivas es un **corte**: ningún retorno ni
  ventana lo cruza (queda nulo, no se rellena).
- En la rejilla, todo es ``asof`` backward con ``max_age`` (``pit.asof_latest``). El
  retorno a k min compara la última barra en t con la última barra en t - k, que debe
  tener como mucho ``max_gap`` de antigüedad y no estar antes de un corte.
- Unidades: retornos y rangos en log; volatilidad realizada = raíz de la suma de los
  retornos de 1 min al cuadrado; minutos como ``Float64``.
"""

from dataclasses import dataclass
from datetime import timedelta

import polars as pl

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.features import pit
from wti_news_alpha.timeutils import require_utc_column

AT = pit.AVAILABLE
DECISION = pit.DECISION
PRICE_COLUMNS = ("bid_high", "bid_low", "bid_close")
REQUIRED = (AT, *PRICE_COLUMNS, "tick_volume", "avg_spread")


@dataclass(frozen=True)
class MarketParams:
    """Parámetros de ``config/features.yaml`` para las features de mercado."""

    return_minutes: list[int]
    max_gap: timedelta
    max_bar_age: timedelta
    vol_window: timedelta


def _minutes(td: timedelta) -> str:
    return f"{int(td / timedelta(minutes=1))}m"


def bar_features(bars: pl.DataFrame, *, max_gap: timedelta, vol_window: timedelta) -> pl.DataFrame:
    """Columnas por barra calculadas solo con barras anteriores o la propia.

    ``_break_at``: ``available_at`` del último corte (la barra que sigue a un hueco)
    hasta esta barra, incluida.
    """
    missing = set(REQUIRED) - set(bars.columns)
    if missing:
        raise SchemaError(f"faltan columnas de barras: {sorted(missing)}")
    require_utc_column(bars, AT)
    if bars[AT].is_duplicated().any():
        raise SchemaError("barras con available_at duplicado")
    bad = bars.filter(pl.any_horizontal(pl.col(c) <= 0 for c in PRICE_COLUMNS)).height
    if bad:
        raise SchemaError(f"{bad} barras con precios no positivos")
    w = _minutes(vol_window)
    at = pl.col(AT)
    b = bars.sort(AT).with_columns(
        _brk=((at - at.shift(1)) > max_gap).fill_null(True),
        _r1=(pl.col("bid_close") / pl.col("bid_close").shift(1)).log(),
    )
    first = b[AT].min()
    b = b.with_columns(
        _break_at=pl.when("_brk").then(at).otherwise(pl.lit(first)).cum_max(),
        # Retorno de 1 min válido solo sin corte en esta barra.
        _r1=pl.when(~pl.col("_brk")).then(pl.col("_r1")),
    )
    window_clean = pl.col("_break_at") <= (at - vol_window)
    sq = pl.when(pl.col("_r1").is_not_null()).then(pl.col("_r1") ** 2).otherwise(0.0)
    return b.with_columns(
        _rv=pl.when(window_clean).then(sq.rolling_sum_by(AT, window_size=w).sqrt()),
        _range=pl.when(window_clean).then(
            (
                pl.col("bid_high").rolling_max_by(AT, window_size=w)
                / pl.col("bid_low").rolling_min_by(AT, window_size=w)
            ).log()
        ),
        _vol=pl.when(window_clean).then(pl.col("tick_volume").rolling_sum_by(AT, window_size=w)),
    )


def market_features(grid: pl.DataFrame, bars: pl.DataFrame, p: MarketParams) -> pl.DataFrame:
    """Añade ``mkt_*`` a la rejilla. Mismas filas que ``grid``, ordenada por ``decision_time``."""
    return_minutes, max_gap, max_bar_age, vol_window = (
        p.return_minutes,
        p.max_gap,
        p.max_bar_age,
        p.vol_window,
    )
    if not return_minutes:
        raise SchemaError("return_minutes vacío")
    b = bar_features(bars, max_gap=max_gap, vol_window=vol_window)
    w = _minutes(vol_window)
    now = pit.asof_latest(
        grid,
        b.rename({"bid_close": "close", "avg_spread": "spread"}),
        prefix="mkt",
        values=["close", "spread", "_break_at", "_rv", "_range", "_vol"],
        max_age=max_bar_age,
    )
    out = now.with_columns(
        mkt_minutes_since_bar=(
            pl.col(DECISION) - pl.col("mkt_available_at")
        ).dt.total_microseconds()
        / 60e6,
    ).rename(
        {
            "mkt__rv": f"mkt_rv_{w}",
            "mkt__range": f"mkt_range_{w}",
            "mkt__vol": f"mkt_tick_volume_{w}",
        }
    )
    for k in return_minutes:
        lag = timedelta(minutes=k)
        then = pit.asof_latest(
            out.select(pl.col(DECISION) - lag).with_columns(_t=pl.col(DECISION) + lag),
            b.select(AT, "bid_close"),
            prefix="then",
            values=["bid_close"],
            max_age=max_gap,
        )
        then = then.select(pl.col("_t").alias(DECISION), "then_bid_close", "then_available_at")
        out = out.join(then, on=DECISION, how="left", validate="1:1")
        # Sin corte entre ambas barras, y barras distintas: si no hubo barra nueva en
        # (t - k, t], el retorno es nulo, no 0 (revisión F4, hallazgo 8).
        ok = (pl.col("then_available_at") >= pl.col("mkt__break_at")) & (
            pl.col("then_available_at") < pl.col("mkt_available_at")
        )
        out = out.with_columns(
            pl.when(ok)
            .then((pl.col("mkt_close") / pl.col("then_bid_close")).log())
            .alias(f"mkt_ret_{k}m")
        ).drop("then_bid_close", "then_available_at")
    # Minutos desde el último corte: aproxima el tiempo desde la reapertura de la sesión.
    since = (pl.col(DECISION) - pl.col("mkt__break_at")).dt.total_microseconds() / 60e6
    return out.with_columns(mkt_minutes_since_break=since).drop("mkt__break_at")

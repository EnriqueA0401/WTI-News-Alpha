"""Modelo de costes (ADR-041; CLAUDE.md §7).

Precios bid de entrada y salida, con el spread medido de esas barras:
- largo: compra a ``bid_e + m·(spread_e + slip)`` y vende a ``bid_x - m·slip``;
- corto: vende a ``bid_e - m·slip`` y recompra a ``bid_x + m·(spread_x + slip)``;
con ``slip`` = ticks de deslizamiento x tamaño del tick y ``m`` el multiplicador de
sensibilidad (1x, 2x, 3x). Retornos en log. La comisión del caso base es 0 (CFD; ADR-041):
una comisión distinta de 0 en USD por contrato necesita el tamaño del contrato, que aún no
se modela, así que falla en lugar de ignorarse.
"""

from dataclasses import dataclass

import polars as pl

from wti_news_alpha.errors import SchemaError

REQUIRED = ("bid_entry", "spread_entry", "bid_exit", "spread_exit")


@dataclass(frozen=True)
class CostParams:
    tick: float
    slippage_ticks: float
    commission_usd: float


def net_returns(df: pl.DataFrame, p: CostParams, *, multiplier: float) -> pl.DataFrame:
    """Añade ``gross``, ``net_long`` y ``net_short`` (log)."""
    missing = set(REQUIRED) - set(df.columns)
    if missing:
        raise SchemaError(f"faltan columnas de costes: {sorted(missing)}")
    if multiplier <= 0:
        raise SchemaError(f"multiplicador de costes no válido: {multiplier}")
    if p.commission_usd != 0:
        raise SchemaError("comisión por contrato no modelada (ADR-041: caso base 0)")
    if df.filter((pl.col("spread_entry") < 0) | (pl.col("spread_exit") < 0)).height:
        raise SchemaError("spread negativo")
    slip = multiplier * p.slippage_ticks * p.tick
    be, bx = pl.col("bid_entry"), pl.col("bid_exit")
    se, sx = multiplier * pl.col("spread_entry"), multiplier * pl.col("spread_exit")
    return df.with_columns(
        gross=(bx / be).log(),
        net_long=((bx - slip) / (be + se + slip)).log(),
        net_short=((be - slip) / (bx + sx + slip)).log(),
    )

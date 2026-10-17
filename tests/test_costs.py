"""Modelo de costes (ADR-041): casos a mano y sensibilidad."""

import math

import polars as pl
import pytest

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.labels import costs

C = costs.CostParams(tick=0.01, slippage_ticks=1.0, commission_usd=0.0)


def _df() -> pl.DataFrame:
    return pl.DataFrame(
        {"bid_entry": [70.00], "spread_entry": [0.04], "bid_exit": [70.50], "spread_exit": [0.05]}
    )


def test_net_returns_hand_case() -> None:
    out = costs.net_returns(_df(), C, multiplier=1.0).row(0, named=True)
    # Largo: compra a 70,00 + 0,04 + 0,01 = 70,05; vende a 70,50 - 0,01 = 70,49.
    assert out["net_long"] == pytest.approx(math.log(70.49 / 70.05))
    # Corto: vende a 70,00 - 0,01 = 69,99; recompra a 70,50 + 0,05 + 0,01 = 70,56.
    assert out["net_short"] == pytest.approx(math.log(69.99 / 70.56))
    assert out["gross"] == pytest.approx(math.log(70.50 / 70.00))


def test_sensitivity_scales_spread_and_slippage() -> None:
    out = costs.net_returns(_df(), C, multiplier=3.0).row(0, named=True)
    assert out["net_long"] == pytest.approx(math.log((70.50 - 0.03) / (70.00 + 0.12 + 0.03)))


def test_validation() -> None:
    with pytest.raises(SchemaError, match="faltan"):
        costs.net_returns(_df().drop("spread_exit"), C, multiplier=1.0)
    with pytest.raises(SchemaError, match="multiplicador"):
        costs.net_returns(_df(), C, multiplier=0.0)
    with pytest.raises(SchemaError, match="spread"):
        costs.net_returns(_df().with_columns(spread_entry=pl.lit(-0.01)), C, multiplier=1.0)
    with pytest.raises(SchemaError, match="comisión"):
        costs.net_returns(_df(), costs.CostParams(0.01, 1.0, 2.5), multiplier=1.0)

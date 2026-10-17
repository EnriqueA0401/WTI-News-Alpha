"""Features de mercado (FASE_04.md): casos a mano, huecos y truncamiento."""

import math
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.features import market, pit

T0 = datetime(2024, 3, 8, 15, 0, tzinfo=UTC)
M = timedelta(minutes=1)
GAP = timedelta(minutes=5)
AGE = timedelta(minutes=5)


def _bars(minutes: list[int], closes: list[float]) -> pl.DataFrame:
    """Barra que cierra en T0 + m minutos (``available_at`` = fin de barra)."""
    ends = [T0 + m * M for m in minutes]
    return pl.DataFrame(
        {
            "available_at": ends,
            "bid_open": closes,
            "bid_high": [c + 0.5 for c in closes],
            "bid_low": [c - 0.5 for c in closes],
            "bid_close": closes,
            "tick_volume": [10] * len(closes),
            "avg_spread": [0.03] * len(closes),
        },
        schema_overrides={"available_at": pl.Datetime("us", "UTC")},
    )


def _grid(minutes: list[int]) -> pl.DataFrame:
    return pl.DataFrame(
        {"decision_time": [T0 + m * M for m in minutes]},
        schema={"decision_time": pl.Datetime("us", "UTC")},
    )


def _run(bars: pl.DataFrame, grid: pl.DataFrame, rets: list[int] = [1, 5]) -> pl.DataFrame:  # noqa: B006
    p = market.MarketParams(rets, GAP, AGE, timedelta(minutes=3))
    return market.market_features(grid, bars, p)


def test_returns_hand_case() -> None:
    closes = [100.0, 101.0, 100.0, 102.0, 103.0, 104.0, 105.0]  # barras que cierran en 1..7
    out = _run(_bars(list(range(1, 8)), closes), _grid([7]))
    row = out.row(0, named=True)
    assert row["mkt_close"] == 105.0
    assert row["mkt_ret_1m"] == pytest.approx(math.log(105 / 104))
    assert row["mkt_ret_5m"] == pytest.approx(
        math.log(105 / 101)
    )  # cierre de la barra de las 15:02
    assert row["mkt_minutes_since_bar"] == 0.0
    assert row["mkt_minutes_since_break"] == 6.0  # primera barra (corte inicial) a las 15:01
    # Ventana de 3 min: barras 5, 6 y 7 → retornos 1 min de 4→5, 5→6, 6→7.
    r = [math.log(104 / 103), math.log(105 / 104)]
    rv = math.sqrt(math.log(103 / 102) ** 2 + r[0] ** 2 + r[1] ** 2)
    assert row["mkt_rv_3m"] == pytest.approx(rv)
    assert row["mkt_range_3m"] == pytest.approx(math.log(105.5 / 102.5))
    assert row["mkt_tick_volume_3m"] == 30
    assert row["mkt_available_at"] == T0 + 7 * M
    pit.check_no_leakage(out)


def test_decision_between_bars_uses_last_complete_bar() -> None:
    out = _run(_bars([1, 2, 3], [100.0, 101.0, 102.0]), _grid([3, 4]))
    assert out["mkt_close"].to_list() == [102.0, 102.0]
    assert out["mkt_minutes_since_bar"].to_list() == [0.0, 1.0]


def test_gap_breaks_returns_and_windows() -> None:
    # Hueco de 10 min entre la barra 2 y la 12 (> 5 min): nada cruza el hueco.
    bars = _bars([1, 2, 12, 13, 14, 15], [100.0, 101.0, 110.0, 111.0, 112.0, 113.0])
    row = _run(bars, _grid([15])).row(0, named=True)
    assert row["mkt_ret_1m"] == pytest.approx(math.log(113 / 112))
    assert row["mkt_ret_5m"] is None  # 15:10 cae en el hueco
    assert row["mkt_rv_3m"] is not None  # 13→14→15 no cruza
    assert row["mkt_minutes_since_break"] == 3.0  # la barra de las 15:12 sigue al hueco
    row2 = _run(bars, _grid([13])).row(0, named=True)
    assert row2["mkt_ret_1m"] == pytest.approx(math.log(111 / 110))
    assert row2["mkt_rv_3m"] is None  # la ventana (15:10, 15:13] empieza en el hueco
    assert row2["mkt_range_3m"] is None


def test_small_missing_minutes_do_not_break() -> None:
    # Falta la barra 3 (hueco de 2 min ≤ 5): el retorno a 5 min usa la última barra ≤ 15:02.
    bars = _bars([1, 2, 4, 5, 6, 7], [100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    row = _run(bars, _grid([7])).row(0, named=True)
    assert row["mkt_ret_5m"] == pytest.approx(math.log(105 / 101))


def test_stale_market_is_null() -> None:
    out = _run(_bars([1, 2], [100.0, 101.0]), _grid([2, 7, 8]))
    assert out["mkt_close"].to_list() == [101.0, 101.0, None]
    assert out["mkt_available_at"].to_list() == [T0 + 2 * M, T0 + 2 * M, None]


def test_input_validation() -> None:
    bars = _bars([1, 2], [100.0, 101.0])
    with pytest.raises(SchemaError, match="faltan"):
        _run(bars.drop("bid_close"), _grid([2]))
    with pytest.raises(SchemaError, match="no positivos"):
        _run(bars.with_columns(bid_close=pl.lit(0.0)), _grid([2]))
    with pytest.raises(SchemaError, match="return_minutes"):
        _run(bars, _grid([2]), rets=[])


@settings(max_examples=40, deadline=None)
@given(
    st.lists(st.integers(1, 300), min_size=2, max_size=80, unique=True),
    st.integers(1, 300),
)
def test_property_truncation(minutes: list[int], cut: int) -> None:
    minutes = sorted(minutes)
    closes = [100.0 + (m % 7) for m in minutes]
    bars = _bars(minutes, closes)
    grid = _grid(list(range(0, 305, 5)))
    t_cut = T0 + cut * M
    full = _run(bars, grid)
    trunc = _run(bars.filter(pl.col("available_at") <= t_cut), grid)
    keep = pl.col("decision_time") <= t_cut
    assert full.filter(keep).equals(trunc.filter(keep))
    pit.check_no_leakage(full)


def test_no_new_bar_gives_null_return_not_zero() -> None:
    """Revisión F4 (hallazgo 8): sin barra nueva en (t-1, t], el retorno de 1 min es nulo."""
    out = _run(_bars([1, 2, 3], [100.0, 101.0, 102.0]), _grid([5]))
    assert out["mkt_ret_1m"].to_list() == [None]
    assert out["mkt_ret_5m"].to_list() == [None]  # 15:00: sin barra en los 5 min previos

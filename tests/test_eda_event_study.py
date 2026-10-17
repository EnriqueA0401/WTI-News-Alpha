"""Estudio de eventos (FASE_05.md): casos a mano, huecos, fin de sesión con DST, MFE/MAE,
bootstrap por bloques y guardia del periodo sellado. Datos SINTÉTICOS."""

import math
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wti_news_alpha.eda import event_study as es
from wti_news_alpha.errors import SchemaError, SealedTestError

T0 = datetime(2024, 3, 6, 15, 0, tzinfo=UTC)  # miércoles 10:00 ET (EST)
M = timedelta(minutes=1)
P = es.StudyParams(
    latency=timedelta(minutes=1),
    max_gap=timedelta(minutes=5),
    horizons_min=[5, 15],
    sessions=[1],
    path_minutes=15,
)


def _bars(minutes: list[int], closes: list[float] | None = None) -> pl.DataFrame:
    closes = closes or [100.0 + 0.1 * m for m in minutes]
    return pl.DataFrame(
        {
            "available_at": [T0 + m * M for m in minutes],
            "bid_high": [c + 0.05 for c in closes],
            "bid_low": [c - 0.05 for c in closes],
            "bid_close": closes,
            "tick_volume": [1] * len(minutes),
            "avg_spread": [0.03] * len(minutes),
        },
        schema_overrides={"available_at": pl.Datetime("us", "UTC")},
    )


def _events(minutes: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {"event_id": [f"e{i}" for i in range(len(minutes))], "t0": [T0 + m * M for m in minutes]},
        schema_overrides={"t0": pl.Datetime("us", "UTC")},
    )


def test_entry_after_latency_and_forward_returns() -> None:
    bars = _bars(list(range(1, 40)))
    out = es.event_returns(_events([0.5]), bars, P).row(0, named=True)
    # t0 + 1 min = 15:01:30 → primera barra completa disponible: la que cierra a las 15:02.
    assert out["entry_at"] == T0 + 2 * M
    assert out["entry_close"] == pytest.approx(100.2)
    assert out["ret_5m"] == pytest.approx(math.log(100.7 / 100.2))
    assert out["ret_15m"] == pytest.approx(math.log(101.7 / 100.2))
    # Trayectoria a 15 min: máximo high y mínimo low en (entrada, entrada + 15].
    assert out["mfe_up_15m"] == pytest.approx(math.log(101.75 / 100.2))
    assert out["mae_dn_15m"] == pytest.approx(math.log(100.25 / 100.2))


def test_gap_and_closed_market_give_null() -> None:
    bars = _bars([1, 2, 3, 4, 5, 6, 20, 21, 22, 23, 24, 25, 26])  # hueco de 14 min tras 15:06
    row = es.event_returns(_events([0.0]), bars, P).row(0, named=True)
    assert row["ret_5m"] == pytest.approx(math.log(100.6 / 100.1))
    assert row["ret_15m"] is None  # la ventana cruza el hueco
    assert row["mfe_up_15m"] is None
    late = es.event_returns(_events([8.0]), bars, P).row(0, named=True)
    assert late["entry_at"] is None  # 15:09 + 1: la siguiente barra llega a las 15:20 (> 5 min)
    assert late["ret_5m"] is None


def test_session_end_and_next_sessions_across_dst() -> None:
    # Barras el miércoles 6-mar de 15:01 a 15:39 UTC y en el cierre de las 17:00 EST (22:00 UTC);
    # el jueves, solo la barra del cierre; el viernes, ninguna.
    mins = [*range(1, 40), 7 * 60 - 1, 7 * 60]
    thu = int((datetime(2024, 3, 7, 22, 0, tzinfo=UTC) - T0) / M)
    mins += [thu]
    bars = _bars(mins)
    p = es.StudyParams(P.latency, P.max_gap, [5], [1, 2], 15)
    row = es.event_returns(_events([0.0]), bars, p).row(0, named=True)
    assert row["session_close_at"] == datetime(2024, 3, 6, 22, 0, tzinfo=UTC)
    entry = 100.1  # t0 + 1 min = 15:01 → barra que cierra a las 15:01
    assert row["ret_session_end"] is None  # hay huecos > 5 min dentro de la sesión
    assert row["ret_1s"] == pytest.approx(math.log((100.0 + 0.1 * thu) / entry))
    assert row["ret_2s"] is None  # viernes sin barras


def test_session_end_without_gaps() -> None:
    close = int((datetime(2024, 3, 6, 22, 0, tzinfo=UTC) - T0) / M)
    bars = _bars(list(range(1, close + 1)))
    row = es.event_returns(_events([0.0]), bars, P).row(0, named=True)
    assert row["ret_session_end"] == pytest.approx(math.log((100.0 + 0.1 * close) / 100.1))


def test_session_close_rolls_weekends_and_evening() -> None:
    t = pl.DataFrame(
        {
            "entry_at": [
                datetime(2024, 3, 8, 21, 0, tzinfo=UTC),  # vie 16:00 EST → vie 17:00
                datetime(2024, 3, 8, 23, 30, tzinfo=UTC),  # vie 18:30 EST → lun 17:00 EDT
                datetime(2024, 3, 10, 23, 0, tzinfo=UTC),  # dom 19:00 EDT → lun 17:00 EDT
            ]
        },
        schema={"entry_at": pl.Datetime("us", "UTC")},
    )
    out = t.select(
        es.session_close(pl.col("entry_at"), 0).alias("k0"),
        es.session_close(pl.col("entry_at"), 1).alias("k1"),
    )
    assert out.to_series(0).to_list() == [
        datetime(2024, 3, 8, 22, 0, tzinfo=UTC),
        datetime(2024, 3, 11, 21, 0, tzinfo=UTC),
        datetime(2024, 3, 11, 21, 0, tzinfo=UTC),
    ]
    assert out.to_series(1).to_list()[0] == datetime(2024, 3, 11, 21, 0, tzinfo=UTC)


def test_block_bootstrap_is_deterministic_and_respects_blocks() -> None:
    values = [1.0, 1.0, 1.0, 5.0]
    blocks = ["d1", "d1", "d1", "d2"]
    a = es.block_bootstrap_ci(values, blocks, n_boot=500, seed=7)
    b = es.block_bootstrap_ci(values, blocks, n_boot=500, seed=7)
    assert a == b
    assert a.n_blocks == 2
    assert a.n == 4
    assert a.point == pytest.approx(1.0)  # mediana
    assert 1.0 <= a.lo <= a.hi <= 5.0
    with pytest.raises(SchemaError):
        es.block_bootstrap_ci([], [], n_boot=10, seed=1)


def test_sealed_guard() -> None:
    sealed = datetime(2025, 7, 1, tzinfo=UTC)
    ok = _events([0.0]).with_columns(window_end=pl.col("t0") + timedelta(days=5))
    es.check_research_only(ok, sealed_start=sealed)
    bad = ok.with_columns(window_end=pl.lit(sealed).dt.replace_time_zone("UTC"))
    with pytest.raises(SealedTestError):
        es.check_research_only(bad, sealed_start=sealed)


@settings(max_examples=30, deadline=None)
@given(st.lists(st.integers(1, 120), min_size=5, max_size=100, unique=True), st.integers(0, 60))
def test_property_future_beyond_window_is_irrelevant(minutes: list[int], t0: int) -> None:
    """El resultado de un evento no depende de las barras posteriores a su ventana."""
    bars = _bars(sorted(minutes))
    ev = _events([float(t0)])
    p = es.StudyParams(P.latency, P.max_gap, [5, 15], [], 15)
    full = es.event_returns(ev, bars, p)
    horizon_end = T0 + (t0 + 1 + 5 + 15 + 15) * M  # latencia + espera máx. + horizonte máx.
    trunc = es.event_returns(ev, bars.filter(pl.col("available_at") <= horizon_end), p)
    assert full.equals(trunc)


def test_cell_percentile_mid_rank() -> None:
    pool = [1.0, 2.0, 2.0, 3.0]
    assert es.cell_percentile(0.5, pool) == 0.0
    assert es.cell_percentile(2.0, pool) == 0.5  # empates a la mitad
    assert es.cell_percentile(9.0, pool) == 1.0
    with pytest.raises(SchemaError):
        es.cell_percentile(1.0, [])


def test_percentile_test_null_and_effect() -> None:
    rng = __import__("random").Random(3)
    null = [rng.random() for _ in range(400)]
    days = [f"d{i // 4}" for i in range(400)]
    t0 = es.percentile_test(null, days, n_boot=400, seed=1)
    assert t0.n_blocks == 100
    assert t0.p_greater > 0.05
    shifted = [min(1.0, x + 0.1) for x in null]
    t1 = es.percentile_test(shifted, days, n_boot=400, seed=1)
    assert t1.p_greater < 0.01
    assert t1.lo > 0.5
    assert es.percentile_test(shifted, days, n_boot=400, seed=1) == t1  # determinista
    with pytest.raises(SchemaError):
        es.percentile_test([], [], n_boot=10, seed=1)


def test_bh_qvalues_hand_case() -> None:
    assert es.bh_qvalues([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.02, 0.04, 0.04, 0.02])

"""Etiquetas (FASE_06.md, ADR-037/040): casos a mano, intervalo de purga, sellado y umbral
de 3 clases ajustado solo en train. Datos SINTÉTICOS."""

import math
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from wti_news_alpha.errors import SchemaError, SealedTestError
from wti_news_alpha.labels import labels as lb

T0 = datetime(2024, 3, 6, 15, 0, tzinfo=UTC)  # miércoles 10:00 EST
M = timedelta(minutes=1)
SEALED = datetime(2025, 7, 1, tzinfo=UTC)
P = lb.LabelParams(
    latency=M,
    max_gap=timedelta(minutes=5),
    horizons_min=[15, 60],
    sessions=[1],
    sealed_start=SEALED,
)


def _bars(minutes: list[int]) -> pl.DataFrame:
    closes = [100.0 + 0.1 * m for m in minutes]
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


def _grid(minutes: list[int]) -> pl.DataFrame:
    return pl.DataFrame(
        {"decision_time": [T0 + m * M for m in minutes]},
        schema={"decision_time": pl.Datetime("us", "UTC")},
    )


def test_labels_hand_case_and_purge_interval() -> None:
    out = lb.build_labels(_grid([0, 5]), _bars(list(range(1, 90))), P)
    row = out.row(0, named=True)
    assert row["entry_at"] == T0 + M
    assert row["ret_15m"] == pytest.approx(math.log(101.6 / 100.1))
    assert row["ret_60m"] == pytest.approx(math.log(106.1 / 100.1))
    # Intervalo de la etiqueta (para purgar): de decision_time a la última salida posible.
    assert row["label_end_15m"] == T0 + 16 * M
    assert row["label_end_60m"] == T0 + 61 * M
    assert row["label_end_1s"] == datetime(2024, 3, 7, 22, 0, tzinfo=UTC)
    assert row["ret_1s"] is None  # no hay barra del cierre del jueves
    assert out["decision_time"].to_list() == [T0, T0 + 5 * M]


def test_labels_never_touch_sealed_period() -> None:
    late = datetime(2025, 6, 30, 20, 0, tzinfo=UTC)
    grid = pl.DataFrame(
        {"decision_time": [late]}, schema={"decision_time": pl.Datetime("us", "UTC")}
    )
    bars = pl.DataFrame(
        {
            "available_at": [late + timedelta(minutes=m) for m in range(1, 80)],
            "bid_high": [70.1] * 79,
            "bid_low": [69.9] * 79,
            "bid_close": [70.0] * 79,
            "tick_volume": [1] * 79,
            "avg_spread": [0.03] * 79,
        },
        schema_overrides={"available_at": pl.Datetime("us", "UTC")},
    )
    out = lb.build_labels(grid, bars, P)
    assert out["ret_15m"].to_list() == [0.0]
    assert out["ret_1s"].to_list() == [None]  # el cierre del 1-jul ya es sellado: se anula
    with pytest.raises(SealedTestError):
        lb.build_labels(
            pl.DataFrame(
                {"decision_time": [SEALED]}, schema={"decision_time": pl.Datetime("us", "UTC")}
            ),
            bars,
            P,
        )


def test_three_class_threshold_fit_on_train_only() -> None:
    train = pl.DataFrame({"z": [-3.0, -1.0, -0.2, 0.0, 0.1, 0.5, 2.0, 4.0, -0.4]})
    tau = lb.fit_threshold(train["z"], neutral_share=1 / 3)
    # |z| ordenado: 0,0 0,1 0,2 0,4 0,5 1 2 3 4 → cuantil 1/3 (más cercano: posición 2,67 → 3) = 0,4
    assert tau == pytest.approx(0.4)
    test = pl.Series("z", [0.39, 0.41, -0.41, None])
    assert lb.three_class(test, tau).to_list() == [0, 1, -1, None]
    with pytest.raises(SchemaError):
        lb.fit_threshold(pl.Series("z", [None, None], dtype=pl.Float64), neutral_share=1 / 3)

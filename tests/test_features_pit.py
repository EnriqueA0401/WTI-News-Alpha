"""Tests del núcleo point-in-time de F4 (docs/plans/FASE_04.md): casos a mano, canario y
propiedad de truncamiento."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wti_news_alpha.errors import LeakageError, NaiveTimestampError, SchemaError
from wti_news_alpha.features import pit

T0 = datetime(2024, 3, 8, 15, 0, tzinfo=UTC)
US = timedelta(microseconds=1)


def _grid(times: list[datetime]) -> pl.DataFrame:
    return pl.DataFrame(
        {"decision_time": times}, schema={"decision_time": pl.Datetime("us", "UTC")}
    )


def _src(times: list[datetime], values: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {"available_at": times, "v": values},
        schema={"available_at": pl.Datetime("us", "UTC"), "v": pl.Float64},
    )


def test_asof_exact_match_is_visible_and_one_microsecond_before_is_not() -> None:
    grid = _grid([T0 - US, T0, T0 + timedelta(minutes=5)])
    src = _src([T0, T0 + timedelta(minutes=1)], [1.0, 2.0])
    out = pit.asof_latest(grid, src, prefix="x", values=["v"])
    assert out["x_v"].to_list() == [None, 1.0, 2.0]
    assert out["x_available_at"].to_list() == [None, T0, T0 + timedelta(minutes=1)]
    assert out.columns == ["decision_time", "x_v", "x_available_at"]


def test_asof_max_age_nulls_stale_values() -> None:
    grid = _grid([T0 + timedelta(minutes=10), T0 + timedelta(minutes=11)])
    src = _src([T0], [1.0])
    out = pit.asof_latest(grid, src, prefix="x", values=["v"], max_age=timedelta(minutes=10))
    assert out["x_v"].to_list() == [1.0, None]
    assert out["x_available_at"].to_list() == [T0, None]


def test_asof_rejects_ambiguous_or_invalid_inputs() -> None:
    grid = _grid([T0])
    with pytest.raises(SchemaError, match="available_at duplicado"):
        pit.asof_latest(grid, _src([T0, T0], [1.0, 2.0]), prefix="x", values=["v"])
    with pytest.raises(SchemaError, match="decision_time"):
        pit.asof_latest(_grid([T0, T0]), _src([T0], [1.0]), prefix="x", values=["v"])
    with pytest.raises(SchemaError, match="faltan"):
        pit.asof_latest(grid, _src([T0], [1.0]), prefix="x", values=["w"])
    naive = pl.DataFrame({"available_at": [datetime(2024, 1, 1)], "v": [1.0]})
    with pytest.raises(NaiveTimestampError):
        pit.asof_latest(grid, naive, prefix="x", values=["v"])


def test_asof_input_order_does_not_matter() -> None:
    grid = _grid([T0 + timedelta(minutes=5), T0])
    src = _src([T0 + timedelta(minutes=1), T0], [2.0, 1.0])
    out = pit.asof_latest(grid, src, prefix="x", values=["v"])
    assert out["decision_time"].to_list() == [T0, T0 + timedelta(minutes=5)]
    assert out["x_v"].to_list() == [1.0, 2.0]


def test_leakage_guard_canary() -> None:
    ok = _grid([T0]).with_columns(x_available_at=pl.lit(T0).dt.replace_time_zone("UTC"))
    pit.check_no_leakage(ok)
    future = ok.with_columns(y_available_at=pl.lit(T0 + US).dt.replace_time_zone("UTC"))
    with pytest.raises(LeakageError, match="y_available_at"):
        pit.check_no_leakage(future)
    with pytest.raises(SchemaError, match="ninguna columna"):
        pit.check_no_leakage(_grid([T0]))


def test_leakage_guard_ignores_missing_components() -> None:
    df = _grid([T0]).with_columns(x_available_at=pl.lit(None, dtype=pl.Datetime("us", "UTC")))
    pit.check_no_leakage(df)


def test_decision_grid_skips_gaps_and_closed_market() -> None:
    # Barras de 1 min completas de 15:00 a 15:12 y de 16:00 a 16:03 (hueco: mercado cerrado).
    ends = [T0 + timedelta(minutes=m) for m in range(1, 13)] + [
        T0 + timedelta(minutes=m) for m in range(61, 64)
    ]
    bars = pl.DataFrame({"available_at": ends}, schema={"available_at": pl.Datetime("us", "UTC")})
    grid = pit.decision_grid(bars, step=timedelta(minutes=5), max_bar_age=timedelta(minutes=5))
    assert grid["decision_time"].to_list() == [
        T0 + timedelta(minutes=5),
        T0 + timedelta(minutes=10),
        T0 + timedelta(minutes=15),  # última barra 15:12, 3 min: aún vale
        T0 + timedelta(minutes=65),
    ]


def test_decision_grid_validates_inputs() -> None:
    bars = pl.DataFrame({"available_at": [T0]}, schema={"available_at": pl.Datetime("us", "UTC")})
    with pytest.raises(SchemaError, match="step"):
        pit.decision_grid(bars, step=timedelta(minutes=7), max_bar_age=timedelta(minutes=5))
    with pytest.raises(SchemaError, match="vacío"):
        pit.decision_grid(bars.clear(), step=timedelta(minutes=5), max_bar_age=timedelta(minutes=5))


@settings(max_examples=60, deadline=None)
@given(
    st.lists(st.integers(0, 600), min_size=1, max_size=40, unique=True),
    st.lists(st.integers(0, 650), min_size=1, max_size=40, unique=True),
    st.integers(0, 650),
)
def test_property_truncation(src_min: list[int], grid_min: list[int], cut: int) -> None:
    """Las features en t son idénticas con el dataset completo y con el truncado en t."""
    src = _src([T0 + timedelta(minutes=m) for m in src_min], [float(m) for m in src_min])
    grid = _grid([T0 + timedelta(minutes=m) for m in grid_min])
    t_cut = T0 + timedelta(minutes=cut)
    full = pit.asof_latest(grid, src, prefix="x", values=["v"], max_age=timedelta(minutes=90))
    trunc = pit.asof_latest(
        grid,
        src.filter(pl.col("available_at") <= t_cut),
        prefix="x",
        values=["v"],
        max_age=timedelta(minutes=90),
    )
    keep = pl.col("decision_time") <= t_cut
    assert full.filter(keep).equals(trunc.filter(keep))
    pit.check_no_leakage(full)

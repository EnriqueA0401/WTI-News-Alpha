"""Features de fundamentales y posicionamiento (FASE_04.md): casos a mano y truncamiento."""

from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.features import fundamentals as fu
from wti_news_alpha.features import pit

R1 = datetime(2024, 1, 10, 15, 30, tzinfo=UTC)  # miércoles 10:30 ET
US = timedelta(microseconds=1)


def _grid(times: list[datetime]) -> pl.DataFrame:
    return pl.DataFrame(
        {"decision_time": times}, schema={"decision_time": pl.Datetime("us", "UTC")}
    )


def _wpsr(rows: list[tuple[str, date, float, datetime]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "series_id": [r[0] for r in rows],
            "week_ending": [r[1] for r in rows],
            "value": [r[2] for r in rows],
            "available_at": [r[3] for r in rows],
        },
        schema_overrides={"available_at": pl.Datetime("us", "UTC")},
    )


def test_wpsr_latest_week_and_change() -> None:
    r0 = R1 - timedelta(days=7)
    r2 = R1 + timedelta(days=14)  # recuperación: dos semanas publicadas a la vez
    rows = []
    for s, base in (("A", 100.0), ("B", 10.0)):
        rows += [
            (s, date(2023, 12, 29), base, r0),
            (s, date(2024, 1, 5), base + 1, R1),
            (s, date(2024, 1, 12), base + 3, r2),
            (s, date(2024, 1, 19), base + 6, r2),
        ]
    out = fu.wpsr_features(_grid([R1 - US, R1, r2]), _wpsr(rows))
    assert out["wpsr_A_value"].to_list() == [100.0, 101.0, 106.0]
    assert out["wpsr_A_chg"].to_list() == [None, 1.0, 3.0]  # 19-ene frente a 12-ene
    assert out["wpsr_B_value"].to_list() == [10.0, 11.0, 16.0]
    assert out["wpsr_available_at"].to_list() == [r0, R1, r2]
    assert out["wpsr_age_h"].to_list() == pytest.approx([7 * 24 - 1 / 3.6e9, 0.0, 0.0])
    pit.check_no_leakage(out)


def test_wpsr_change_needs_previous_week() -> None:
    rows = [("A", date(2024, 1, 5), 1.0, R1), ("A", date(2024, 1, 19), 5.0, R1 + timedelta(7))]
    out = fu.wpsr_features(_grid([R1 + timedelta(days=8)]), _wpsr(rows))
    assert out["wpsr_A_chg"].to_list() == [None]  # falta la semana del 12-ene


def test_wpsr_rejects_misaligned_release_times() -> None:
    rows = [("A", date(2024, 1, 5), 1.0, R1), ("B", date(2024, 1, 5), 1.0, R1 + US)]
    with pytest.raises(SchemaError, match="horas de publicación"):
        fu.wpsr_features(_grid([R1]), _wpsr(rows))
    with pytest.raises(SchemaError, match="duplicad"):
        fu.wpsr_features(_grid([R1]), _wpsr([rows[0], rows[0]]))


def _cot(rows: list[tuple[date, int, int, datetime]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "as_of_date": [r[0] for r in rows],
            "mm_long": [r[1] for r in rows],
            "mm_short": [r[2] for r in rows],
            "open_interest": [1000] * len(rows),
            "available_at": [r[3] for r in rows],
        },
        schema_overrides={"available_at": pl.Datetime("us", "UTC")},
    )


def test_cot_net_and_change() -> None:
    f1 = datetime(2024, 1, 12, 20, 30, tzinfo=UTC)
    rows = [(date(2024, 1, 2), 300, 100, f1 - timedelta(days=7)), (date(2024, 1, 9), 250, 150, f1)]
    out = fu.cot_features(_grid([f1 - US, f1]), _cot(rows))
    assert out["cot_mm_net"].to_list() == [200, 100]
    assert out["cot_mm_net_chg"].to_list() == [None, -100]
    assert out["cot_mm_net_oi"].to_list() == [0.2, 0.1]
    pit.check_no_leakage(out)


def _fred(rows: list[tuple[str, date, float, datetime]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "series_id": [r[0] for r in rows],
            "obs_date": [r[1] for r in rows],
            "value": [r[2] for r in rows],
            "available_at": [r[3] for r in rows],
        },
        schema_overrides={"available_at": pl.Datetime("us", "UTC")},
    )


def test_fred_latest_observation_with_revisions() -> None:
    a1 = datetime(2024, 1, 3, 21, 15, tzinfo=UTC)
    rows = [
        ("DGS10", date(2024, 1, 2), 3.9, a1),
        ("DGS10", date(2024, 1, 3), 4.0, a1 + timedelta(days=1)),
        ("DGS10", date(2024, 1, 2), 3.95, a1 + timedelta(days=2)),  # revisión de la anterior
        ("DGS10", date(2024, 1, 3), 4.05, a1 + timedelta(days=3)),  # revisión de la última
    ]
    times = [a1 - US, a1, a1 + timedelta(days=1), a1 + timedelta(days=2), a1 + timedelta(days=3)]
    out = fu.fred_features(_grid(times), _fred(rows))
    # Revisar una observación antigua no cambia "el último dato conocido".
    assert out["fred_DGS10_value"].to_list() == [None, 3.9, 4.0, 4.0, 4.05]
    assert out["fred_DGS10_obs_lag_d"].to_list() == [None, 1, 1, 2, 3]
    assert out["fred_DGS10_available_at"].to_list() == [
        None,
        a1,
        a1 + timedelta(days=1),
        a1 + timedelta(days=1),
        a1 + timedelta(days=3),
    ]
    pit.check_no_leakage(out)


def test_fred_holiday_without_value_is_not_latest() -> None:
    a1 = datetime(2024, 6, 18, 21, 15, tzinfo=UTC)
    rows = [("S", date(2024, 6, 18), 2.3, a1), ("S", date(2024, 6, 19), None, a1 + timedelta(1))]
    out = fu.fred_features(_grid([a1 + timedelta(days=1, hours=1)]), _fred(rows))  # type: ignore[arg-type]
    assert out["fred_S_value"].to_list() == [2.3]
    assert out["fred_S_obs_lag_d"].to_list() == [1]


@settings(max_examples=40, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(0, 30), st.integers(0, 60), st.floats(-5, 5, allow_nan=False)),
        min_size=1,
        max_size=30,
    ),
    st.integers(0, 60),
)
def test_fred_property_truncation(raw: list[tuple[int, int, float]], cut: int) -> None:
    d0 = date(2024, 1, 1)
    base = datetime(2024, 1, 1, 21, 0, tzinfo=UTC)
    seen: set[tuple[int, int]] = set()
    rows = []
    for obs, lag, v in raw:
        if (obs, lag) in seen:
            continue
        seen.add((obs, lag))
        rows.append(("S", d0 + timedelta(days=obs), v, base + timedelta(days=obs + lag)))
    src = _fred(rows)
    grid = _grid([base + timedelta(days=d, hours=1) for d in range(0, 95, 3)])
    t_cut = base + timedelta(days=cut, hours=1)
    full = fu.fred_features(grid, src, series=["S"])
    trunc = fu.fred_features(grid, src.filter(pl.col("available_at") <= t_cut), series=["S"])
    keep = pl.col("decision_time") <= t_cut
    assert full.filter(keep).equals(trunc.filter(keep))
    pit.check_no_leakage(full)

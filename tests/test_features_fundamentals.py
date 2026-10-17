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


def test_wpsr_backfilled_week_is_not_used_before_it_is_known() -> None:
    """Revisión F4 (hallazgo 2): la semana 10-ene se publica el 23, después de la del 17."""
    d22 = datetime(2024, 1, 22, 15, 30, tzinfo=UTC)
    d23 = d22 + timedelta(days=1)
    rows = [("A", date(2024, 1, 17), 30.0, d22), ("A", date(2024, 1, 10), 20.0, d23)]
    out = fu.wpsr_features(_grid([d22 + timedelta(hours=1), d23 + timedelta(hours=1)]), _wpsr(rows))
    assert out["wpsr_A_chg"].to_list() == [None, None]
    assert out["wpsr_A_value"].to_list() == [30.0, 30.0]  # no retrocede a la semana atrasada
    assert out["wpsr_data_lag_d"].to_list() == [5, 6]


def test_cot_holiday_shifted_report_keeps_change_and_data_lag() -> None:
    f = datetime(2019, 1, 1, 20, 30, tzinfo=UTC)
    rows = [
        (date(2018, 12, 18), 300, 100, f),
        (date(2018, 12, 24), 280, 100, f + timedelta(days=7)),  # lunes por festivo
        (date(2019, 1, 1), 250, 100, f + timedelta(days=14)),
        (date(2019, 1, 22), 200, 100, f + timedelta(days=21)),  # hueco de 3 semanas
    ]
    times = [f + timedelta(days=d, hours=1) for d in (7, 14, 21)]
    out = fu.cot_features(_grid(times), _cot(rows))
    assert out["cot_mm_net_chg"].to_list() == [-20, -30, None]
    assert out["cot_data_lag_d"].to_list() == [15, 14, 0]


def test_fred_null_revision_of_latest_fails_loudly() -> None:
    a = datetime(2024, 1, 3, 21, 0, tzinfo=UTC)
    rows = [
        ("S", date(2024, 1, 1), 1.0, a - timedelta(days=1)),
        ("S", date(2024, 1, 2), 2.0, a),
        ("S", date(2024, 1, 2), None, a + timedelta(days=2)),  # borra la última
    ]
    with pytest.raises(SchemaError, match="borran la última"):
        fu.fred_features(_grid([a]), _fred(rows))  # type: ignore[arg-type]
    old = [rows[0], rows[1], ("S", date(2024, 1, 1), None, a + timedelta(days=2))]
    out = fu.fred_features(_grid([a + timedelta(days=3)]), _fred(old))  # type: ignore[arg-type]
    assert out["fred_S_value"].to_list() == [2.0]  # borrar una antigua no afecta


@settings(max_examples=40, deadline=None)
@given(
    st.lists(st.tuples(st.integers(0, 20), st.integers(0, 40)), min_size=1, max_size=25),
    st.integers(0, 200),
)
def test_wpsr_and_cot_property_truncation(raw: list[tuple[int, int]], cut: int) -> None:
    """WPSR y COT: features en t idénticas con datos completos o truncados en t."""
    seen_w: set[int] = set()
    seen_at: set[int] = set()
    wrows, crows = [], []
    for week, delay in raw:
        at = R1 + timedelta(days=7 * week + delay)
        if week in seen_w or (7 * week + delay) in seen_at:
            continue
        seen_w.add(week)
        seen_at.add(7 * week + delay)
        wd = date(2024, 1, 5) + timedelta(days=7 * week)
        wrows += [("A", wd, float(week), at), ("B", wd, float(-week), at)]
        crows.append((wd, 100 + week, 50, at))
    grid = _grid([R1 + timedelta(days=d, hours=1) for d in range(0, 200, 3)])
    t_cut = R1 + timedelta(days=cut, hours=1)
    keep = pl.col("decision_time") <= t_cut
    for fn, src in ((fu.wpsr_features, _wpsr(wrows)), (fu.cot_features, _cot(crows))):
        full = fn(grid, src)  # type: ignore[operator]
        tsrc = src.filter(pl.col("available_at") <= t_cut)
        if tsrc.height == 0:
            continue
        trunc = fn(grid, tsrc)  # type: ignore[operator]
        cols = [c for c in full.columns if c in trunc.columns]
        assert full.filter(keep).select(cols).equals(trunc.filter(keep).select(cols))
        pit.check_no_leakage(full)

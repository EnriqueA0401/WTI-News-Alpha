"""Tests de utilidades de tiempo (componente crítico §7: tiempo y calendarios).

Casos calculados a mano (reglas DST de EE. UU., America/New_York):
- 2026: DST empieza el domingo 8 de marzo a las 02:00 EST (salta a 03:00 EDT)
  y termina el domingo 1 de noviembre a las 02:00 EDT (vuelve a 01:00 EST).
- EST = UTC-5, EDT = UTC-4.
"""

from datetime import UTC, date, datetime, time, timedelta, timezone

import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st

from wti_news_alpha.errors import CalendarError, NaiveTimestampError, SchemaError
from wti_news_alpha.timeutils import (
    NEW_YORK,
    ensure_utc,
    ny_wall_to_utc,
    require_utc_column,
    to_new_york,
)

# ---------------------------------------------------------------- ensure_utc


def test_ensure_utc_rejects_naive() -> None:
    with pytest.raises(NaiveTimestampError):
        ensure_utc(datetime(2026, 1, 15, 12, 0))


def test_naive_error_is_calendar_error() -> None:
    assert issubclass(NaiveTimestampError, CalendarError)


def test_ensure_utc_converts_offset_to_utc() -> None:
    ts = datetime(2026, 1, 15, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
    out = ensure_utc(ts)
    assert out == datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
    assert out.tzinfo is UTC


def test_ensure_utc_keeps_utc_instant() -> None:
    ts = datetime(2026, 7, 1, 16, 0, tzinfo=UTC)
    assert ensure_utc(ts) == ts


def _transitions(ts: datetime) -> tuple[bool, bool]:
    """Oráculo independiente (round-trip por UTC): (en_hueco, ambigua)."""
    wall = ts.replace(tzinfo=None, fold=0)
    candidates = {
        datetime.combine(ts.date(), ts.time(), tzinfo=ts.tzinfo).replace(fold=f).astimezone(UTC)
        for f in (0, 1)
    }
    valid = {c for c in candidates if c.astimezone(ts.tzinfo).replace(tzinfo=None, fold=0) == wall}
    return (len(valid) == 0, len(valid) == 2)


@given(
    st.datetimes(
        min_value=datetime(1950, 1, 1),
        max_value=datetime(2040, 1, 1),
        timezones=st.sampled_from([UTC, NEW_YORK, timezone(timedelta(hours=3))]),
    )
)
def test_ensure_utc_preserves_instant(ts: datetime) -> None:
    in_gap, is_ambiguous = _transitions(ts)
    if in_gap:
        with pytest.raises(CalendarError, match="no existe"):
            ensure_utc(ts)
        return
    if is_ambiguous:
        with pytest.raises(CalendarError, match="ambigua"):
            ensure_utc(ts)
    policy = "use_fold" if is_ambiguous else "raise"
    out = ensure_utc(ts, ambiguous=policy)
    assert out.tzinfo is UTC
    # Se compara por epoch: la igualdad entre zonas de horas ambiguas
    # devuelve siempre False en Python (PEP 495).
    assert out.timestamp() == ts.timestamp()
    assert to_new_york(out).timestamp() == ts.timestamp()


def test_ensure_utc_rejects_nonexistent_wall_time_regression() -> None:
    # Regresión (hallada por hypothesis): 02:00 del 13-mar-2016 no existe en NY.
    ts = datetime(2016, 3, 13, 2, 0, tzinfo=NEW_YORK)
    with pytest.raises(CalendarError, match="no existe"):
        ensure_utc(ts)
    with pytest.raises(CalendarError, match="no existe"):
        ensure_utc(ts, ambiguous="use_fold")


def test_ensure_utc_rejects_ambiguous_by_default_regression() -> None:
    # Regresión (revisión adversarial): 01:30 del 1-nov-2026 ocurre dos veces;
    # resolver con fold=0 por defecto adelantaría una hora el instante.
    with pytest.raises(CalendarError, match="ambigua"):
        ensure_utc(datetime(2026, 11, 1, 1, 30, tzinfo=NEW_YORK))
    with pytest.raises(CalendarError, match="ambigua"):
        to_new_york(datetime(2026, 11, 1, 1, 30, tzinfo=NEW_YORK))


def test_ensure_utc_ambiguous_with_explicit_fold_policy() -> None:
    # fold=0 es EDT (05:30 UTC), fold=1 es EST (06:30 UTC).
    first = datetime(2026, 11, 1, 1, 30, tzinfo=NEW_YORK)
    second = first.replace(fold=1)
    assert ensure_utc(first, ambiguous="use_fold") == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert ensure_utc(second, ambiguous="use_fold") == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


@pytest.mark.parametrize("bad", [date(2026, 1, 1), "2026-01-01T00:00:00Z", 1_700_000_000])
def test_ensure_utc_rejects_non_datetime(bad: object) -> None:
    with pytest.raises(CalendarError, match="datetime"):
        ensure_utc(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------- to_new_york


def test_to_new_york_rejects_naive() -> None:
    with pytest.raises(NaiveTimestampError):
        to_new_york(datetime(2026, 1, 15, 12, 0))


@pytest.mark.parametrize(
    ("utc_ts", "ny_wall", "offset_hours"),
    [
        # Invierno (EST, UTC-5)
        (datetime(2026, 1, 15, 17, 0, tzinfo=UTC), datetime(2026, 1, 15, 12, 0), -5),
        # Verano (EDT, UTC-4)
        (datetime(2026, 7, 1, 16, 0, tzinfo=UTC), datetime(2026, 7, 1, 12, 0), -4),
        # Último minuto EST antes del salto de marzo: 01:59 EST = 06:59 UTC
        (datetime(2026, 3, 8, 6, 59, tzinfo=UTC), datetime(2026, 3, 8, 1, 59), -5),
        # Primer instante EDT: 07:00 UTC = 03:00 EDT
        (datetime(2026, 3, 8, 7, 0, tzinfo=UTC), datetime(2026, 3, 8, 3, 0), -4),
        # Noviembre: 05:30 UTC = 01:30 EDT (primera pasada)
        (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), datetime(2026, 11, 1, 1, 30), -4),
        # Noviembre: 06:30 UTC = 01:30 EST (segunda pasada)
        (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), datetime(2026, 11, 1, 1, 30), -5),
    ],
)
def test_to_new_york_hand_cases(utc_ts: datetime, ny_wall: datetime, offset_hours: int) -> None:
    ny = to_new_york(utc_ts)
    assert ny.replace(tzinfo=None) == ny_wall
    assert ny.utcoffset() == timedelta(hours=offset_hours)


# ---------------------------------------------------------------- ny_wall_to_utc


@pytest.mark.parametrize(
    ("d", "t", "expected_utc"),
    [
        # Release EIA WPSR típico, miércoles 10:30 ET
        (date(2026, 1, 14), time(10, 30), datetime(2026, 1, 14, 15, 30, tzinfo=UTC)),
        (date(2026, 7, 15), time(10, 30), datetime(2026, 7, 15, 14, 30, tzinfo=UTC)),
        # COT viernes 15:30 ET
        (date(2026, 3, 13), time(15, 30), datetime(2026, 3, 13, 19, 30, tzinfo=UTC)),
        # Apertura de domingo 18:00 ET justo el día del cambio DST de marzo
        (date(2026, 3, 8), time(18, 0), datetime(2026, 3, 8, 22, 0, tzinfo=UTC)),
        # Apertura de domingo 18:00 ET el día del cambio DST de noviembre
        (date(2026, 11, 1), time(18, 0), datetime(2026, 11, 1, 23, 0, tzinfo=UTC)),
        # Medianoche y último minuto antes del salto
        (date(2026, 3, 8), time(0, 0), datetime(2026, 3, 8, 5, 0, tzinfo=UTC)),
        (date(2026, 3, 8), time(1, 59), datetime(2026, 3, 8, 6, 59, tzinfo=UTC)),
        (date(2026, 3, 8), time(3, 0), datetime(2026, 3, 8, 7, 0, tzinfo=UTC)),
    ],
)
def test_ny_wall_to_utc_hand_cases(d: date, t: time, expected_utc: datetime) -> None:
    out = ny_wall_to_utc(d, t)
    assert out == expected_utc
    assert out.tzinfo is UTC


def test_ny_wall_to_utc_rejects_nonexistent_spring_forward() -> None:
    # 02:30 del 8-mar-2026 no existe en Nueva York
    with pytest.raises(CalendarError, match="no existe"):
        ny_wall_to_utc(date(2026, 3, 8), time(2, 30))


def test_ny_wall_to_utc_rejects_ambiguous_fall_back() -> None:
    # 01:30 del 1-nov-2026 ocurre dos veces
    with pytest.raises(CalendarError, match="ambigua"):
        ny_wall_to_utc(date(2026, 11, 1), time(1, 30))


def test_ny_wall_to_utc_rejects_datetime_as_date_regression() -> None:
    with pytest.raises(CalendarError, match="no datetime"):
        ny_wall_to_utc(datetime(2026, 1, 15, 3, 0, tzinfo=UTC), time(10, 30))


def test_ny_wall_to_utc_rejects_time_with_tzinfo() -> None:
    with pytest.raises(CalendarError):
        ny_wall_to_utc(date(2026, 1, 14), time(10, 30, tzinfo=UTC))


# ---------------------------------------------------------------- require_utc_column


def test_require_utc_column_accepts_utc() -> None:
    df = pl.DataFrame({"ts": [datetime(2026, 1, 1, tzinfo=UTC)]})
    require_utc_column(df, "ts")  # no lanza


def test_require_utc_column_rejects_nulls_by_default() -> None:
    df = pl.DataFrame({"ts": [datetime(2026, 1, 1, tzinfo=UTC), None]})
    with pytest.raises(SchemaError, match="1 nulos"):
        require_utc_column(df, "ts")
    require_utc_column(df, "ts", allow_nulls=True)


def test_require_utc_column_rejects_naive() -> None:
    df = pl.DataFrame({"ts": [datetime(2026, 1, 1)]})
    with pytest.raises(NaiveTimestampError):
        require_utc_column(df, "ts")


def test_require_utc_column_rejects_other_timezone() -> None:
    df = pl.DataFrame({"ts": [datetime(2026, 1, 1, tzinfo=UTC)]}).with_columns(
        pl.col("ts").dt.convert_time_zone("America/New_York")
    )
    with pytest.raises(SchemaError, match="UTC"):
        require_utc_column(df, "ts")


def test_require_utc_column_rejects_non_datetime() -> None:
    df = pl.DataFrame({"ts": ["2026-01-01"]})
    with pytest.raises(SchemaError, match="Datetime"):
        require_utc_column(df, "ts")


def test_require_utc_column_rejects_missing_column() -> None:
    df = pl.DataFrame({"x": [1]})
    with pytest.raises(SchemaError, match="no existe"):
        require_utc_column(df, "ts")

"""Tests del calendario federal de días hábiles (5 U.S.C. 6103 + órdenes ejecutivas).

Fechas reales calculadas a mano; textos SINTÉTICOS con el formato de las fuentes.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from wti_news_alpha.errors import CalendarError, SchemaError
from wti_news_alpha.normalization import federal_calendar as fc

D = date.fromisoformat
RAW = Path(__file__).resolve().parents[1] / "data" / "raw" / "federal_calendar"

STATUTE = (
    "(a) The following are legal public holidays: New Year's Day, January 1. Birthday of "
    "Martin Luther King, Jr., the third Monday in January. Washington's Birthday, the third "
    "Monday in February. Memorial Day, the last Monday in May. Juneteenth National Independence "
    "Day, June 19. Independence Day, July 4. Labor Day, the first Monday in September. Columbus "
    "Day, the second Monday in October. Veterans Day, November 11. Thanksgiving Day, the fourth "
    "Thursday in November. Christmas Day, December 25. (b) ... Instead of a holiday that occurs "
    "on a Saturday, the Friday immediately before is a legal public holiday ... (c) January 20 of "
    "each fourth year after 1965, Inauguration Day, is a legal public holiday ..."
)


def _eos(*titles: str) -> str:
    rows = [{"title": t, "signing_date": "2018-12-01", "executive_order_number": 1} for t in titles]
    return json.dumps({"count": len(rows), "results": rows})


# ---------------------------------------------------------------- fuentes


def test_statute_check_passes() -> None:
    fc.check_statute(f"<html><p>{STATUTE}</p></html>")


def test_statute_missing_rule_raises() -> None:
    with pytest.raises(SchemaError, match="Columbus"):
        fc.check_statute(STATUTE.replace("Columbus Day, the second Monday in October.", ""))


def test_parse_closure_eos() -> None:
    text = _eos(
        "Providing for the Closing of Executive Departments and Agencies of the Federal "
        "Government on December 24, 2025, and December 26, 2025",
        "Half-Day Closing of Executive Departments and Agencies of the Federal Government on "
        "Thursday, December 24, 2015",
    )
    assert fc.parse_closure_eos(text) == {
        D("2025-12-24"): "eo_closure",
        D("2025-12-26"): "eo_closure",
        D("2015-12-24"): "eo_half_day_closure",
    }


def test_parse_closure_eos_errors() -> None:
    with pytest.raises(SchemaError, match="fecha"):
        fc.parse_closure_eos(_eos("Closing of Executive Departments on some day"))
    with pytest.raises(SchemaError, match="count"):
        fc.parse_closure_eos(json.dumps({"count": 5, "results": []}))


# ---------------------------------------------------------------- festivos


@pytest.mark.parametrize(
    ("day", "reason"),
    [
        ("2015-01-01", "new_years_day"),
        ("2015-01-19", "mlk_day"),  # tercer lunes de enero
        ("2015-02-16", "washingtons_birthday"),
        ("2015-05-25", "memorial_day"),  # último lunes de mayo
        ("2015-07-03", "independence_day_observed"),  # 4-jul sábado → viernes
        ("2015-09-07", "labor_day"),
        ("2015-10-12", "columbus_day"),
        ("2015-11-11", "veterans_day"),
        ("2015-11-26", "thanksgiving_day"),
        ("2015-12-25", "christmas_day"),
        ("2016-12-26", "christmas_day_observed"),  # 25-dic domingo → lunes
        ("2017-01-02", "new_years_day_observed"),  # 1-ene domingo → lunes
        ("2017-01-20", "inauguration_day"),
        ("2021-01-20", "inauguration_day"),
        ("2021-06-18", "juneteenth_observed"),  # 19-jun sábado → viernes
        ("2022-06-20", "juneteenth_observed"),  # 19-jun domingo → lunes
        ("2021-12-31", "new_years_day_observed"),  # 1-ene-2022 sábado → viernes previo
    ],
)
def test_statutory_holidays(day: str, reason: str) -> None:
    year = D(day).year
    days = fc.statutory_non_business_days(year - 1, year + 1)
    assert days[D(day)] == reason


def test_juneteenth_only_from_2021() -> None:
    days = fc.statutory_non_business_days(2019, 2020)
    assert D("2019-06-19") not in days
    assert D("2020-06-19") not in days


def test_inauguration_on_sunday_moves_to_monday() -> None:
    # 2013-01-20 domingo → lunes 21 (que además es MLK).
    days = fc.statutory_non_business_days(2013, 2013)
    assert D("2013-01-21") in days
    assert D("2013-01-20") not in days


def test_range_validation() -> None:
    with pytest.raises(CalendarError, match="rango"):
        fc.statutory_non_business_days(2020, 2019)


# ---------------------------------------------------------------- días hábiles


@pytest.fixture
def cal() -> fc.FederalCalendar:
    closures = {D("2018-12-05"): "eo_closure", D("2015-12-24"): "eo_half_day_closure"}
    return fc.FederalCalendar.build(2014, 2026, closures)


def test_business_day_rules(cal: fc.FederalCalendar) -> None:
    assert cal.is_business_day(D("2018-12-04"))
    assert not cal.is_business_day(D("2018-12-05"))  # cierre (funeral G. H. W. Bush)
    assert not cal.is_business_day(D("2015-12-24"))  # medio día → no hábil (conservador)
    assert not cal.is_business_day(D("2018-12-08"))  # sábado


def test_nth_business_day_after(cal: fc.FederalCalendar) -> None:
    # as-of martes 2018-12-04 → mié 5 cerrado → jue 6, vie 7, lun 10.
    assert cal.nth_business_day_after(D("2018-12-04"), 3) == D("2018-12-10")
    # as-of normal: martes 2018-11-27 → viernes 30.
    assert cal.nth_business_day_after(D("2018-11-27"), 3) == D("2018-11-30")


def test_non_business_between(cal: fc.FederalCalendar) -> None:
    assert cal.non_business_weekdays_between(D("2018-12-04"), D("2018-12-10")) == [D("2018-12-05")]
    assert cal.non_business_weekdays_between(D("2018-11-27"), D("2018-11-30")) == []


def test_out_of_range_raises(cal: fc.FederalCalendar) -> None:
    with pytest.raises(CalendarError, match="fuera del rango"):
        cal.is_business_day(D("2030-01-02"))


def test_closure_out_of_range_raises() -> None:
    with pytest.raises(CalendarError, match="fuera del rango"):
        fc.FederalCalendar.build(2015, 2016, {D("2018-12-05"): "eo_closure"})


@given(st.dates(min_value=D("2014-01-01"), max_value=D("2026-11-30")), st.integers(1, 5))
def test_property_nth_business_day(start: date, n: int) -> None:
    cal = fc.FederalCalendar.build(2014, 2026, {})
    out = cal.nth_business_day_after(start, n)
    assert out > start
    assert cal.is_business_day(out)
    between = [start + timedelta(days=i) for i in range(1, (out - start).days)]
    assert sum(cal.is_business_day(d) for d in between) == n - 1


# ---------------------------------------------------------------- fuentes reales


@pytest.mark.skipif(not RAW.exists(), reason="raw federal_calendar no disponible")
def test_real_sources_parse() -> None:
    fc.check_statute((RAW / "usc5_6103_2023.htm").read_text(encoding="utf-8"))
    closures = fc.parse_closure_eos(
        (RAW / "fr_closing_eos_2014plus_20261016.json").read_text(encoding="utf-8")
    )
    assert D("2018-12-05") in closures
    assert D("2025-01-09") in closures
    assert closures[D("2015-12-24")] == "eo_half_day_closure"

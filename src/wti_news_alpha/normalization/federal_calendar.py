"""Calendario federal de EE. UU. de días hábiles (días de oficina de las agencias).

Fuentes oficiales (raw con manifiesto ``manifests/federal_calendar.jsonl``):
- 5 U.S.C. 6103 (govinfo, edición 2023): festivos legales, regla sábado → viernes
  y Inauguration Day (área de Washington, donde tienen sede la CFTC y la EIA).
- Órdenes ejecutivas de cierre (Federal Register API).

Invariantes:
- Las reglas codificadas se contrastan con el texto oficial (``check_statute``):
  si falta alguna frase, ``SchemaError``.
- Domingo → lunes (EO 11582, no incluida en el texto de 6103) y el medio día de
  cierre se tratan como no hábiles: ambas opciones retrasan fechas (conservador).
- Consultar fuera del rango construido lanza ``CalendarError``.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from wti_news_alpha.errors import CalendarError, SchemaError

_SAT, _SUN = 5, 6
_MON, _THU = 0, 3
JUNETEENTH_FIRST_YEAR = 2021  # Juneteenth National Independence Day Act (2021-06-17)
INAUGURATION_BASE_YEAR = 1965

STATUTE_PHRASES = {
    "new_years_day": "New Year's Day, January 1.",
    "mlk_day": "Martin Luther King, Jr., the third Monday in January.",
    "washingtons_birthday": "Washington's Birthday, the third Monday in February.",
    "memorial_day": "Memorial Day, the last Monday in May.",
    "juneteenth": "Juneteenth National Independence Day, June 19.",
    "independence_day": "Independence Day, July 4.",
    "labor_day": "Labor Day, the first Monday in September.",
    "columbus_day": "Columbus Day, the second Monday in October.",
    "veterans_day": "Veterans Day, November 11.",
    "thanksgiving_day": "Thanksgiving Day, the fourth Thursday in November.",
    "christmas_day": "Christmas Day, December 25.",
    "saturday_rule": (
        "Instead of a holiday that occurs on a Saturday, the Friday immediately before"
    ),
    "inauguration_day": "January 20 of each fourth year after 1965, Inauguration Day",
}
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_EO_DATE_RE = re.compile(rf"({_MONTHS}) (\d{{1,2}}), (\d{{4}})")


def _plain(html_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_text)
    return re.sub(r"\s+", " ", text.replace("&#8217;", "'").replace("&rsquo;", "'"))


def check_statute(html_text: str) -> None:
    """Comprueba que el texto oficial contiene cada regla codificada."""
    text = _plain(html_text)
    missing = [
        k for k, phrase in STATUTE_PHRASES.items() if re.sub(r"\s+", " ", phrase) not in text
    ]
    if missing:
        raise SchemaError(f"5 U.S.C. 6103: no aparecen las reglas {missing} (Columbus, etc.)")


def parse_closure_eos(text: str) -> dict[date, str]:
    """Fechas de cierre de las órdenes ejecutivas (título) → motivo."""
    data = json.loads(text)
    rows = data["results"]
    if data["count"] != len(rows):
        raise SchemaError(f"Federal Register: count={data['count']} ≠ {len(rows)} resultados")
    out: dict[date, str] = {}
    for r in rows:
        title = r["title"]
        found = _EO_DATE_RE.findall(title)
        if not found:
            raise SchemaError(f"orden ejecutiva sin fecha en el título: {title!r}")
        reason = "eo_half_day_closure" if title.startswith("Half-Day") else "eo_closure"
        for m, d, y in found:
            out[datetime.strptime(f"{m} {d} {y}", "%B %d %Y").date()] = reason  # noqa: DTZ007
    return out


def _nth_weekday(y: int, m: int, wd: int, n: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(wd - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y: int, m: int, wd: int) -> date:
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - wd) % 7)


def _observed(d: date, name: str) -> tuple[date, str]:
    if d.weekday() == _SAT:
        return d - timedelta(days=1), f"{name}_observed"
    if d.weekday() == _SUN:
        return d + timedelta(days=1), f"{name}_observed"
    return d, name


def _year_holidays(y: int) -> dict[date, str]:
    fixed = [
        (date(y, 1, 1), "new_years_day"),
        (date(y, 7, 4), "independence_day"),
        (date(y, 11, 11), "veterans_day"),
        (date(y, 12, 25), "christmas_day"),
    ]
    if y >= JUNETEENTH_FIRST_YEAR:
        fixed.append((date(y, 6, 19), "juneteenth"))
    out = dict(_observed(d, n) for d, n in fixed)
    out[_nth_weekday(y, 1, _MON, 3)] = "mlk_day"
    out[_nth_weekday(y, 2, _MON, 3)] = "washingtons_birthday"
    out[_last_weekday(y, 5, _MON)] = "memorial_day"
    out[_nth_weekday(y, 9, _MON, 1)] = "labor_day"
    out[_nth_weekday(y, 10, _MON, 2)] = "columbus_day"
    out[_nth_weekday(y, 11, _THU, 4)] = "thanksgiving_day"
    if (y - INAUGURATION_BASE_YEAR) % 4 == 0:
        inaug = date(y, 1, 20)
        if inaug.weekday() == _SUN:
            inaug += timedelta(days=1)
        out.setdefault(inaug, "inauguration_day")
    return out


def statutory_non_business_days(first_year: int, last_year: int) -> dict[date, str]:
    """Festivos legales (con su día observado) entre el 1-ene y el 31-dic del rango."""
    if last_year < first_year:
        raise CalendarError(f"rango de años inválido: {first_year}-{last_year}")
    out: dict[date, str] = {}
    for y in range(first_year - 1, last_year + 2):
        out.update(_year_holidays(y))
    lo, hi = date(first_year, 1, 1), date(last_year, 12, 31)
    return {d: r for d, r in sorted(out.items()) if lo <= d <= hi}


@dataclass(frozen=True)
class FederalCalendar:
    first: date
    last: date
    non_business: dict[date, str]

    @classmethod
    def build(cls, first_year: int, last_year: int, closures: dict[date, str]) -> "FederalCalendar":
        days = statutory_non_business_days(first_year, last_year)
        lo, hi = date(first_year, 1, 1), date(last_year, 12, 31)
        outside = [d for d in closures if not lo <= d <= hi]
        if outside:
            raise CalendarError(f"cierres fuera del rango {lo}-{hi}: {outside}")
        return cls(lo, hi, {**days, **closures})

    def _check(self, d: date) -> None:
        if not self.first <= d <= self.last:
            raise CalendarError(f"{d} fuera del rango del calendario ({self.first}-{self.last})")

    def is_business_day(self, d: date) -> bool:
        self._check(d)
        return d.weekday() < _SAT and d not in self.non_business

    def nth_business_day_after(self, d: date, n: int) -> date:
        cur = d
        while n:
            cur += timedelta(days=1)
            n -= self.is_business_day(cur)
        return cur

    def non_business_weekdays_between(self, start: date, end: date) -> list[date]:
        """Días laborables (lun-vie) no hábiles en el intervalo abierto (start, end)."""
        days = [start + timedelta(days=i) for i in range(1, (end - start).days)]
        return [d for d in days if d.weekday() < _SAT and not self.is_business_day(d)]

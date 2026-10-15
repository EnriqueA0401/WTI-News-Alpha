"""F1 (CFTC COT): contrasta una regla de fecha de publicación con las fechas oficiales.

Regla candidata (About the COT Reports, data/raw/cftc/pages/about_cot.htm): publicación
el 3.er día hábil federal tras la fecha "as of", a las 15:30 ET. Festivos federales
calculados por regla estatutaria (5 U.S.C. 6103; Juneteenth desde 2021) + cierres
ad hoc anunciados por la CFTC. Fuentes oficiales contrastadas (todas en data/raw/cftc):
- schedule/release_schedule_index.htm: calendario 2026 (tentativo).
- press/9147-25.htm: tabla 2025 con "Original Publish Date" y "New Publish Date".
- pages/historical_special_announcements.htm: desplazamientos por festivo anunciados.
Uso: uv run python -I scripts/f1_audit/cftc_calendar.py RAW_DIR
"""

import html
import io
import json
import re
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl

CODE = "067651"
STUDY_START = date(2015, 2, 18)
# Cierres ad hoc citados en historical_special_announcements.htm (2018-12-04, 2020-12-28,
# 2025-01-07). Otros cierres por orden ejecutiva NO están verificados aquí.
AD_HOC_CLOSURES = {date(2018, 12, 5), date(2020, 12, 24), date(2025, 1, 9)}
# Desplazamientos anunciados (as_of -> publicación) en historical_special_announcements.htm.
ANNOUNCED = {
    date(2015, 6, 30): date(2015, 7, 6),
    date(2020, 12, 21): date(2020, 12, 28),
    date(2021, 6, 15): date(2021, 6, 21),
    date(2025, 1, 7): date(2025, 1, 13),
}


def nth_weekday(y: int, m: int, wd: int, n: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(wd - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def last_weekday(y: int, m: int, wd: int) -> date:
    d = date(y, m + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - wd) % 7)


def observed(d: date) -> date:
    if d.weekday() == 5:  # sábado -> viernes
        return d - timedelta(days=1)
    if d.weekday() == 6:  # domingo -> lunes
        return d + timedelta(days=1)
    return d


def federal_holidays(y: int) -> set[date]:
    hs = {
        observed(date(y, 1, 1)),
        nth_weekday(y, 1, 0, 3),
        nth_weekday(y, 2, 0, 3),
        last_weekday(y, 5, 0),
        observed(date(y, 7, 4)),
        nth_weekday(y, 9, 0, 1),
        nth_weekday(y, 10, 0, 2),
        observed(date(y, 11, 11)),
        nth_weekday(y, 11, 3, 4),
        observed(date(y, 12, 25)),
    }
    if y >= 2021:
        hs.add(observed(date(y, 6, 19)))
    return hs


HOLIDAYS = set().union(*(federal_holidays(y) for y in range(2014, 2029))) | AD_HOC_CLOSURES


def is_bday(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def rule_release(as_of: date, n: int = 3) -> date:
    d = as_of
    while n:
        d += timedelta(days=1)
        n -= is_bday(d)
    return d


def text_of(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def official_2026(raw: Path) -> list[date]:
    t = text_of(raw / "cftc/schedule/release_schedule_index.htm")
    block = t.split("2026 Release Schedule Month Dates", 1)[1].split("*Delayed", 1)[0]
    months = "January February March April May June July August September October November December"
    out: list[date] = []
    parts = re.split(r"\b(" + "|".join(months.split()) + r")\b", block)
    for name, days in zip(parts[1::2], parts[2::2], strict=True):
        m = months.split().index(name) + 1
        out += [date(2026, m, int(x)) for x in re.findall(r"\d{2}", days)]
    return out


def table_2025(raw: Path) -> list[tuple[date, date, date]]:
    t = text_of(raw / "cftc/press/9147-25.htm")
    rows = re.findall(r"(\d\d)/(\d\d)/(\d{4}) (\d\d)/(\d\d)/(\d{4}) (\d\d)/(\d\d)/(\d{4})", t)
    return [
        (
            date(int(r[2]), int(r[0]), int(r[1])),
            date(int(r[5]), int(r[3]), int(r[4])),
            date(int(r[8]), int(r[6]), int(r[7])),
        )
        for r in rows
    ]


def as_of_dates(raw: Path) -> list[date]:
    ds: set[date] = set()
    for f in sorted((raw / "cftc/history").glob("fut_disagg_txt_20*.zip")):
        with zipfile.ZipFile(f) as z:
            (m,) = z.infolist()
            df = pl.read_csv(io.BytesIO(z.read(m.filename)), infer_schema=False, encoding="latin1")
        sel = df.filter(pl.col("CFTC_Contract_Market_Code").str.strip_chars() == CODE)
        ds |= {date.fromisoformat(s[:10]) for s in sel["Report_Date_as_YYYY-MM-DD"]}
    return sorted(d for d in ds if d >= STUDY_START)


def main() -> None:
    raw = Path(sys.argv[1])
    dates = as_of_dates(raw)
    rel = {d: rule_release(d) for d in dates}
    off26 = official_2026(raw)
    rule26 = sorted(r for r in rel.values() if r.year == 2026)
    # 2026: comparar sólo fechas oficiales ya alcanzadas por los datos (as-of <= último).
    reached = [d for d in off26 if d <= rule_release(dates[-1])]
    t25 = table_2025(raw)
    orig_mismatch = [(str(a), str(o), str(rel.get(a))) for a, o, _ in t25 if rel.get(a) != o]
    ann_mismatch = [(str(a), str(p), str(rel.get(a))) for a, p in ANNOUNCED.items() if rel[a] != p]
    shifted = [
        {
            "as_of": str(d),
            "as_of_wd": d.strftime("%a"),
            "rule_release": str(r),
            "wd": r.strftime("%a"),
        }
        for d, r in rel.items()
        if r != d + timedelta(days=3 if d.weekday() == 1 else 4)
    ]
    out = {
        "n_as_of_in_window": len(dates),
        "first_as_of": str(dates[0]),
        "last_as_of": str(dates[-1]),
        "official_2026_n": len(off26),
        "official_2026_reached_n": len(reached),
        "rule_2026_equals_official_reached": rule26 == reached,
        "rule_2026_diff": sorted(set(map(str, rule26)) ^ set(map(str, reached))),
        "table_2025_rows": len(t25),
        "table_2025_original_vs_rule_mismatch": orig_mismatch,
        "announced_vs_rule_mismatch": ann_mismatch,
        "n_holiday_shifted_weeks": len(shifted),
        "holiday_shifted_weeks": shifted,
        "release_weekday_counts": {
            wd: sum(1 for r in rel.values() if r.strftime("%a") == wd)
            for wd in ("Mon", "Tue", "Wed", "Thu", "Fri")
        },
    }
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()

"""F1 (CFTC COT): analiza :created_at/:updated_at (Socrata) de las filas 067651.

Para filas cargadas tras la carga inicial (2022-08-01), compara :created_at con la
fecha de publicación de la regla "3.er día hábil a las 15:30 ET" (cftc_calendar.py)
y lista filas con :updated_at != :created_at (posibles re-cargas/revisiones).
Uso: uv run python -I scripts/f1_audit/cftc_socrata_times.py SYSTEMFIELDS.json
"""

import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from cftc_calendar import rule_release

ET = ZoneInfo("America/New_York")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def main() -> None:
    rows = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    created = Counter(r[":created_at"] for r in rows)
    bulk_ts, bulk_n = created.most_common(1)[0]
    updated = [r for r in rows if r[":updated_at"] != r[":created_at"]]
    out_rows = []
    for r in rows:
        if r[":created_at"] == bulk_ts:
            continue
        as_of = date.fromisoformat(r["report_date_as_yyyy_mm_dd"][:10])
        c_et = parse(r[":created_at"]).astimezone(ET)
        rule = rule_release(as_of)
        out_rows.append(
            {
                "as_of": str(as_of),
                "created_et": c_et.strftime("%Y-%m-%d %a %H:%M:%S"),
                "rule_release": str(rule),
                "day_diff": (c_et.date() - rule).days,
                "minutes_after_1530": round(
                    (
                        c_et - datetime(c_et.year, c_et.month, c_et.day, 15, 30, tzinfo=ET)
                    ).total_seconds()
                    / 60,
                    1,
                ),
            }
        )
    same_day = [r for r in out_rows if r["day_diff"] == 0]
    mins = sorted(r["minutes_after_1530"] for r in same_day)
    out = {
        "n_rows": len(rows),
        "bulk_created_at": bulk_ts,
        "bulk_n": bulk_n,
        "n_incremental": len(out_rows),
        "n_same_day_as_rule": len(same_day),
        "minutes_after_1530_min": mins[0] if mins else None,
        "minutes_after_1530_median": mins[len(mins) // 2] if mins else None,
        "minutes_after_1530_max": mins[-1] if mins else None,
        "n_before_1530": sum(1 for m in mins if m < 0),
        "not_same_day": [r for r in out_rows if r["day_diff"] != 0],
        "n_updated_ne_created": len(updated),
        "updated_examples": [
            {k: r[k] for k in (":created_at", ":updated_at", "report_date_as_yyyy_mm_dd")}
            for r in updated[:10]
        ],
        "first_incremental": out_rows[:3],
        "last_incremental": out_rows[-3:],
    }
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()

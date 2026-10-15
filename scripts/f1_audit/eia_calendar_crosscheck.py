"""F1 auditoría EIA: contrasta fechas de publicación WPSR (archivo EIA) con fuentes independientes.

Compara las fechas de carpeta del índice oficial del archivo WPSR (2015+) con:
(a) fechas del release FRED 212 "Spot Prices" (EIA spot, publicado con el WPSR);
(b) las excepciones del calendario oficial vigente (schedule.php, dic-2024 a nov-2026).
Solo lee ficheros raw.
"""

import html
import json
import re
from datetime import UTC, datetime

from wti_news_alpha.runlog import REPO_ROOT

RAW = REPO_ROOT / "data/raw"
LINK_RE = re.compile(r"/petroleum/supply/weekly/archive/\d{4}/(\d{4})_(\d{2})_(\d{2})/")
SCHED_RE = re.compile(
    r"([A-Z][a-z]+ \d{1,2}, \d{4})\s+([A-Z][a-z]+ \d{1,2}, \d{4})\s+([A-Z][a-z]+day)\s+"
    r"(\d{1,2}:\d{2} [ap]\.m\.)\s+([^\n]+?)\s*(?=[A-Z][a-z]+ \d{1,2}, \d{4}|Top)"
)


def main() -> None:
    idx = sorted((RAW / "eia/web").glob("wpsr_archive_index_*.html"))[-1].read_text(
        encoding="utf-8", errors="replace"
    )
    eia = {f"{y}-{m}-{d}" for y, m, d in LINK_RE.findall(idx) if y >= "2015"}
    fred = {
        r["date"]
        for r in json.loads((RAW / "fred/api/release_212/dates_2015.json").read_text())[
            "release_dates"
        ]
    }
    lo, hi = max(min(eia), min(fred)), min(max(eia), max(fred))
    e = {d for d in eia if lo <= d <= hi}
    f = {d for d in fred if lo <= d <= hi}
    print(f"window={lo}..{hi} eia_archive={len(e)} fred_212={len(f)} common={len(e & f)}")
    print("eia_not_fred", sorted(e - f))
    print("fred_not_eia", sorted(f - e))

    sched_html = sorted((RAW / "eia/web").glob("wpsr_schedule_*.html"))[-1].read_text(
        encoding="utf-8", errors="replace"
    )
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", sched_html)))
    rows = SCHED_RE.findall(text)
    print(f"schedule_exceptions={len(rows)}")
    for week, alt, day, time_, holiday in rows:
        alt_iso = datetime.strptime(alt, "%B %d, %Y").replace(tzinfo=UTC).date().isoformat()
        status = "in_archive" if alt_iso in eia else "not_in_archive"
        print(f"  week_ending={week} | release={alt_iso} {day} {time_} | {holiday} | {status}")


if __name__ == "__main__":
    main()

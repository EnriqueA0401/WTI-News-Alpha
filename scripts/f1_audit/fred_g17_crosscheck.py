"""F1 auditoría FRED: contrasta fechas de release G.17 de FRED (release 13) con la Fed.

Compara ``data/raw/fred/api/release_13/dates_2015.json`` con las fechas listadas en
``data/raw/fred/official/fed_g17_release_dates_*.html`` (página oficial de fechas históricas),
y las fechas de vintage de INDPRO desde 2015. Solo lee ficheros raw.
"""

import html
import json
import re
from datetime import UTC, datetime

from wti_news_alpha.runlog import REPO_ROOT

RAW = REPO_ROOT / "data/raw/fred"
DATE_RE = re.compile(r"\b(\d{1,2})-([A-Z][a-z]+)-(\d{4})\b")


def main() -> None:
    page = sorted((RAW / "official").glob("fed_g17_release_dates_*.html"))[-1]
    text = html.unescape(re.sub(r"<[^>]+>", " ", page.read_text(encoding="utf-8")))
    fed = set()
    for d, mon, y in DATE_RE.findall(text):
        fed.add(
            datetime.strptime(f"{d}-{mon}-{y}", "%d-%B-%Y").replace(tzinfo=UTC).date().isoformat()
        )
    today = "2026-10-15"
    fed15 = {d for d in fed if "2015-01-01" <= d <= today}
    fred = {
        r["date"]
        for r in json.loads((RAW / "api/release_13/dates_2015.json").read_text())["release_dates"]
    }
    vint = {
        v
        for v in json.loads((RAW / "api/INDPRO/vintagedates.json").read_text())["vintage_dates"]
        if v >= "2015-01-01"
    }
    print(f"fed_page={page.name} fed_dates_2015_to_today={len(fed15)} fred_release13={len(fred)}")
    print("in_fed_not_fred", sorted(fed15 - fred))
    print("in_fred_not_fed", sorted(fred - fed15))
    print("indpro_vintages_not_in_fed", sorted(vint - fed15))


if __name__ == "__main__":
    main()

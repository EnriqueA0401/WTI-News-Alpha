"""F1 auditoría EIA: descarga una muestra de informes WPSR archivados (semanas con festivo).

Para cada fecha de publicación de la lista guarda en el almacén raw (fuente ``eia``):
la página del archivo, ``pdf/highlights.pdf`` y ``csv/table1.csv`` (balance EE. UU.). Luego
extrae de la página ``Data for week ending`` y ``Release Date`` y lo imprime.
Uso: uv run python scripts/f1_audit/eia_archive_sample.py [--pdf] [--csv] [FECHA ...]
"""

import html
import re
import sys
from datetime import date

import httpx
from eia_fetch import UA, fetch  # type: ignore[import-not-found]

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

BASE = "https://www.eia.gov/petroleum/supply/weekly/archive"
SAMPLE = [
    # 2015
    "2015-01-07", "2015-01-22", "2015-02-19", "2015-05-28", "2015-07-08", "2015-09-10",
    "2015-11-25", "2015-12-30",
    # 2019
    "2019-01-04", "2019-01-24", "2019-02-21", "2019-05-30", "2019-07-03", "2019-09-05",
    "2019-11-27", "2019-12-27",
    # 2022 (semana sin informe en el índice)
    "2022-06-15", "2022-06-29",
    # 2023
    "2023-01-05", "2023-01-19", "2023-02-23", "2023-06-01", "2023-07-06", "2023-09-07",
    "2023-11-15", "2023-11-22", "2023-12-28",
    # 2025 (Navidad: lunes 17:00 según calendario vigente)
    "2025-12-29", "2025-12-31",
]  # fmt: skip
WEEK_RE = re.compile(r"Data for week ending\s*([A-Z][a-z]+ \d{1,2}, \d{4})")
REL_RE = re.compile(r"Release Date:\s*([A-Z][a-z]+ \d{1,2}, \d{4})")


def page_rel(d: date) -> str:
    return f"eia/web/archive/wpsr_{d:%Y_%m_%d}.php.html"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dates = [date.fromisoformat(s) for s in (args or SAMPLE)]
    store = RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit())
    with httpx.Client(timeout=90, follow_redirects=True, headers=UA) as client:
        for d in dates:
            folder = f"{BASE}/{d:%Y}/{d:%Y_%m_%d}"
            fetch(client, store, page_rel(d), f"{folder}/wpsr_{d:%Y_%m_%d}.php", {})
            if "--pdf" in sys.argv:
                rel = f"eia/web/archive/{d:%Y_%m_%d}/highlights.pdf"
                fetch(client, store, rel, f"{folder}/pdf/highlights.pdf", {})
            if "--csv" in sys.argv:
                rel = f"eia/web/archive/{d:%Y_%m_%d}/table1.csv"
                fetch(client, store, rel, f"{folder}/csv/table1.csv", {})
    for d in dates:
        raw = (REPO_ROOT / "data/raw" / page_rel(d)).read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))
        wk = WEEK_RE.search(text)
        rl = REL_RE.search(text)
        print(
            f"{d}\t{d:%a}\tweek_ending={wk.group(1) if wk else 'NA'}"
            f"\trelease_date={rl.group(1) if rl else 'NA'}"
        )


if __name__ == "__main__":
    main()

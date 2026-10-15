"""F1 auditoría EIA: fechas de publicación WPSR implícitas en el índice del archivo oficial.

Lee ``data/raw/eia/web/wpsr_archive_index_*.html`` y extrae los enlaces
``/petroleum/supply/weekly/archive/YYYY/YYYY_MM_DD/...``. La carpeta ``YYYY_MM_DD`` se
interpreta como fecha de publicación (hipótesis a validar con el contenido de cada informe).
Imprime conteos por año, días de la semana, fechas no-miércoles y semanas sin informe.
Con ``--csv`` escribe la lista completa a la salida estándar.
"""

import re
import sys
from collections import Counter
from datetime import date, timedelta

from wti_news_alpha.runlog import REPO_ROOT

LINK_RE = re.compile(
    r'href="(/petroleum/supply/weekly/archive/(\d{4})/(\d{4})_(\d{2})_(\d{2})/[^"]*)"'
)
START = date(2015, 1, 1)


def main() -> None:
    path = sorted((REPO_ROOT / "data/raw/eia/web").glob("wpsr_archive_index_*.html"))[-1]
    text = path.read_text(encoding="utf-8", errors="replace")
    links: dict[date, list[str]] = {}
    for m in LINK_RE.finditer(text):
        d = date(int(m.group(3)), int(m.group(4)), int(m.group(5)))
        links.setdefault(d, []).append(m.group(1))
    dates = sorted(d for d in links if d >= START)
    if "--csv" in sys.argv:
        for d in dates:
            print(f"{d},{d.strftime('%a')},{';'.join(sorted(set(links[d])))}")
        return
    print(f"file={path.name} all_dates={len(links)} since_2015={len(dates)}")
    print(f"first={dates[0]} last={dates[-1]}")
    print("per_year", dict(Counter(d.year for d in dates)))
    print("weekday", dict(Counter(d.strftime("%a") for d in dates)))
    non_wed = [f"{d}({d.strftime('%a')})" for d in dates if d.weekday() != 2]
    print(f"non_wednesday n={len(non_wed)}", non_wed)
    # Semanas ISO (lunes) sin ningún informe
    weeks = {d - timedelta(days=d.weekday()) for d in dates}
    w = dates[0] - timedelta(days=dates[0].weekday())
    missing = []
    while w <= dates[-1]:
        if w not in weeks:
            missing.append(w.isoformat())
        w += timedelta(days=7)
    print(f"weeks_without_report n={len(missing)}", missing)
    multi = [d.isoformat() for d in dates if len(set(links[d])) > 1]
    print("dates_with_multiple_links", multi[:20])
    print("link_suffixes", dict(Counter(links[d][0].rsplit(".", 1)[-1] for d in dates)))


if __name__ == "__main__":
    main()

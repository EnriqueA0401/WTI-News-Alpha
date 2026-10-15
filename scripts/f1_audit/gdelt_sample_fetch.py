"""F1: descarga una muestra estratificada de ficheros GDELT 2.0 al almacén raw.

Diseño de la muestra (fijo, documentado en reports/f1/gdelt.md):
- Por año 2015-2026: días 10 de mar/jun/sep/dic (los futuros se omiten),
  slots 08:00 y 15:00 UTC; tipos gkg, export y mentions.
- Bloque consecutivo: 2024-06-10 de 14:00 a 15:45 UTC (8 slots) para medir
  repeticiones de URL entre actualizaciones contiguas.
Los slots ausentes en el índice maestro se registran, no se sustituyen.
"""

import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

MASTER = REPO_ROOT / "data/raw/gdelt/index/masterfilelist_20261015.txt"
KINDS = ("gkg", "export", "mentions")
MAX_BYTES = 60_000_000


def sample_slots(now: datetime) -> list[datetime]:
    slots = []
    for year in range(2015, 2027):
        for month in (3, 6, 9, 12):
            for hour in (8, 15):
                ts = datetime(year, month, 10, hour, 0, tzinfo=UTC)
                if ts < now:
                    slots.append(ts)
    block_start = datetime(2024, 6, 10, 14, 0, tzinfo=UTC)
    slots += [block_start + timedelta(minutes=15 * i) for i in range(8)]
    return sorted(set(slots))


def main() -> None:
    urls: dict[tuple[str, str], str] = {}
    pat = re.compile(r"(\S+/(\d{14})\.(export|mentions|gkg)\.(?:CSV|csv)\.zip)$")
    for line in MASTER.read_text(encoding="utf-8").splitlines():
        m = pat.search(line)
        if m:
            urls[(m.group(2), m.group(3))] = m.group(1).replace("http://", "https://")
    store = RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit())
    missing = []
    total = 0
    with httpx.Client(timeout=300, follow_redirects=True) as c:
        for ts in sample_slots(datetime.now(tz=UTC)):
            key = ts.strftime("%Y%m%d%H%M%S")
            for kind in KINDS:
                url = urls.get((key, kind))
                if url is None:
                    missing.append(f"{key}.{kind}")
                    continue
                rel = f"gdelt/sample/{ts:%Y}/{Path(url).name}"
                e = store.fetch(c, "gdelt", url, rel, max_bytes=MAX_BYTES)
                total += e.n_bytes
                time.sleep(0.3)
    print(f"bytes={total} ausentes_en_indice={len(missing)} {missing}")


if __name__ == "__main__":
    main()

"""F1 (NOAA Storm Events): parsea un listado HTML de directorio (Apache) guardado.

Imprime nombre, última modificación y tamaño; para ficheros StormEvents_* extrae el año
de datos (dYYYY) y el sello de creación (cYYYYMMDD). Uso:
uv run python -I scripts/f1_audit/noaa_listing.py LISTING.html [REGEX_FILTRO]
"""

import re
import sys
from pathlib import Path

ROW = re.compile(
    r'<td><a href="([^"]+)">[^<]*</a></td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>', re.S
)
NAME = re.compile(r"StormEvents_(\w+)-ftp_v1\.0_d(\d{4})_c(\d{8})")


def main() -> None:
    text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    filt = re.compile(sys.argv[2]) if len(sys.argv) > 2 else None
    for href, mod, size in ROW.findall(text):
        if filt and not filt.search(href):
            continue
        m = NAME.search(href)
        extra = f"\t{m.group(1)}\td{m.group(2)}\tc{m.group(3)}" if m else ""
        print(f"{href}\t{mod.strip()}\t{size.strip()}{extra}")


if __name__ == "__main__":
    main()

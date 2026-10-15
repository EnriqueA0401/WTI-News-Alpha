"""F1 (CFTC): imprime el texto visible de un HTML guardado (sin scripts/estilos).

Uso: uv run python -I scripts/f1_audit/cftc_htmltext.py FICHERO.htm [--links]
"""

import html
import re
import sys
from pathlib import Path


def visible_text(raw: str) -> str:
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", raw, flags=re.S | re.I)
    body = re.sub(r"<(br|p|tr|li|h\d|div)[^>]*>", "\n", body, flags=re.I)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", body))
    txt = re.sub(r"[ \t\r\f\v]+", " ", txt)
    return re.sub(r"\n\s*\n+", "\n", txt)


def main() -> None:
    raw = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
    if "--links" in sys.argv:
        for href, label in re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', raw, flags=re.S | re.I):
            print(href, "|", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", label)).strip())
    else:
        print(visible_text(raw))


if __name__ == "__main__":
    main()

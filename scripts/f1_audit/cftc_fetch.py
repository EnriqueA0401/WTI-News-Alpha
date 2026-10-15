"""F1 (CFTC COT): descarga URLs al RawStore inmutable con pausa >= 1 s entre peticiones.

Uso: uv run python scripts/f1_audit/cftc_fetch.py URL REL_PATH [URL REL_PATH ...]
REL_PATH es relativo a data/raw/ y debe empezar por "cftc/". Imprime ruta, bytes y sha256.
"""

import sys
import time

import httpx

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

SOURCE = "cftc"
PAUSE_S = 1.2


def main() -> None:
    args = sys.argv[1:]
    if not args or len(args) % 2:
        raise SystemExit("uso: cftc_fetch.py URL REL_PATH [URL REL_PATH ...]")
    store = RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit())
    headers = {"User-Agent": "WTI-News-Alpha research audit (low volume)"}
    with httpx.Client(timeout=120, follow_redirects=True, headers=headers) as client:
        for url, rel in zip(args[::2], args[1::2], strict=True):
            if not rel.startswith(f"{SOURCE}/"):
                raise SystemExit(f"rel_path debe empezar por {SOURCE}/: {rel}")
            known = rel in store.entries(SOURCE)
            e = store.fetch(client, SOURCE, url, rel)
            print(f"{e.path}\t{e.n_bytes}\t{e.sha256[:12]}\t{'cache' if known else 'new'}")
            if not known:
                time.sleep(PAUSE_S)


if __name__ == "__main__":
    main()

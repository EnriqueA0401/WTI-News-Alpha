"""F1: descarga el índice maestro de GDELT 2.0 (inglés) al almacén raw (una vez)."""

import httpx

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

URL = "https://data.gdeltproject.org/gdeltv2/masterfilelist.txt"


def main() -> None:
    store = RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit())
    with httpx.Client(timeout=600, follow_redirects=True) as c:
        e = store.fetch(c, "gdelt", URL, "gdelt/index/masterfilelist_20261015.txt")
    print(e.path, e.n_bytes, e.sha256)


if __name__ == "__main__":
    main()

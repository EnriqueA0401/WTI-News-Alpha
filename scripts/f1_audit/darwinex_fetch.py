"""Descarga el fichero Darwinex XTI M1 (SQX) compartido por el usuario en Google Drive.

Uso: uv run python scripts/f1_audit/darwinex_fetch.py FILE_ID REL_PATH
Se guarda una sola vez en data/raw/<REL_PATH> con manifiesto (fuente ``darwinex``).
El enlace es privado del usuario: el ID no es un secreto, pero el fichero no se redistribuye.
"""

import sys

import httpx

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

URL = "https://drive.usercontent.google.com/download"


def main() -> None:
    file_id, rel = sys.argv[1:3]
    store = RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit())
    with httpx.Client(timeout=600, follow_redirects=True) as client:
        e = store.fetch(
            client,
            "darwinex",
            URL,
            rel,
            params={"id": file_id, "export": "download", "confirm": "t"},
            max_bytes=2_000_000_000,
        )
    print(f"{e.path}\t{e.n_bytes}\t{e.sha256[:16]}\t{e.content_type}")


if __name__ == "__main__":
    main()

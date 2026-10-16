"""F2: descarga el historial de vintages ALFRED (output_type=1) por ventanas anuales.

Series diarias de contexto (FASE_02_fundamentales.md §C): DGS10, DGS2, T10YIE, VIXCLS,
DTWEXBGS. Ventanas de tiempo real de un año para no superar el límite de 2000 vintages.
Raw inmutable con manifiesto (``manifests/fred.jsonl``); la clave se redacta. Árbol limpio.
Uso: set -a; . ~/.config/wna/secrets.env; set +a; uv run python -I scripts/f2/fred_vintages_fetch.py
"""

import os

import httpx

from wti_news_alpha.errors import RawIntegrityError, SourceAccessError
from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

SERIES = ("DGS10", "DGS2", "T10YIE", "VIXCLS", "DTWEXBGS")
FIRST_YEAR, LAST_YEAR = 2015, 2026
LAST_DAY = "2026-10-16"
URL = "https://api.stlouisfed.org/fred/series/observations"


def main() -> None:
    commit = current_commit()
    if commit.endswith("-dirty"):
        raise RawIntegrityError(f"árbol git dirty ({commit}): commitea antes de descargar")
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise SourceAccessError("falta FRED_API_KEY en el entorno")
    store = RawStore(REPO_ROOT / "data" / "raw", REPO_ROOT / "manifests", commit)
    with httpx.Client(timeout=120) as client:
        for sid in SERIES:
            for year in range(FIRST_YEAR, LAST_YEAR + 1):
                end = LAST_DAY if year == LAST_YEAR else f"{year}-12-31"
                params = {
                    "series_id": sid,
                    "api_key": key,
                    "file_type": "json",
                    "realtime_start": f"{year}-01-01",
                    "realtime_end": end,
                    "observation_start": "2014-01-01",
                    "output_type": "1",
                }
                e = store.fetch(
                    client,
                    "fred",
                    URL,
                    f"fred/api/{sid}/vintages_rt{year}_20261016.json",
                    params=params,
                    secret_params=["api_key"],
                    max_bytes=50_000_000,
                )
                print(e.path, e.n_bytes)


if __name__ == "__main__":
    main()

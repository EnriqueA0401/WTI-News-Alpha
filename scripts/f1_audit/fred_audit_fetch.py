"""F1 auditoría FRED/ALFRED: descarga metadatos, vintages, releases y observaciones.

Por serie candidata guarda en ``data/raw/fred/api/<SERIE>/``:
- ``series.json`` (fred/series), ``vintagedates.json`` (fred/series/vintagedates, todas),
- ``release.json`` (fred/series/release),
- ``obs_latest_2015.json`` (observaciones actuales desde 2015-01-01),
- ``obs_initial_2015.json`` (output_type=4: valor de la primera publicación; solo si la serie
  tiene <= 2000 vintages, límite de la API),
- ``obs_asof_2020-01-02.json`` (foto as-of: realtime_start = realtime_end = 2020-01-02).
Por release: ``data/raw/fred/api/release_<id>/dates_2015.json`` (fred/release/dates).
Cortesía <= 1 req/s (ver fred_fetch.fetch).
"""

import json

import httpx
from fred_fetch import UA, fetch  # type: ignore[import-not-found]

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

API = "https://api.stlouisfed.org/fred"
SERIES = [
    "DCOILWTICO", "DTWEXBGS", "DGS10", "DGS2", "T10YIE", "VIXCLS",
    "INDPRO", "PAYEMS", "CPIAUCSL", "UNRATE", "ICSA",
]  # fmt: skip
OBS_START = "2015-01-01"
MAX_VINTAGES = 2000  # límite de ALFRED para output_type=4 (respuesta HTTP 400 observada)
ASOF = "2020-01-02"  # foto as-of para medir revisiones de 2015-2019
ALL_RT = {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}


def main() -> None:
    store = RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit())
    releases: set[int] = set()
    with httpx.Client(timeout=90, follow_redirects=True, headers=UA) as client:
        for sid in SERIES:
            base = f"fred/api/{sid}"
            fetch(client, store, f"{base}/series.json", f"{API}/series", {"series_id": sid})
            fetch(
                client,
                store,
                f"{base}/vintagedates.json",
                f"{API}/series/vintagedates",
                {"series_id": sid, "limit": "10000", **ALL_RT},
            )
            fetch(
                client, store, f"{base}/release.json", f"{API}/series/release", {"series_id": sid}
            )
            fetch(
                client,
                store,
                f"{base}/obs_latest_2015.json",
                f"{API}/series/observations",
                {"series_id": sid, "observation_start": OBS_START},
            )
            vint = json.loads((REPO_ROOT / "data/raw" / base / "vintagedates.json").read_text())
            if int(vint["count"]) <= MAX_VINTAGES:
                fetch(
                    client,
                    store,
                    f"{base}/obs_initial_2015.json",
                    f"{API}/series/observations",
                    {
                        "series_id": sid,
                        "observation_start": OBS_START,
                        "output_type": "4",
                        **ALL_RT,
                    },
                )
            if vint["vintage_dates"][0] <= ASOF:
                fetch(
                    client,
                    store,
                    f"{base}/obs_asof_{ASOF}.json",
                    f"{API}/series/observations",
                    {
                        "series_id": sid,
                        "observation_start": OBS_START,
                        "realtime_start": ASOF,
                        "realtime_end": ASOF,
                    },
                )
            rel = json.loads((REPO_ROOT / "data/raw" / base / "release.json").read_text())
            releases.update(int(r["id"]) for r in rel["releases"])
        for rid in sorted(releases):
            fetch(
                client,
                store,
                f"fred/api/release_{rid}/dates_2015.json",
                f"{API}/release/dates",
                {
                    "release_id": str(rid),
                    "realtime_start": OBS_START,
                    "realtime_end": "9999-12-31",
                    "include_release_dates_with_no_data": "false",
                    "limit": "10000",
                    "sort_order": "asc",
                },
            )
            fetch(
                client,
                store,
                f"fred/api/release_{rid}/release.json",
                f"{API}/release",
                {"release_id": str(rid)},
            )


if __name__ == "__main__":
    main()

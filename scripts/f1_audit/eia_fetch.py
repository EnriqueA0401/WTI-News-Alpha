"""F1 auditoría EIA: descarga una URL al almacén raw inmutable (fuente ``eia``).

Uso:
    uv run python scripts/f1_audit/eia_fetch.py REL_PATH URL [clave=valor ...]

Si la URL es de ``api.eia.gov`` se añade ``api_key`` desde ``EIA_API_KEY`` (redactada en
el manifiesto). Espera 1,1 s tras cada petición de red (cortesía: <= 1 req/s).
Imprime solo ruta, bytes y sha256 (nunca la clave).
"""

import os
import sys
import time

import httpx

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

SOURCE = "eia"
UA = {"User-Agent": "WTI-News-Alpha research audit (non-commercial)"}


def fetch(
    client: httpx.Client, store: RawStore, rel: str, url: str, params: dict[str, str]
) -> None:
    """Descarga ``url`` a ``data/raw/<rel>`` (idempotente) e imprime un resumen."""
    secret: tuple[str, ...] = ()
    if "api.eia.gov" in url:
        params = {"api_key": os.environ["EIA_API_KEY"], **params}
        secret = ("api_key",)
    known = store.entries(SOURCE).get(rel)
    e = store.fetch(client, SOURCE, url, rel, params=params, secret_params=secret)
    print(f"{rel}\t{e.n_bytes}\t{e.sha256[:12]}\t{'cache' if known else 'new'}")
    if known is None:
        time.sleep(1.1)


def main() -> None:
    rel, url, *kv = sys.argv[1:]
    params = dict(item.split("=", 1) for item in kv)
    store = RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit())
    with httpx.Client(timeout=90, follow_redirects=True, headers=UA) as client:
        fetch(client, store, rel, url, params)


if __name__ == "__main__":
    main()

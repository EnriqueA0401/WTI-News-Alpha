"""F2: descarga las fuentes oficiales del calendario federal de días hábiles.

- 5 U.S.C. 6103 (festivos federales legales), edición 2023 en govinfo.gov.
- Órdenes ejecutivas de cierre de departamentos federales (Federal Register API),
  firmadas desde 2014-01-01.
Raw inmutable con manifiesto (``manifests/federal_calendar.jsonl``). Exige árbol limpio.
Uso: uv run python -I scripts/f2/federal_calendar_fetch.py
"""

import httpx

from wti_news_alpha.errors import RawIntegrityError
from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

SOURCE = "federal_calendar"
STATUTE_URL = (
    "https://www.govinfo.gov/content/pkg/USCODE-2023-title5/html/"
    "USCODE-2023-title5-partIII-subpartE-chap61-subchapI-sec6103.htm"
)
FR_URL = "https://www.federalregister.gov/api/v1/documents.json"
FR_PARAMS = {
    "conditions[term]": '"closing of executive departments"',
    "conditions[type][]": "PRESDOCU",
    "conditions[presidential_document_type][]": "executive_order",
    "conditions[signing_date][gte]": "2014-01-01",
    "per_page": "100",
    "order": "oldest",
}
FR_FIELDS = [
    "title",
    "signing_date",
    "executive_order_number",
    "document_number",
    "publication_date",
    "html_url",
    "abstract",
]


def main() -> None:
    commit = current_commit()
    if commit.endswith("-dirty"):
        raise RawIntegrityError(f"árbol git dirty ({commit}): commitea antes de descargar")
    store = RawStore(REPO_ROOT / "data" / "raw", REPO_ROOT / "manifests", commit)
    url = httpx.URL(FR_URL).copy_merge_params(FR_PARAMS)
    for field in FR_FIELDS:
        url = url.copy_add_param("fields[]", field)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        e1 = store.fetch(client, SOURCE, STATUTE_URL, "federal_calendar/usc5_6103_2023.htm")
        e2 = store.fetch(
            client, SOURCE, str(url), "federal_calendar/fr_closing_eos_2014plus_20261016.json"
        )
    for e in (e1, e2):
        print(e.path, e.n_bytes, e.sha256[:12])


if __name__ == "__main__":
    main()

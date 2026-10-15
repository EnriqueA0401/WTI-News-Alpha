"""F1: auditoría de Databento GLBX.MDP3 usando SOLO endpoints gratuitos.

Endpoints usados (gratuitos según https://databento.com/docs/api-reference-historical,
sección "Metered pricing": "You will only be billed for usage of time series data. Access to
metadata, symbology, and account management is free."):
  metadata.list_datasets / list_schemas / list_publishers / list_fields / list_unit_prices /
  get_dataset_range / get_dataset_condition / get_record_count / get_billable_size / get_cost,
  y symbology.resolve ("This conversion can be done, for no cost").
NUNCA se llama a timeseries.get_range ni batch.*.

Cada respuesta se guarda en el almacén crudo inmutable (data/raw/databento_meta/...) con
manifiesto en manifests/databento_meta.jsonl. La clave se lee de DATABENTO_API_KEY y solo
viaja como usuario de HTTP basic auth; nunca se imprime ni se guarda.

Uso: uv run python scripts/f1_audit/databento_meta.py {base,docs,quotes,resolve}
"""

import argparse
import json
import os
import re
from datetime import UTC, datetime

import httpx

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

SOURCE = "databento_meta"
API = "https://hist.databento.com/v0/"
DATASET = "GLBX.MDP3"
STUDY_START = "2015-02-18T00:00:00Z"  # inicio de GDELT 2.0 (PROTOCOL A7)
PARENT = "CL.FUT"
MONTH_CODES = "FGHJKMNQUVXZ"
# CME usa año de 1 dígito (CLZ5) y, en contratos listados recientemente, de 2 dígitos
# (venues-and-datasets, GLBX.MDP3 > Symbology). Se cubren ambos formatos.
OUTRIGHTS = [f"CL{m}{y}" for m in MONTH_CODES for y in range(10)] + [
    f"CL{m}{y:02d}" for m in MONTH_CODES for y in range(10, 46)
]
DOC_PAGES = {
    # Páginas de contenido que carga el SPA de docs (la URL pública es JS).
    "historical_http": "https://databento.com/docs/03-00-00-reference-historical-http.html",
    "schemas": "https://databento.com/docs/08-00-00-schemas-and-data-formats.html",
    "standards": "https://databento.com/docs/09-00-00-standards-and-conventions.html",
    "venues": "https://databento.com/docs/12-00-00-venues-and-datasets.html",
    "portal": "https://databento.com/docs/14-00-00-portal.html",
    # Chunk JS de https://databento.com/pricing que contiene el texto de las FAQ.
    "pricing_faq_js": "https://databento.com/assets/Pricing-Bvr9F8V9.js",
    "dataset_page": "https://databento.com/datasets/GLBX.MDP3",
}


def _auth() -> tuple[str, str]:
    key = os.environ.get("DATABENTO_API_KEY", "")
    if not key:
        raise SystemExit("DATABENTO_API_KEY no está en el entorno")
    return (key, "")


def _store() -> RawStore:
    return RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit())


def _get(store: RawStore, c: httpx.Client, endpoint: str, rel: str, params: dict[str, str]) -> str:
    e = store.fetch(c, SOURCE, API + endpoint, f"{SOURCE}/{rel}", params=params, basic_auth=_auth())
    path = REPO_ROOT / "data/raw" / e.path
    print(f"{e.path}\t{e.n_bytes} B\tsha256={e.sha256[:12]}")
    return path.read_text(encoding="utf-8")


def stage_base(store: RawStore, c: httpx.Client) -> None:
    _get(store, c, "metadata.list_datasets", "list_datasets.json", {})
    _get(store, c, "metadata.list_schemas", "list_schemas_glbx.json", {"dataset": DATASET})
    _get(store, c, "metadata.list_publishers", "list_publishers.json", {})
    _get(store, c, "metadata.get_dataset_range", "dataset_range_glbx.json", {"dataset": DATASET})
    _get(
        store,
        c,
        "metadata.get_dataset_condition",
        "dataset_condition_glbx_2015-02-18_last.json",
        {"dataset": DATASET, "start_date": "2015-02-18"},
    )
    _get(store, c, "metadata.list_unit_prices", "unit_prices_glbx.json", {"dataset": DATASET})
    for schema in ("ohlcv-1m", "definition", "statistics", "status"):
        _get(
            store,
            c,
            "metadata.list_fields",
            f"list_fields_{schema}_dbn.json",
            {"schema": schema, "encoding": "dbn"},
        )


def stage_docs(store: RawStore, c: httpx.Client) -> None:
    for name, url in DOC_PAGES.items():
        ext = url.rsplit(".", 1)[-1] if url.endswith((".html", ".js")) else "html"
        e = store.fetch(c, SOURCE, url, f"{SOURCE}/docs/2026-10-15_{name}.{ext}")
        print(f"{e.path}\t{e.n_bytes} B")


def _schema_end_midnight(schema: str) -> str:
    """Fin del rango del schema, truncado a medianoche UTC (las cuotas son exactas solo
    para múltiplos de 10 min y, en ``definition``, de 24 h)."""
    raw = (REPO_ROOT / "data/raw" / SOURCE / "dataset_range_glbx.json").read_text("utf-8")
    end = json.loads(raw)["schema"][schema]["end"]
    d = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(UTC)
    return d.strftime("%Y-%m-%dT00:00:00Z")


def _schema_start(schema: str) -> str:
    raw = (REPO_ROOT / "data/raw" / SOURCE / "dataset_range_glbx.json").read_text("utf-8")
    start = json.loads(raw)["schema"][schema]["start"]
    return max(start.replace(".000000000Z", "Z"), STUDY_START)


def _quote(  # noqa: PLR0913 - parámetros explícitos de la petición
    store: RawStore,
    c: httpx.Client,
    tag: str,
    schema: str,
    span: tuple[str, str],
    *,
    outrights: bool = False,
    endpoints: tuple[str, ...] = ("get_cost", "get_billable_size", "get_record_count"),
) -> None:
    params = {
        "dataset": DATASET,
        "schema": schema,
        "start": span[0],
        "end": span[1],
        "stype_in": "raw_symbol" if outrights else "parent",
        "symbols": ",".join(OUTRIGHTS) if outrights else PARENT,
    }
    universe = "outrights" if outrights else "clfut"
    for ep in endpoints:
        _get(store, c, f"metadata.{ep}", f"quotes/{ep}_{universe}_{schema}_{tag}.json", params)


def stage_quotes(store: RawStore, c: httpx.Client) -> None:
    for schema in ("ohlcv-1m", "ohlcv-1h", "ohlcv-1d", "definition", "statistics", "status"):
        full = (_schema_start(schema), _schema_end_midnight(schema))
        for outr in (False, True):
            _quote(store, c, "full", schema, full, outrights=outr)
    m1_end = _schema_end_midnight("ohlcv-1m")
    for outr in (False, True):
        _quote(
            store,
            c,
            "2024-03",
            "ohlcv-1m",
            ("2024-03-01T00:00:00Z", "2024-04-01T00:00:00Z"),
            outrights=outr,
        )
        _quote(
            store,
            c,
            "2024",
            "ohlcv-1m",
            ("2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            outrights=outr,
        )
        for y in range(2015, 2027):
            start = STUDY_START if y == 2015 else f"{y}-01-01T00:00:00Z"
            end = f"{y + 1}-01-01T00:00:00Z" if y < 2026 else m1_end
            _quote(store, c, f"y{y}", "ohlcv-1m", (start, end), outrights=outr)
    # Esquemas de mayor resolución, solo como referencia de coste (1 mes y 1 año).
    for schema in ("trades", "tbbo", "mbp-1", "ohlcv-1s"):
        _quote(
            store,
            c,
            "2024-03",
            schema,
            ("2024-03-01T00:00:00Z", "2024-04-01T00:00:00Z"),
            outrights=True,
        )
        _quote(
            store,
            c,
            "2024",
            schema,
            ("2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            outrights=True,
            endpoints=("get_cost", "get_billable_size"),
        )


def stage_resolve(store: RawStore, c: httpx.Client) -> None:
    """symbology.resolve de un solo día (gratuito según docs). La docs lo describe como POST;
    se intenta GET porque el almacén crudo solo hace GET. Si falla, se informa."""
    day = {"start_date": "2024-03-01", "end_date": "2024-03-02"}
    _get(
        store,
        c,
        "symbology.resolve",
        "resolve_clfut_parent_to_iid_2024-03-01.json",
        {
            "dataset": DATASET,
            "symbols": PARENT,
            "stype_in": "parent",
            "stype_out": "instrument_id",
            **day,
        },
    )
    _get(
        store,
        c,
        "symbology.resolve",
        "resolve_outrights_raw_to_iid_2024-03-01.json",
        {
            "dataset": DATASET,
            "symbols": ",".join(OUTRIGHTS),
            "stype_in": "raw_symbol",
            "stype_out": "instrument_id",
            **day,
        },
    )


def _resolved(rel: str) -> set[str]:
    raw = (REPO_ROOT / "data/raw" / SOURCE / rel).read_text("utf-8")
    return {k for k, v in json.loads(raw)["result"].items() if v}


def stage_probe(store: RawStore, c: httpx.Client) -> None:
    """Compara outrights de CL.FUT (parent) con la lista explícita de símbolos raw en un día
    de 2015 (feed legado FIX/FAST) y mide la actividad de los símbolos que resuelven por raw
    pero no están en CL.FUT."""
    outright_re = re.compile(r"^CL[FGHJKMNQUVXZ]\d{1,2}$")
    for day, nxt in (("2015-06-01", "2015-06-02"), ("2024-03-01", "2024-03-02")):
        if day != "2024-03-01":
            span = {"start_date": day, "end_date": nxt}
            _get(
                store,
                c,
                "symbology.resolve",
                f"resolve_clfut_parent_to_iid_{day}.json",
                {
                    "dataset": DATASET,
                    "symbols": PARENT,
                    "stype_in": "parent",
                    "stype_out": "instrument_id",
                    **span,
                },
            )
            _get(
                store,
                c,
                "symbology.resolve",
                f"resolve_outrights_raw_to_iid_{day}.json",
                {
                    "dataset": DATASET,
                    "symbols": ",".join(OUTRIGHTS),
                    "stype_in": "raw_symbol",
                    "stype_out": "instrument_id",
                    **span,
                },
            )
        parent = {
            k for k in _resolved(f"resolve_clfut_parent_to_iid_{day}.json") if outright_re.match(k)
        }
        extras = sorted(_resolved(f"resolve_outrights_raw_to_iid_{day}.json") - parent)
        print(f"{day}: outrights en CL.FUT={len(parent)}; solo por raw={len(extras)}")
        if not extras:
            continue
        month = day[:7]
        y, m = int(month[:4]), int(month[5:])
        end = f"{y + (m == 12)}-{(m % 12) + 1:02d}-01T00:00:00Z"
        params = {
            "dataset": DATASET,
            "schema": "ohlcv-1m",
            "start": f"{month}-01T00:00:00Z",
            "end": end,
            "stype_in": "raw_symbol",
            "symbols": ",".join(extras),
        }
        for ep in ("get_record_count", "get_cost"):
            _get(store, c, f"metadata.{ep}", f"probe/{ep}_rawonly_ohlcv-1m_{month}.json", params)


def stage_years(store: RawStore, c: httpx.Client) -> None:
    """symbology.resolve de CL.FUT un día por año (primer martes de julio): comprueba que todo
    outright del parent encaja en el patrón de la lista explícita OUTRIGHTS."""
    for y in range(2015, 2027):
        d = datetime(y, 7, 1, tzinfo=UTC)
        while d.weekday() != 1:
            d = d.replace(day=d.day + 1)
        day = d.strftime("%Y-%m-%d")
        nxt = d.replace(day=d.day + 1).strftime("%Y-%m-%d")
        _get(
            store,
            c,
            "symbology.resolve",
            f"resolve_years/resolve_clfut_parent_to_iid_{day}.json",
            {
                "dataset": DATASET,
                "symbols": PARENT,
                "stype_in": "parent",
                "stype_out": "instrument_id",
                "start_date": day,
                "end_date": nxt,
            },
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["base", "docs", "quotes", "resolve", "probe", "years"])
    a = ap.parse_args()
    store = _store()
    stages = {
        "years": stage_years,
        "probe": stage_probe,
        "base": stage_base,
        "docs": stage_docs,
        "quotes": stage_quotes,
        "resolve": stage_resolve,
    }
    with httpx.Client(timeout=120, follow_redirects=True) as c:
        stages[a.stage](store, c)


if __name__ == "__main__":
    main()

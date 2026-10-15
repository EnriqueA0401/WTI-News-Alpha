"""F1: resume las respuestas de metadatos de Databento guardadas en data/raw/databento_meta.

No toca la red. Verifica checksums contra el manifiesto y emite tablas Markdown (stdout)
con cada cifra acompañada de la ruta del fichero crudo del que sale.

Uso: uv run python scripts/f1_audit/databento_summarize.py
"""

import json
import re
from collections import Counter
from datetime import date, timedelta

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

SOURCE = "databento_meta"
RAW = REPO_ROOT / "data/raw" / SOURCE
OUTRIGHT_RE = re.compile(r"^CL[FGHJKMNQUVXZ]\d{1,2}$")
GB = 1e9


def _load(rel: str) -> object:
    return json.loads((RAW / rel).read_text(encoding="utf-8"))


def _q(universe: str, schema: str, tag: str) -> tuple[float, int, int | None, str]:
    base = f"quotes/{{}}_{universe}_{schema}_{tag}.json"
    cost = float(_load(base.format("get_cost")))  # type: ignore[arg-type]
    size = int(_load(base.format("get_billable_size")))  # type: ignore[call-overload]
    cnt_path = RAW / base.format("get_record_count")
    count = int(json.loads(cnt_path.read_text("utf-8"))) if cnt_path.exists() else None
    return cost, size, count, base.format("get_cost")


def _row(label: str, universe: str, schema: str, tag: str) -> str:
    cost, size, count, path = _q(universe, schema, tag)
    cnt = f"{count:,}" if count is not None else "—"
    return (
        f"| {label} | {schema} | {universe} | {cost:,.2f} | {size / GB:,.3f} | {cnt} | `{path}` |"
    )


def section_quotes() -> None:
    print("| Petición | Schema | Universo | Coste USD | GB facturables | Registros | Fuente |")
    print("|---|---|---|---|---|---|---|")
    for schema in ("ohlcv-1m", "ohlcv-1h", "ohlcv-1d", "definition", "statistics", "status"):
        for uni in ("clfut", "outrights"):
            print(_row("rango completo", uni, schema, "full"))
    for tag in ("2024-03", "2024"):
        for uni in ("clfut", "outrights"):
            print(_row(tag, uni, "ohlcv-1m", tag))
    for schema in ("trades", "tbbo", "mbp-1", "ohlcv-1s"):
        for tag in ("2024-03", "2024"):
            print(_row(tag, "outrights", schema, tag))


def section_years() -> None:
    print(
        "\n| Año | ohlcv-1m CL.FUT USD | GB | registros | ohlcv-1m outrights USD | GB | registros |"
    )
    print("|---|---|---|---|---|---|---|")
    tot = Counter[str]()
    for y in range(2015, 2027):
        c1, s1, n1, _ = _q("clfut", "ohlcv-1m", f"y{y}")
        c2, s2, n2, _ = _q("outrights", "ohlcv-1m", f"y{y}")
        tot.update({"c1": round(c1 * 100), "c2": round(c2 * 100)})
        print(
            f"| {y} | {c1:,.2f} | {s1 / GB:,.3f} | {n1:,} | {c2:,.2f} | {s2 / GB:,.3f} | {n2:,} |"
        )
    print(f"| suma | {tot['c1'] / 100:,.2f} | | | {tot['c2'] / 100:,.2f} | | |")
    print(
        "(ficheros: `quotes/get_{cost,billable_size,record_count}_"
        "{clfut,outrights}_ohlcv-1m_yYYYY.json`)"
    )


def section_condition() -> None:
    rel = "dataset_condition_glbx_2015-02-18_last.json"
    cond = _load(rel)
    if not isinstance(cond, list):
        raise TypeError(rel)
    days = {x["date"]: x["condition"] for x in cond}
    print(f"\nCondición ({rel}): {len(days)} fechas, {dict(Counter(days.values()))}")
    first, last = min(days), max(days)
    d, missing = date.fromisoformat(first), Counter[str]()
    while d <= date.fromisoformat(last):
        if d.isoformat() not in days:
            missing[d.strftime("%a")] += 1
        d += timedelta(days=1)
    print(f"Rango {first}..{last}; fechas ausentes por día de semana: {dict(missing)}")
    print("Degradadas:", ", ".join(sorted(k for k, v in days.items() if v == "degraded")))


def section_symbology(day: str) -> None:
    par = _load(f"resolve_clfut_parent_to_iid_{day}.json")
    out = _load(f"resolve_outrights_raw_to_iid_{day}.json")
    if not (isinstance(par, dict) and isinstance(out, dict)):
        raise TypeError("resolve")
    keys = list(par["result"])

    def kind(k: str) -> str:
        if OUTRIGHT_RE.match(k):
            return "outright"
        if k.startswith("CL:"):
            return "UDS/estrategia CL:"
        return "spread (contiene '-')" if "-" in k else "otro"

    kinds = Counter(kind(k) for k in keys)
    print(f"\nCL.FUT el {day}: {len(keys)} instrumentos; por tipo de símbolo: {dict(kinds)}")
    print("Ejemplos 'otro':", sorted(k for k in keys if kind(k) == "otro")[:15])
    par_out = {k for k in keys if OUTRIGHT_RE.match(k)}
    resolved = {k for k, v in out["result"].items() if v}
    print(
        f"Outrights resueltos con la lista explícita: {len(resolved)}; "
        f"outrights dentro de CL.FUT: {len(par_out)}; CL.FUT ⊆ lista: {par_out <= resolved}; "
        f"solo en lista: {sorted(resolved - par_out)}"
    )
    two = sorted(k for k in resolved if len(k) == 5)
    print(f"Con año de 2 dígitos: {len(two)} {two[:3]}…{two[-2:]}")
    print("Ejemplos de spreads:", [k for k in keys if "-" in k][:6])
    month = day[:7]
    probe = RAW / f"probe/get_record_count_rawonly_ohlcv-1m_{month}.json"
    if probe.exists():
        print(
            f"Registros ohlcv-1m de 'solo en lista' en {month}: {probe.read_text('utf-8')} "
            f"(probe/get_record_count_rawonly_ohlcv-1m_{month}.json)"
        )


def section_years_symbology() -> None:
    print("\n| Día (resolve_years/) | Instrumentos CL.FUT | Outrights | 'otro' | Años 2 dígitos |")
    print("|---|---|---|---|---|")
    for f in sorted((RAW / "resolve_years").glob("*.json")):
        res = json.loads(f.read_text("utf-8"))["result"]
        outr = [k for k in res if OUTRIGHT_RE.match(k)]
        other = [
            k for k in res if not OUTRIGHT_RE.match(k) and "-" not in k and not k.startswith("CL:")
        ]
        two = sorted(int(k[3:]) for k in outr if len(k) == 5)
        rng = f"{two[0]}-{two[-1]}" if two else "—"
        print(f"| {f.stem[-10:]} | {len(res)} | {len(outr)} | {other} | {rng} |")


def main() -> None:
    store = RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit())
    n = store.verify_all(SOURCE)
    print(f"Checksums verificados: {n} ficheros en manifests/{SOURCE}.jsonl\n")
    rng = _load("dataset_range_glbx.json")
    if not isinstance(rng, dict):
        raise TypeError("range")
    print(f"Rango GLBX.MDP3 (dataset_range_glbx.json): {rng['start']} → {rng['end']}")
    print("Schemas (list_schemas_glbx.json):", ", ".join(_load("list_schemas_glbx.json")))  # type: ignore[arg-type]
    up = _load("unit_prices_glbx.json")
    if not isinstance(up, list):
        raise TypeError("unit prices")
    hist = next(x for x in up if x["mode"] == "historical")["unit_prices"]
    print("Precio unitario histórico USD/GB (unit_prices_glbx.json):", hist)
    section_condition()
    for day in ("2015-06-01", "2024-03-01"):
        section_symbology(day)
    section_years_symbology()
    print()
    section_quotes()
    section_years()


if __name__ == "__main__":
    main()

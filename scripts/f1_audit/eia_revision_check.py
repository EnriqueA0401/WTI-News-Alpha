"""F1 auditoría EIA: compara valores impresos en WPSR archivados (table1.csv) con la API actual.

Para cada ``data/raw/eia/web/archive/<fecha>/table1.csv`` toma, por serie, las columnas
"semana actual" (primera publicación), "semana previa" (tal como se imprimió una semana después)
y "hace un año" (valor del año anterior tal como constaba en esa publicación) y las compara con
los valores de ``data/raw/eia/api/seriesid_*.json``. Las existencias en table1 están en millones
de barriles (se convierten a miles); los flujos en miles de barriles/día. Solo lee ficheros raw.
"""

import csv
import json
import re
from datetime import UTC, date, datetime

from wti_news_alpha.runlog import REPO_ROOT

RAW = REPO_ROOT / "data/raw/eia"
API_FILES = {
    "WCESTUS1": "seriesid_WCESTUS1_2015-2026.json",
    "WCSSTUS1": "seriesid_WCSSTUS1.W_full.json",
    "WGTSTUS1": "seriesid_WGTSTUS1.W_full.json",
    "WDISTUS1": "seriesid_WDISTUS1.W_full.json",
    "WCRFPUS2": "seriesid_WCRFPUS2.W_full.json",
    "WCRIMUS2": "seriesid_WCRIMUS2.W_full.json",
    "WCREXUS2": "seriesid_WCREXUS2.W_full.json",
}
STOCK_ROWS = {
    "Commercial (Excluding SPR)": "WCESTUS1",
    "Strategic Petroleum Reserve (SPR)": "WCSSTUS1",
    "Total Motor Gasoline": "WGTSTUS1",
    "Distillate Fuel Oil": "WDISTUS1",
}
SUPPLY_ROWS = {
    r"Domestic Production": "WCRFPUS2",
    r"Imports": "WCRIMUS2",
    r"Exports": "WCREXUS2",
}


def num(s: str) -> float:
    return float(s.replace(",", ""))


def mdy(s: str) -> date:
    fmt = "%m/%d/%Y" if len(s.rsplit("/", 1)[-1]) == 4 else "%m/%d/%y"
    return datetime.strptime(s, fmt).replace(tzinfo=UTC).date()


def load_api() -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {}
    for sid, fname in API_FILES.items():
        rows = json.loads((RAW / "api" / fname).read_text(encoding="utf-8"))["response"]["data"]
        out[sid] = {date.fromisoformat(r["period"]): float(r["value"]) for r in rows}
    return out


def parse_table1(path: str) -> dict[str, list[tuple[date, float]]]:
    """Devuelve {serie: [(semana, valor en unidades API), ...]} para actual, previa y año antes."""
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh))
    out: dict[str, list[tuple[date, float]]] = {}
    section = 0
    hdr: list[str] = []
    for r in rows:
        if r and r[0] == "STUB_1":
            section += 1
            hdr = r
            continue
        if section == 1 and r and r[0] in STOCK_ROWS and STOCK_ROWS[r[0]] not in out:
            idx = (1, 2, 5)
            out[STOCK_ROWS[r[0]]] = [(mdy(hdr[i]), num(r[i]) * 1000) for i in idx]
        if section == 2 and len(r) > 2 and r[0].strip() == "Crude Oil Supply":
            label = re.sub(r"^\(\d+\)\s*", "", r[1]).strip()
            sid = SUPPLY_ROWS.get(label)
            if sid and sid not in out:
                out[sid] = [(mdy(hdr[i]), num(r[i])) for i in (2, 3, 5)]
    return out


def main() -> None:
    api = load_api()
    kinds = ("first_print", "prior_week_as_printed", "year_ago_as_printed")
    stats: dict[tuple[str, str], list[float]] = {}
    for f in sorted((RAW / "web/archive").glob("*/table1.csv")):
        parsed = parse_table1(str(f))
        for sid, triples in sorted(parsed.items()):
            for kind, (wk, val) in zip(kinds, triples, strict=True):
                cur = api[sid].get(wk)
                if cur is None:
                    print(f"{f.parent.name}\t{sid}\t{kind}\t{wk}\tNO_API_VALUE")
                    continue
                diff = cur - val
                stats.setdefault((sid, kind), []).append(diff)
                if abs(diff) > 0.5:
                    print(
                        f"{f.parent.name}\t{sid}\t{kind}\t{wk}\tprinted={val}\tapi={cur}\tdiff={diff}"
                    )
    print("--- resumen (diff = API_actual - impreso), unidades API")
    for (sid, kind), diffs in sorted(stats.items()):
        nz = [d for d in diffs if abs(d) > 0.5]
        mx = max(diffs, key=abs)
        print(f"{sid}\t{kind}\tn={len(diffs)}\tn_distintos={len(nz)}\tmax_abs={mx}")


if __name__ == "__main__":
    main()

"""F1 auditoría FRED/ALFRED: resume cobertura, vintages, releases y revisiones medidas.

Solo lee ``data/raw/fred/api``. Para cada serie imprime una línea TSV con:
metadatos (frecuencia, unidades, ajuste estacional, last_updated), cobertura desde 2015-01-01
(nº de observaciones, nº de valores ausentes '.'), vintages (nº, primera, nº desde 2015),
release FRED (id, nombre, enlace), fechas de release desde 2015 (nº, primera, última),
retraso de primera publicación (días entre fecha de observación y realtime_start de la
primera publicación, output_type=4) y revisiones: inicial vs actual y as-of 2020-01-02 vs actual.
"""

import json
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from wti_news_alpha.runlog import REPO_ROOT

RAW = REPO_ROOT / "data/raw/fred/api"
SERIES = [
    "DCOILWTICO", "DTWEXBGS", "DGS10", "DGS2", "T10YIE", "VIXCLS",
    "INDPRO", "PAYEMS", "CPIAUCSL", "UNRATE", "ICSA",
]  # fmt: skip
TOL = 1e-9


def load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def values(obs: list[dict[str, str]]) -> dict[str, float]:
    return {o["date"]: float(o["value"]) for o in obs if o["value"] != "."}


def compare(old: dict[str, float], new: dict[str, float]) -> str:
    common = sorted(set(old) & set(new))
    diffs = [abs(new[d] - old[d]) for d in common]
    nz = [x for x in diffs if x > TOL]
    rel = [
        abs(new[d] - old[d]) / abs(old[d]) for d in common if old[d] and abs(new[d] - old[d]) > TOL
    ]
    return (
        f"n_common={len(common)} n_revised={len(nz)} "
        f"pct_revised={100 * len(nz) / max(len(common), 1):.1f}% "
        f"max_abs={max(nz, default=0):.6g} median_abs_revised={median(nz) if nz else 0:.6g} "
        f"median_rel_revised={100 * median(rel) if rel else 0:.3g}% "
        f"only_old={len(set(old) - set(new))} only_new_in_old_range="
        f"{len([d for d in set(new) - set(old) if old and d <= max(old)])}"
    )


def main() -> None:
    for sid in SERIES:
        d = RAW / sid
        meta = load(d / "series.json")["seriess"][0]
        vint = load(d / "vintagedates.json")["vintage_dates"]
        rel = load(d / "release.json")["releases"][0]
        rdates = load(RAW / f"release_{rel['id']}" / "dates_2015.json")["release_dates"]
        latest = load(d / "obs_latest_2015.json")["observations"]
        nmiss = sum(1 for o in latest if o["value"] == ".")
        out = [
            f"== {sid}: {meta['title']}",
            f"freq={meta['frequency_short']} units={meta['units_short']}"
            f" sa={meta['seasonal_adjustment_short']}"
            f" obs_range={meta['observation_start']}..{meta['observation_end']}"
            f" last_updated={meta['last_updated']}",
            f"obs_since_2015={len(latest)} missing_dot={nmiss}"
            f" first={latest[0]['date']} last={latest[-1]['date']}",
            f"vintages={len(vint)} first_vintage={vint[0]} last_vintage={vint[-1]}"
            f" vintages_since_2015={sum(1 for v in vint if v >= '2015-01-01')}",
            f"release id={rel['id']} name={rel['name']!r} press={rel.get('press_release')}"
            f" link={rel.get('link', '')}",
            f"release_dates_since_2015 n={len(rdates)}"
            f" first={rdates[0]['date'] if rdates else 'NA'}"
            f" last={rdates[-1]['date'] if rdates else 'NA'}",
        ]
        init_p = d / "obs_initial_2015.json"
        if init_p.exists():
            init = load(init_p)["observations"]
            lags = [
                (date.fromisoformat(o["realtime_start"]) - date.fromisoformat(o["date"])).days
                for o in init
                if o["value"] != "."
            ]
            out.append(
                f"first_pub_lag_days: min={min(lags)} median={median(lags)} max={max(lags)}"
                f" n={len(lags)}"
            )
            out.append("initial_vs_latest: " + compare(values(init), values(latest)))
        else:
            out.append("initial_vs_latest: NA (>2000 vintages; output_type=4 rechazado por la API)")
        asof_p = d / "obs_asof_2020-01-02.json"
        if asof_p.exists():
            asof = values(load(asof_p)["observations"])
            out.append("asof_2020-01-02_vs_latest: " + compare(asof, values(latest)))
        print("\n".join(out))
    rwtc_rows = load(REPO_ROOT / "data/raw/eia/api/seriesid_RWTC.D_full.json")["response"]["data"]
    rwtc = {r["period"]: float(r["value"]) for r in rwtc_rows if r["period"] >= "2015-01-01"}
    wti = values(load(RAW / "DCOILWTICO" / "obs_latest_2015.json")["observations"])
    print("== EIA RWTC (API v2) vs FRED DCOILWTICO (actual): " + compare(rwtc, wti))


if __name__ == "__main__":
    main()

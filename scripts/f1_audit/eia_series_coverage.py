"""F1 auditoría EIA: cobertura de las series WPSR/RWTC guardadas en data/raw/eia/api.

Para cada fichero ``seriesid_*.json``: nº de observaciones desde 2015-02-18, primer y último
``period``, unidades, día de la semana de ``period``, huecos (semanas sin dato para series
semanales; días hábiles de lunes a viernes sin dato para la diaria) y valores no numéricos.
Solo lee ficheros raw; no descarga nada.
"""

import json
from collections import Counter
from datetime import date, timedelta
from itertools import pairwise

from wti_news_alpha.runlog import REPO_ROOT

RAW = REPO_ROOT / "data/raw/eia/api"
START = date(2015, 2, 18)


def main() -> None:
    for path in sorted(RAW.glob("seriesid_*.json")):
        resp = json.loads(path.read_text(encoding="utf-8"))["response"]
        rows = resp["data"]
        periods = sorted(date.fromisoformat(r["period"]) for r in rows)
        dup = len(periods) - len(set(periods))
        sel = [p for p in periods if p >= START]
        nonnum = [r["period"] for r in rows if not isinstance(r["value"], int | float)]
        units = Counter(r["units"] for r in rows)
        wd = Counter(p.strftime("%a") for p in sel)
        freq = resp.get("frequency")
        gaps: list[str] = []
        step = timedelta(days=7)
        for a, b in pairwise(sel):
            if freq == "weekly" and b - a != step:
                gaps.append(f"{a}->{b}")
            if freq == "daily":
                d = a + timedelta(days=1)
                while d < b:
                    if d.weekday() < 5:
                        gaps.append(d.isoformat())
                    d += timedelta(days=1)
        desc = rows[0].get("series-description", "")
        print(
            f"{path.name}\tfreq={freq}\ttotal={resp['total']}\tn_since_2015-02-18={len(sel)}"
            f"\tfirst={periods[0]}\tlast={periods[-1]}\tunits={dict(units)}\tweekday={dict(wd)}"
            f"\tdup={dup}\tnonnumeric={nonnum[:5]}\tn_gaps={len(gaps)}\tgaps={gaps[:12]}"
            f"\tdesc={desc}"
        )


if __name__ == "__main__":
    main()

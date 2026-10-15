"""F1 (revisión G0): Last-Modified de ficheros GDELT en casos límite.

Casos: (a) ficheros GKG degradados (< 200 KB, muestra aleatoria fija de 150, semilla 0);
(b) el primer slot disponible tras cada hueco de >= 4 slots (todos los tipos).
Solo HEAD. Escribe reports/f1/gdelt_lastmodified_edge.csv.
"""

import itertools
import random
import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

MASTER = REPO_ROOT / "data/raw/gdelt/index/masterfilelist_20261015.txt"
PAT = re.compile(r"^(\d+) [0-9a-f]{32} (\S+/(\d{14})\.(export|mentions|gkg)\.(?:CSV|csv)\.zip)$")
SMALL = 200_000
MIN_GAP_SLOTS = 4


def main() -> None:
    rows = []
    for line in MASTER.read_text(encoding="utf-8").splitlines():
        m = PAT.match(line)
        if m:
            rows.append((int(m.group(1)), m.group(2), m.group(3), m.group(4)))
    small = [r for r in rows if r[3] == "gkg" and r[0] < SMALL]
    print(f"GKG < {SMALL} bytes: {len(small)}")
    by_year: dict[str, int] = {}
    for r in small:
        by_year[r[2][:6]] = by_year.get(r[2][:6], 0) + 1
    print("por año-mes (top 10):", sorted(by_year.items(), key=lambda kv: -kv[1])[:10])
    rng = random.Random(0)  # noqa: S311 - muestreo reproducible, no criptográfico
    cases = [("degraded_gkg", r) for r in rng.sample(small, 150)]
    gkg_ts = sorted({r[2] for r in rows if r[3] == "gkg"})
    after_gap = []
    for a, b in itertools.pairwise(gkg_ts):
        ta = datetime.strptime(a, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        tb = datetime.strptime(b, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        if tb - ta >= timedelta(minutes=15 * (MIN_GAP_SLOTS + 1)):
            after_gap.append(b)
    idx = {(r[2], r[3]): r for r in rows}
    for ts in after_gap:
        for kind in ("gkg", "export", "mentions"):
            if (ts, kind) in idx:
                cases.append(("after_gap", idx[(ts, kind)]))
    print(f"slots tras hueco >= {MIN_GAP_SLOTS}: {len(after_gap)}; peticiones HEAD: {len(cases)}")
    out = []
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        for case, (size, url, ts, kind) in cases:
            r = c.head(url.replace("http://", "https://"))
            lm = r.headers.get("last-modified")
            nominal = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            lm_dt = parsedate_to_datetime(lm) if lm else None
            out.append(
                {
                    "case": case,
                    "kind": kind,
                    "nominal_utc": nominal,
                    "size": size,
                    "status": r.status_code,
                    "lm_minus_nominal_min": (
                        round((lm_dt - nominal).total_seconds() / 60, 1) if lm_dt else None
                    ),
                }
            )
            time.sleep(0.15)
    df = pl.DataFrame(out)
    df.write_csv(REPO_ROOT / "reports/f1/gdelt_lastmodified_edge.csv")
    print(
        df.group_by("case").agg(
            pl.len(),
            pl.col("lm_minus_nominal_min").min().alias("min"),
            pl.col("lm_minus_nominal_min").median().alias("med"),
            pl.col("lm_minus_nominal_min").quantile(0.99).alias("p99"),
            pl.col("lm_minus_nominal_min").max().alias("max"),
            (pl.col("lm_minus_nominal_min") > 0).sum().alias("n_pos"),
            (pl.col("lm_minus_nominal_min") > 5).sum().alias("n_gt5"),
            (pl.col("lm_minus_nominal_min") > 15).sum().alias("n_gt15"),
        )
    )
    late = df.filter(pl.col("lm_minus_nominal_min") > 5).sort(
        "lm_minus_nominal_min", descending=True
    )
    with pl.Config(tbl_rows=20):
        print(late.head(20))


if __name__ == "__main__":
    main()

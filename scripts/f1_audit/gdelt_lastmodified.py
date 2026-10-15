"""F1: compara Last-Modified (HEAD) de ficheros GDELT históricos con su timestamp nominal.

Si el objeto no se ha re-subido, Last-Modified aproxima la hora real de publicación.
Escribe reports/f1/gdelt_lastmodified.csv. Solo peticiones HEAD (sin descarga).
"""

import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

SAMPLE = REPO_ROOT / "data/raw/gdelt/sample"
NAME_RE = re.compile(r"(\d{14})\.(gkg|export|mentions)\.(?:csv|CSV)\.zip$")


def main() -> None:
    rows = []
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        for p in sorted(SAMPLE.rglob("*.zip")):
            m = NAME_RE.search(p.name)
            if m is None:
                continue
            url = f"https://data.gdeltproject.org/gdeltv2/{p.name}"
            r = c.head(url)
            lm = r.headers.get("last-modified")
            nominal = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            lm_dt = parsedate_to_datetime(lm) if lm else None
            rows.append(
                {
                    "file": p.name,
                    "kind": m.group(2),
                    "nominal_utc": nominal,
                    "last_modified_utc": lm_dt,
                    "status": r.status_code,
                }
            )
            time.sleep(0.2)
    df = pl.DataFrame(rows).with_columns(
        ((pl.col("last_modified_utc") - pl.col("nominal_utc")).dt.total_seconds() / 60)
        .round(1)
        .alias("lm_minus_nominal_min"),
        pl.col("nominal_utc").dt.year().alias("year"),
    )
    df.write_csv(REPO_ROOT / "reports/f1/gdelt_lastmodified.csv")
    with pl.Config(tbl_rows=30):
        print(
            df.group_by("year", "kind")
            .agg(
                pl.len().alias("n"),
                pl.col("lm_minus_nominal_min").min().alias("min"),
                pl.col("lm_minus_nominal_min").median().alias("med"),
                pl.col("lm_minus_nominal_min").max().alias("max"),
            )
            .filter(pl.col("kind") == "gkg")
            .sort("year")
        )


if __name__ == "__main__":
    main()

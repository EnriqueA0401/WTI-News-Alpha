"""F2: retraso Last-Modified menos timestamp nominal de los ficheros GKG del piloto (sin red).

Lee manifests/gdelt_gkg_oil/<version>/*.jsonl.gz, escribe
reports/f2/gdelt_gkg_lastmodified_lag.csv (un fichero por fila) e imprime cuantiles.
Evidencia para ADR-012 (regla de disponibilidad).
"""

import gzip
import json
import sys
from datetime import datetime

import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

VERSION = sys.argv[1] if len(sys.argv) > 1 else "gkg-oil-v2"
MAN = REPO_ROOT / "manifests/gdelt_gkg_oil" / VERSION
OUT = REPO_ROOT / "reports/f2"


def main() -> None:
    rows = []
    for path in sorted(MAN.glob("*.jsonl.gz")):
        for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines():
            for f in json.loads(line)["files"]:
                if f["status"] != "ok":
                    continue
                rows.append(
                    {
                        "name": f["name"],
                        "nominal": f["name"][:14],
                        "last_modified_utc": f["last_modified_utc"],
                        "size": f["size"],
                    }
                )
    df = (
        pl.DataFrame(rows)
        .with_columns(
            pl.col("nominal").str.strptime(pl.Datetime("us", "UTC"), "%Y%m%d%H%M%S"),
            pl.col("last_modified_utc").map_elements(
                datetime.fromisoformat, return_dtype=pl.Datetime("us", "UTC")
            ),
        )
        .with_columns(
            ((pl.col("last_modified_utc") - pl.col("nominal")).dt.total_seconds() / 60).alias(
                "lag_min"
            )
        )
        .sort("nominal")
        .with_columns((pl.col("nominal").diff().dt.total_minutes() > 15).alias("after_gap"))
    )
    OUT.mkdir(parents=True, exist_ok=True)
    df.write_csv(OUT / "gdelt_gkg_lastmodified_lag.csv")
    q = [0.0, 0.01, 0.5, 0.99, 0.999, 1.0]
    print("n", df.height)
    print("cuantiles lag_min", [round(df["lag_min"].quantile(x) or 0.0, 2) for x in q])
    for thr in (0, 5, 15, 30, 60):
        print(f"> +{thr} min:", int((df["lag_min"] > thr).sum()))
    late = df.filter(pl.col("lag_min") > 0)
    with pl.Config(tbl_rows=30, tbl_width_chars=160):
        print(
            late.select("name", "lag_min", "size", "after_gap")
            .sort("lag_min", descending=True)
            .head(25)
        )
    print("tarde tras hueco:", int(late["after_gap"].sum()), "de", late.height)


if __name__ == "__main__":
    main()

"""F2: resumen del piloto GDELT GKG a partir de los manifiestos (sin red).

Lee manifests/gdelt_gkg_oil/<version>/*.jsonl.gz y escribe
reports/f2/gdelt_gkg_pilot_months.csv (una fila por mes) e imprime totales.
"""

import gzip
import json
import sys

import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

VERSION = sys.argv[1] if len(sys.argv) > 1 else "gkg-oil-v1"
MAN = REPO_ROOT / "manifests/gdelt_gkg_oil" / VERSION
OUT = REPO_ROOT / "reports/f2"


def main() -> None:
    days = []
    for path in sorted(MAN.glob("*.jsonl.gz")):
        for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines():
            r = json.loads(line)
            files = r["files"]
            days.append(
                {
                    "day": r["day"],
                    "month": r["day"][:7],
                    "n_files_index": r["n_files_index"],
                    "n_files_ok": r["n_files_ok"],
                    "n_missing_404": r["n_missing_404"],
                    "rows_in": r["rows_in"],
                    "n_rows": r["n_rows"],
                    "rejected": r["rejected"],
                    "rejected_maybe_relevant": r["rejected_maybe_relevant"],
                    "n_bytes": r["n_bytes"],
                    "utf8_replacements": r["utf8_replacements"],
                    "n_no_last_modified": r["n_no_last_modified"],
                    "match_title": sum(f["match_title"] for f in files),
                    "match_url": sum(f["match_url"] for f in files),
                    "match_theme": sum(f["match_theme"] for f in files),
                    "date_mismatch": sum(f["date_mismatch"] for f in files),
                    "retried_files": sum(f["attempts"] > 1 for f in files),
                }
            )
    df = pl.DataFrame(days).sort("day")
    months = (
        df.group_by("month")
        .agg(
            pl.len().alias("days"),
            (pl.col("n_files_index") == 0).sum().alias("days_without_files"),
            pl.exclude("day", "month").sum(),
        )
        .sort("month")
        .with_columns((pl.col("n_rows") / pl.col("rows_in")).round(4).alias("kept_fraction"))
    )
    OUT.mkdir(parents=True, exist_ok=True)
    months.write_csv(OUT / f"gdelt_gkg_{VERSION}_months.csv")
    tot = df.select(pl.len().alias("days"), pl.exclude("day", "month").sum())
    with pl.Config(tbl_cols=20, tbl_width_chars=250, tbl_rows=40):
        print(months)
        print(tot)


if __name__ == "__main__":
    main()

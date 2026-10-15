"""F1: cobertura de GDELT 2.0 a partir del índice maestro (sin descargar los ficheros).

Salida: reports/f1/gdelt_coverage.csv (por año y tipo) y resumen por stdout.
Uso: python gdelt_coverage.py data/raw/gdelt/index/masterfilelist_20261015.txt
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

LINE_RE = r"^(\d+) ([0-9a-f]{32}) (\S+/(\d{14})\.(export|mentions|gkg)\.(?:CSV|csv)\.zip)$"


def main() -> None:
    path = Path(sys.argv[1])
    lines = pl.read_csv(
        path, has_header=False, new_columns=["line"], separator="\x01", quote_char=None
    )
    parsed = lines.with_columns(pl.col("line").str.extract_groups(LINE_RE).alias("g")).unnest("g")
    parsed = parsed.rename({"1": "size", "2": "md5", "3": "url", "4": "ts", "5": "kind"})
    bad = parsed.filter(pl.col("ts").is_null())
    print(f"líneas totales={parsed.height} no_parseables={bad.height}")
    for row in bad.head(10).iter_rows(named=True):
        print("  no parseable:", row["line"][:120])
    ok = parsed.filter(pl.col("ts").is_not_null()).with_columns(
        pl.col("size").cast(pl.Int64),
        pl.col("ts").str.strptime(pl.Datetime("us", "UTC"), "%Y%m%d%H%M%S").alias("ts"),
    )
    off_grid = ok.filter((pl.col("ts").dt.minute() % 15 != 0) | (pl.col("ts").dt.second() != 0))
    print(f"timestamps fuera de la rejilla de 15 min: {off_grid.height}")
    dups = ok.group_by(["kind", "ts"]).len().filter(pl.col("len") > 1)
    print(f"(tipo, ts) duplicados: {dups.height}")
    start = ok["ts"].min()
    end = ok["ts"].max()
    print(f"rango: {start} -> {end}")
    grid = pl.DataFrame({"ts": pl.datetime_range(start, end, "15m", time_zone="UTC", eager=True)})
    rows = []
    for kind in ("export", "mentions", "gkg"):
        present = ok.filter(pl.col("kind") == kind).select("ts").unique()
        missing = grid.join(present, on="ts", how="anti")
        size = ok.filter(pl.col("kind") == kind)
        per_year = (
            grid.with_columns(pl.col("ts").dt.year().alias("year"))
            .group_by("year")
            .len("expected")
            .join(
                missing.with_columns(pl.col("ts").dt.year().alias("year"))
                .group_by("year")
                .len("missing"),
                on="year",
                how="left",
            )
            .join(
                size.with_columns(pl.col("ts").dt.year().alias("year"))
                .group_by("year")
                .agg(
                    pl.len().alias("files"),
                    (pl.col("size").sum() / 1e9).round(2).alias("gb_zip"),
                    (pl.col("size").median() / 1e6).round(2).alias("median_mb"),
                ),
                on="year",
                how="left",
            )
            .with_columns(pl.lit(kind).alias("kind"), pl.col("missing").fill_null(0))
        )
        rows.append(per_year)
        # rachas de huecos más largas
        if missing.height:
            runs = (
                missing.sort("ts")
                .with_columns(
                    (pl.col("ts").diff() != pl.duration(minutes=15)).cum_sum().alias("run")
                )
                .group_by("run")
                .agg(pl.col("ts").min().alias("from"), pl.len().alias("slots"))
                .sort("slots", descending=True)
                .head(5)
            )
            print(f"[{kind}] huecos={missing.height}; 5 rachas más largas (slots de 15 min):")
            for r in runs.iter_rows(named=True):
                print(f"   desde {r['from']}  {r['slots']} slots ({r['slots'] / 4:.1f} h)")
    out = (
        pl.concat(rows)
        .sort(["kind", "year"])
        .select("kind", "year", "expected", "files", "missing", "gb_zip", "median_mb")
    )
    dest = REPO_ROOT / "reports/f1/gdelt_coverage.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.write_csv(dest)
    with pl.Config(tbl_rows=60, tbl_width_chars=120):
        print(out)
    print("generado:", datetime.now(tz=UTC).isoformat(timespec="seconds"))


if __name__ == "__main__":
    main()

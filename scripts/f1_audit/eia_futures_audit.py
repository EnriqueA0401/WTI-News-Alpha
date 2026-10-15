"""Auditoría de los precios de liquidación diarios del futuro NYMEX CL publicados por la EIA.

Series RCLC1..RCLC4 (contratos 1 a 4, USD/bbl), descargadas con eia_fetch.py y
verificadas contra manifests/eia.jsonl. Uso: referencia diaria para contrastar los
datos de Darwinex (ADR-015). Imprime cobertura, huecos y valores atípicos y escribe
reports/f1/eia_futures_coverage.csv. Sin red.
"""

import json

import polars as pl

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

SERIES = ("RCLC1", "RCLC2", "RCLC3", "RCLC4")


def load(series: str) -> pl.DataFrame:
    rel = f"eia/futures/{series}_daily_20261015.json"
    RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit()).verify("eia", rel)
    payload = json.loads((REPO_ROOT / "data/raw" / rel).read_text(encoding="utf-8"))
    resp = payload["response"]
    rows = resp["data"]
    if int(resp["total"]) != len(rows):
        raise RuntimeError(f"{series}: total {resp['total']} != filas {len(rows)} (paginar)")
    return pl.DataFrame(rows).select(
        pl.col("period").str.strptime(pl.Date, "%Y-%m-%d").alias("date"),
        pl.lit(series).alias("series"),
        pl.col("value").cast(pl.Float64).alias("settle"),
        pl.col("units"),
        pl.col("series-description").alias("description"),
    )


def main() -> None:
    df = pl.concat([load(s) for s in SERIES]).sort("series", "date")
    print(df.group_by("series", "units", "description").len().sort("series"))
    cov = (
        df.group_by("series")
        .agg(
            pl.col("date").min().alias("first"),
            pl.col("date").max().alias("last"),
            pl.len().alias("n"),
            pl.col("date").n_unique().alias("n_unique_dates"),
            pl.col("settle").null_count().alias("nulls"),
            (pl.col("settle") <= 0).sum().alias("non_positive"),
            pl.col("date").diff().dt.total_days().max().alias("max_gap_days"),
            pl.col("settle").min().alias("min"),
            pl.col("settle").max().alias("max"),
        )
        .sort("series")
    )
    (REPO_ROOT / "reports/f1").mkdir(parents=True, exist_ok=True)
    cov.write_csv(REPO_ROOT / "reports/f1/eia_futures_coverage.csv")
    with pl.Config(tbl_cols=12, tbl_width_chars=200):
        print(cov)
        wide = df.pivot(on="series", index="date", values="settle").sort("date")
        print(
            "fechas con alguna serie ausente:",
            wide.filter(pl.any_horizontal(pl.all().is_null())).height,
        )
        print(df.filter(pl.col("settle") <= 0))
        big = (
            df.sort("series", "date")
            .with_columns(
                (pl.col("settle") / pl.col("settle").shift(1).over("series") - 1).alias("ret")
            )
            .filter(pl.col("ret").abs() > 0.15)
        )
        print("variaciones diarias > 15 %:", big.height)
        print(big.head(10))


if __name__ == "__main__":
    main()

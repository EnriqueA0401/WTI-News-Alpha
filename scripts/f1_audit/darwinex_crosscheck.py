"""F1 bis: zona horaria semanal y contraste Darwinex XTIUSD (bid M1) con EIA RCLC1/RCLC2.

ADR-015/017. Hipótesis a contrastar: hora servidor = America/New_York + 7 h
(pausa CL 17:00-18:00 ET = 00:00-01:00 servidor).
Sin red. Escribe reports/f1/darwinex_break_by_week.csv y reports/f1/darwinex_vs_eia_daily.csv.
"""

import json
from datetime import timedelta

import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

CSV = REPO_ROOT / "data/raw/darwinex/extracted_v2/XTIUSD_darwinex_M1 - copia.csv"
OUT = REPO_ROOT / "reports/f1"
SERVER_MINUS_ET = timedelta(hours=7)  # hipótesis


def load() -> pl.DataFrame:
    df = pl.read_csv(CSV, schema_overrides={"Date": pl.String, "Time": pl.String})
    return df.with_columns(
        (pl.col("Date") + " " + pl.col("Time"))
        .str.strptime(pl.Datetime("us"), "%Y.%m.%d %H:%M")
        .alias("ts_server")
    )


def weekly_break(df: pl.DataFrame) -> pl.DataFrame:
    """Por semana: ¿hay barras en 23:00-23:59, 00:00-00:59 y 01:00-01:59 servidor de mar a vie?"""
    d = df.with_columns(
        pl.col("ts_server").dt.truncate("1w").alias("week"),
        pl.col("ts_server").dt.hour().alias("h"),
        pl.col("ts_server").dt.weekday().alias("wd"),
    ).filter(pl.col("wd").is_between(2, 5))
    return (
        d.group_by("week")
        .agg(
            (pl.col("h") == 23).sum().alias("bars_h23"),
            (pl.col("h") == 0).sum().alias("bars_h00"),
            (pl.col("h") == 1).sum().alias("bars_h01"),
        )
        .sort("week")
    )


def eia() -> pl.DataFrame:
    frames = []
    for s in ("RCLC1", "RCLC2"):
        rows = json.loads(
            (REPO_ROOT / f"data/raw/eia/futures/{s}_daily_20261015.json").read_text()
        )["response"]["data"]
        frames.append(
            pl.DataFrame(rows).select(
                pl.col("period").str.strptime(pl.Date, "%Y-%m-%d").alias("date"),
                pl.col("value").cast(pl.Float64).alias(s),
            )
        )
    return frames[0].join(frames[1], on="date", how="inner")


def main() -> None:
    df = load()
    wb = weekly_break(df)
    OUT.mkdir(parents=True, exist_ok=True)
    wb.write_csv(OUT / "darwinex_break_by_week.csv")
    bad = wb.filter(pl.col("bars_h00") > 0)
    print("semanas:", wb.height, "| semanas con barras a las 00h servidor (mar-vie):", bad.height)
    print(bad.head(20))
    print(
        "semanas sin barras a las 23h o 01h:",
        wb.filter((pl.col("bars_h23") == 0) | (pl.col("bars_h01") == 0)).height,
    )

    # 14:29 ET = 21:29 servidor (hipótesis): último minuto antes de la liquidación 14:30 ET.
    snap = (
        df.with_columns((pl.col("ts_server") - SERVER_MINUS_ET).alias("ts_et"))
        .filter((pl.col("ts_et").dt.hour() == 14) & (pl.col("ts_et").dt.minute() == 29))
        .select(
            pl.col("ts_et").dt.date().alias("date"),
            pl.col("Close").alias("bid_1429"),
            (pl.col("Close") + pl.col("AvgSpread") / 2).alias("mid_1429"),
            pl.col("AvgSpread").alias("spread"),
        )
    )
    j = (
        snap.join(eia(), on="date", how="inner")
        .sort("date")
        .with_columns(
            (pl.col("mid_1429") - pl.col("RCLC1")).alias("d1"),
            (pl.col("mid_1429") - pl.col("RCLC2")).alias("d2"),
        )
    )
    j = j.with_columns(
        pl.when(pl.col("d1").abs() <= pl.col("d2").abs())
        .then(pl.lit("C1"))
        .otherwise(pl.lit("C2"))
        .alias("closest")
    )
    j.write_csv(OUT / "darwinex_vs_eia_daily.csv")
    print("días comparados:", j.height, j["date"].min(), "→", j["date"].max())
    stats = j.select(
        pl.col("d1").abs().median().alias("med_abs_d1"),
        pl.col("d2").abs().median().alias("med_abs_d2"),
        pl.col("d1").abs().quantile(0.95).alias("p95_abs_d1"),
        (pl.col("closest") == "C1").mean().alias("share_closest_C1"),
        pl.col("spread").median().alias("med_spread"),
    )
    print(stats)
    # correlación de retornos diarios (mismo contrato EIA más cercano no es trivial: se usa C1 y C2)
    r = j.with_columns(
        pl.col("mid_1429").pct_change().alias("r_dw"),
        pl.col("RCLC1").pct_change().alias("r_c1"),
        pl.col("RCLC2").pct_change().alias("r_c2"),
        pl.col("date").diff().dt.total_days().alias("gapd"),
    ).filter(pl.col("gapd") <= 4)
    r = r.filter(pl.col("date") != pl.date(2020, 4, 20)).filter(
        pl.col("date") != pl.date(2020, 4, 21)
    )
    print(
        "corr retornos dw~C1:",
        round(r.select(pl.corr("r_dw", "r_c1")).item(), 4),
        "dw~C2:",
        round(r.select(pl.corr("r_dw", "r_c2")).item(), 4),
        "n:",
        r.height,
    )
    worst = j.with_columns(
        pl.min_horizontal(pl.col("d1").abs(), pl.col("d2").abs()).alias("dmin")
    ).sort("dmin", descending=True)
    print("peores días (min |dif| a C1/C2):")
    print(worst.select("date", "mid_1429", "RCLC1", "RCLC2", "dmin").head(10))
    by_year = (
        j.group_by(pl.col("date").dt.year().alias("y"))
        .agg(
            pl.len().alias("n"),
            pl.col("d1").abs().median().alias("med_abs_d1"),
            (pl.col("closest") == "C1").mean().alias("sh_C1"),
        )
        .sort("y")
    )
    print(by_year)


if __name__ == "__main__":
    main()

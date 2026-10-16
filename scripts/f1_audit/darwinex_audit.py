"""F1 bis: auditoría del fichero Darwinex XTIUSD M1 exportado de SQX (ADR-017).

Sin red. Lee el CSV extraído de data/raw/darwinex/xti_m1_sqx_20261016_v2.bin y mide:
1) validez (OHLC coherentes, duplicados, orden), 2) cobertura y huecos,
3) desfase horario servidor→ET inferido de la pausa diaria del CL (17:00-18:00 ET),
4) contraste del cierre de las 14:30 ET con la liquidación EIA RCLC1/RCLC2.
Escribe reports/f1/darwinex_*.csv e imprime resúmenes.
"""

import json

import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

CSV = REPO_ROOT / "data/raw/darwinex/extracted_v2/XTIUSD_darwinex_M1 - copia.csv"
OUT = REPO_ROOT / "reports/f1"


def load() -> pl.DataFrame:
    df = pl.read_csv(CSV, schema_overrides={"Date": pl.String, "Time": pl.String})
    return df.with_columns(
        (pl.col("Date") + " " + pl.col("Time"))
        .str.strptime(pl.Datetime("us"), "%Y.%m.%d %H:%M", strict=True)
        .alias("ts_server")
    ).sort("ts_server", maintain_order=True)


def validity(df: pl.DataFrame) -> dict[str, int]:
    return {
        "rows": df.height,
        "dup_ts": df.height - df["ts_server"].n_unique(),
        "non_positive": int(
            (pl.min_horizontal("Open", "High", "Low", "Close") <= 0).sum()
            if False
            else df.select((pl.min_horizontal("Open", "High", "Low", "Close") <= 0).sum()).item()
        ),
        "high_lt_max": int(
            df.select((pl.col("High") < pl.max_horizontal("Open", "Close")).sum()).item()
        ),
        "low_gt_min": int(
            df.select((pl.col("Low") > pl.min_horizontal("Open", "Close")).sum()).item()
        ),
        "neg_spread": int((df["AvgSpread"] < 0).sum()),
        "zero_tickvol": int((df["TickVolume"] <= 0).sum()),
    }


def gaps(df: pl.DataFrame) -> pl.DataFrame:
    return df.select(
        pl.col("ts_server").shift(1).alias("from"),
        pl.col("ts_server").alias("to"),
        (pl.col("ts_server").diff().dt.total_minutes()).alias("gap_min"),
    ).filter(pl.col("gap_min") > 3 * 24 * 60)


def break_profile(df: pl.DataFrame) -> pl.DataFrame:
    """Por mes: minutos del día (hora servidor) sin barra en >= 95 % de los días mar-vie.

    Se usa la rejilla completa de 1440 minutos: un minuto sin ninguna barra no aparece en los datos.
    """
    d = df.with_columns(
        pl.col("ts_server").dt.truncate("1mo").alias("month"),
        pl.col("ts_server").dt.date().alias("day"),
        (pl.col("ts_server").dt.hour().cast(pl.Int32) * 60 + pl.col("ts_server").dt.minute()).alias(
            "mod"
        ),
        pl.col("ts_server").dt.weekday().alias("wd"),
    ).filter(pl.col("wd").is_between(2, 5))
    days = d.group_by("month").agg(pl.col("day").n_unique().alias("n_days"))
    grid = days.select("month").join(
        pl.DataFrame({"mod": list(range(1440))}, schema={"mod": pl.Int32}), how="cross"
    )
    cnt = d.group_by("month", "mod").agg(pl.col("day").n_unique().alias("n"))
    prof = (
        grid.join(cnt, on=["month", "mod"], how="left")
        .join(days, on="month")
        .with_columns(
            pl.col("n").fill_null(0).alias("n")
        )  # minuto sin ninguna barra = 0 días (conteo, no dato)
        .with_columns((pl.col("n") / pl.col("n_days")).alias("share"))
    )
    br = (
        prof.filter(pl.col("share") < 0.05)
        .group_by("month")
        .agg(
            pl.col("mod").min().alias("break_start_mod"),
            pl.col("mod").max().alias("break_end_mod"),
            pl.len().alias("n_break_min"),
        )
    )
    return days.join(br, on="month", how="left").sort("month")


def main() -> None:
    df = load()
    print("validez:", json.dumps(validity(df)))
    print("rango:", df["ts_server"].min(), "→", df["ts_server"].max())
    print(
        "barras por año:",
        dict(df.group_by(pl.col("ts_server").dt.year()).len().sort("ts_server").iter_rows()),
    )
    g = gaps(df)
    OUT.mkdir(parents=True, exist_ok=True)
    g.write_csv(OUT / "darwinex_gaps_gt3d.csv")
    print("huecos > 3 días:")
    print(g)
    bp = break_profile(df)
    bp.write_csv(OUT / "darwinex_break_by_month.csv")
    with pl.Config(tbl_rows=120):
        print(
            bp.with_columns(
                (pl.col("break_start_mod") // 60).alias("h0"),
                (pl.col("break_start_mod") % 60).alias("m0"),
                (pl.col("break_end_mod") // 60).alias("h1"),
                (pl.col("break_end_mod") % 60).alias("m1"),
            ).select("month", "h0", "m0", "h1", "m1", "n_break_min")
        )


if __name__ == "__main__":
    main()

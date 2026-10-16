"""F1 bis: tamaño del salto en la reapertura diaria (01:00 servidor) frente a minutos normales."""

import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

CSV = REPO_ROOT / "data/raw/darwinex/extracted_v2/XTIUSD_darwinex_M1 - copia.csv"
df = (
    pl.read_csv(CSV, schema_overrides={"Date": pl.String, "Time": pl.String})
    .with_columns(
        (pl.col("Date") + " " + pl.col("Time"))
        .str.strptime(pl.Datetime("us"), "%Y.%m.%d %H:%M")
        .alias("ts")
    )
    .sort("ts")
)
df = df.with_columns(
    (pl.col("Open") / pl.col("Close").shift(1)).log().alias("r_open"),
    pl.col("ts").diff().dt.total_minutes().alias("gap"),
    pl.col("ts").dt.hour().alias("h"),
    pl.col("ts").dt.minute().alias("m"),
    pl.col("ts").dt.weekday().alias("wd"),
)
reopen = df.filter(
    (pl.col("h") == 1)
    & (pl.col("m") == 0)
    & pl.col("gap").is_between(55, 70)
    & pl.col("wd").is_between(2, 5)
)
normal = df.filter((pl.col("gap") == 1) & pl.col("h").is_between(2, 4))
for name, d in [
    ("reapertura diaria 01:00 (pausa 1h)", reopen),
    ("minutos normales 02-04h", normal),
]:
    a = d["r_open"].abs()
    print(
        name,
        "n",
        d.height,
        "mediana |r| bp",
        round(a.median() * 1e4, 2),
        "p99 bp",
        round(a.quantile(0.99) * 1e4, 1),
        "p99.9",
        round(a.quantile(0.999) * 1e4, 1),
    )
print(reopen.sort(pl.col("r_open").abs(), descending=True).select("ts", "r_open").head(8))

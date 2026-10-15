"""F1: analiza la muestra GDELT (relevancia WTI, timestamps, títulos, duplicados).

Lee los ficheros de data/raw/gdelt/sample (verificados contra el manifiesto) y
escribe reports/f1/gdelt_sample_files.csv (una fila por fichero GKG) y
reports/f1/gdelt_sample_lags.csv (cuantiles de retraso por año).
Criterios de relevancia (auditoría, no definitivos; F3 define los finales):
- theme: V1THEMES contiene ECON_OILPRICE o ENV_OIL (amplio).
- oilprice: V1THEMES contiene ECON_OILPRICE (estrecho).
- title: PAGE_TITLE contiene crude|oil|opec|wti|brent (palabra completa).
"""

import io
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.runlog import REPO_ROOT, current_commit

SAMPLE = REPO_ROOT / "data/raw/gdelt/sample"
OUT = REPO_ROOT / "reports/f1"
GKG_COLS = 27
THEME_RE = r"(?:^|;)(?:ECON_OILPRICE|ENV_OIL)(?:;|$)"
OILPRICE_RE = r"(?:^|;)ECON_OILPRICE(?:;|$)"
TITLE_RE = r"(?i)\b(?:crude|oil|opec|wti|brent)\b"
NAME_RE = re.compile(r"(\d{14})\.(gkg|export|mentions)\.(?:csv|CSV)\.zip$")


def read_tsv(path: Path) -> pl.DataFrame:
    with zipfile.ZipFile(path) as z:
        (name,) = z.namelist()
        raw = z.read(name)
    text = raw.decode("utf-8", errors="replace")
    return pl.read_csv(
        io.StringIO(text),
        separator="\t",
        has_header=False,
        quote_char=None,
        infer_schema=False,
        truncate_ragged_lines=True,
    )


def analyze_gkg(path: Path, file_ts: datetime) -> tuple[dict[str, object], pl.DataFrame]:
    df = read_tsv(path)
    ncols = df.width
    df = df.select(
        pl.col("column_1").alias("date"),
        pl.col("column_2").alias("collection"),
        pl.col("column_4").alias("url"),
        pl.col("column_7").fill_null("").alias("themes"),
        pl.col(f"column_{GKG_COLS - 1}").fill_null("").alias("extras"),
    ).with_columns(
        pl.col("extras").str.extract(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>").alias("title"),
        pl.col("extras")
        .str.extract(r"<PAGE_PRECISEPUBTIMESTAMP>(\d{14})</PAGE_PRECISEPUBTIMESTAMP>")
        .str.strptime(pl.Datetime("us", "UTC"), "%Y%m%d%H%M%S", strict=False)
        .alias("pub_ts"),
    )
    df = df.with_columns(
        pl.col("themes").str.contains(THEME_RE).alias("rel_theme"),
        pl.col("themes").str.contains(OILPRICE_RE).alias("rel_oilprice"),
        pl.col("title").fill_null("").str.contains(TITLE_RE).alias("rel_title"),
        ((pl.lit(file_ts) - pl.col("pub_ts")).dt.total_seconds() / 60).alias("lag_min"),
    )
    rel = df.filter(pl.col("rel_theme"))
    stats: dict[str, object] = {
        "file_ts": file_ts.isoformat(),
        "year": file_ts.year,
        "ncols": ncols,
        "rows": df.height,
        "date_eq_file_ts": int((df["date"] == file_ts.strftime("%Y%m%d%H%M%S")).sum()),
        "web_collection": int((df["collection"] == "1").sum()),
        "rel_theme": rel.height,
        "rel_title": int(df["rel_title"].sum()),
        "rel_oilprice": int(df["rel_oilprice"].sum()),
        "rel_both": int((df["rel_theme"] & df["rel_title"]).sum()),
        "with_title": int(df["title"].is_not_null().sum()),
        "with_pubts": int(df["pub_ts"].is_not_null().sum()),
        "rel_with_pubts": int(rel["pub_ts"].is_not_null().sum()),
        "pub_after_file": int((df["lag_min"] < 0).sum()),
        "dup_urls_in_file": df.height - df["url"].n_unique(),
    }
    return stats, df.select("url", "rel_theme", "lag_min", "pub_ts").with_columns(
        pl.lit(file_ts).alias("file_ts")
    )


def main() -> None:
    store = RawStore(REPO_ROOT / "data/raw", REPO_ROOT / "manifests", current_commit())
    print("ficheros verificados contra manifiesto:", store.verify_all("gdelt"))
    files = sorted(SAMPLE.rglob("*.zip"))
    rows, frames, aux = [], [], []
    for p in files:
        m = NAME_RE.search(p.name)
        if m is None:
            continue
        file_ts = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        if m.group(2) == "gkg":
            st, fr = analyze_gkg(p, file_ts)
            rows.append(st)
            frames.append(fr)
        elif m.group(2) == "export":
            df = read_tsv(p)
            added = df.get_column(f"column_{df.width - 2}")  # DATEADDED (penúltima)
            aux.append(
                {
                    "kind": "export",
                    "file_ts": file_ts.isoformat(),
                    "rows": df.height,
                    "ncols": df.width,
                    "dateadded_eq_file": int((added == m.group(1)).sum()),
                }
            )
        else:
            df = read_tsv(p)
            aux.append(
                {
                    "kind": "mentions",
                    "file_ts": file_ts.isoformat(),
                    "rows": df.height,
                    "ncols": df.width,
                    "dateadded_eq_file": int(
                        (df["column_2"] == m.group(1)).sum()
                    ),  # MentionTimeDate
                }
            )
    stats = pl.DataFrame(rows).sort("file_ts")
    OUT.mkdir(parents=True, exist_ok=True)
    stats.write_csv(OUT / "gdelt_sample_files.csv")
    auxdf = pl.DataFrame(aux)
    auxdf.write_csv(OUT / "gdelt_sample_export_mentions.csv")
    allrows = pl.concat(frames)

    with pl.Config(tbl_rows=40, tbl_width_chars=200, tbl_cols=20):
        print(
            stats.group_by("year")
            .agg(
                pl.len().alias("files"),
                pl.col("rows").mean().round(0).alias("rows_mean"),
                pl.col("rel_theme").mean().round(1).alias("rel_theme_mean"),
                pl.col("rel_theme").std().round(1).alias("rel_theme_sd"),
                pl.col("rel_title").mean().round(1).alias("rel_title_mean"),
                (pl.col("with_title").sum() / pl.col("rows").sum()).round(3).alias("p_title"),
                (pl.col("with_pubts").sum() / pl.col("rows").sum()).round(3).alias("p_pubts"),
                pl.col("pub_after_file").sum().alias("pub_after"),
                pl.col("dup_urls_in_file").sum().alias("dups"),
                (pl.col("date_eq_file_ts").sum() / pl.col("rows").sum())
                .round(3)
                .alias("p_date_eq"),
                pl.col("ncols").min().alias("ncol_min"),
                pl.col("ncols").max().alias("ncol_max"),
            )
            .sort("year")
        )
        lags = (
            allrows.filter(pl.col("lag_min").is_not_null())
            .with_columns(pl.col("file_ts").dt.year().alias("year"))
            .group_by("year")
            .agg(
                pl.len().alias("n"),
                *[
                    pl.col("lag_min").quantile(q).round(1).alias(f"q{int(q * 100)}")
                    for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99)
                ],
                (pl.col("lag_min") < 0).sum().alias("neg"),
                (pl.col("lag_min") > 1440).sum().alias("gt_1d"),
            )
            .sort("year")
        )
        lags.write_csv(OUT / "gdelt_sample_lags.csv")
        print("retraso fichero - PAGE_PRECISEPUBTIMESTAMP (min):")
        print(lags)
        print(
            auxdf.group_by("kind").agg(
                pl.len(),
                pl.col("ncols").unique().alias("ncols"),
                (pl.col("dateadded_eq_file").sum() / pl.col("rows").sum())
                .round(4)
                .alias("p_eq_file"),
            )
        )
    block = allrows.filter(
        (pl.col("file_ts") >= datetime(2024, 6, 10, 14, tzinfo=UTC))
        & (pl.col("file_ts") <= datetime(2024, 6, 10, 15, 45, tzinfo=UTC))
    )
    first_seen = block.group_by("url").agg(
        pl.col("file_ts").min().alias("first"), pl.len().alias("n")
    )
    print(
        "bloque consecutivo 2024-06-10 14:00-15:45:",
        f"filas={block.height} urls_unicas={first_seen.height}",
        f"urls_en_>1_fichero={first_seen.filter(pl.col('n') > 1).height}",
    )
    across = allrows.group_by("url").len().filter(pl.col("len") > 1)
    print(f"urls repetidas en ficheros distintos de toda la muestra: {across.height}")


if __name__ == "__main__":
    main()

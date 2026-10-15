"""F1 (CFTC COT): resume el informe Disaggregated para NYMEX WTI (código 067651).

Lee los zips anuales guardados en data/raw/cftc/history/ (fut_ y com_), filtra el
código de mercado y emite un resumen JSON: nombres y su rango de fechas, fechas
"as of", día de la semana, huecos > 7 días, duplicados, rango de open interest y
mercados con "CRUDE" en el nombre (contexto). Uso:
uv run python -I scripts/f1_audit/cftc_analyze.py RAW_DIR [CODE]
"""

import io
import itertools
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

COLS = [
    "Market_and_Exchange_Names",
    "As_of_Date_In_Form_YYMMDD",
    "Report_Date_as_YYYY-MM-DD",
    "CFTC_Contract_Market_Code",
    "Open_Interest_All",
]
STUDY_START = date(2015, 2, 18)


def read_zip(path: Path) -> pl.DataFrame:
    with zipfile.ZipFile(path) as z:
        (member,) = z.infolist()
        data = z.read(member.filename)
    df = pl.read_csv(
        io.BytesIO(data),
        columns=COLS,
        schema_overrides={c: pl.Utf8 for c in COLS},
        encoding="latin1",
    )
    return df.with_columns(
        pl.col("Report_Date_as_YYYY-MM-DD").str.slice(0, 10).str.to_date("%Y-%m-%d").alias("d"),
        pl.col("CFTC_Contract_Market_Code").str.strip_chars().alias("code"),
        pl.col("Market_and_Exchange_Names").str.strip_chars().alias("name"),
        pl.lit(path.name).alias("file"),
        pl.lit(f"{member.date_time}").alias("member_mtime"),
    )


def summarize(df: pl.DataFrame, code: str) -> dict[str, object]:
    sel = df.filter(pl.col("code") == code).sort("d")
    dates = sel["d"].unique().sort().to_list()
    gaps = [
        {"prev": str(a), "next": str(b), "days": (b - a).days}
        for a, b in itertools.pairwise(dates)
        if (b - a).days != 7
    ]
    names = (
        sel.group_by("name")
        .agg(pl.col("d").min().alias("first"), pl.col("d").max().alias("last"), pl.len())
        .sort("first")
    )
    weekday = sel.select(pl.col("d").dt.weekday().alias("wd")).group_by("wd").len().sort("wd")
    dups = sel.group_by("d").len().filter(pl.col("len") > 1)
    in_window = [d for d in dates if d >= STUDY_START]
    return {
        "n_rows": sel.height,
        "n_dates": len(dates),
        "first": str(dates[0]) if dates else None,
        "last": str(dates[-1]) if dates else None,
        "n_dates_in_window": len(in_window),
        "non_tuesday": [str(d) for d in dates if d.isoweekday() != 2],
        "weekday_counts_iso": {str(r["wd"]): r["len"] for r in weekday.to_dicts()},
        "gaps_not_7d": gaps,
        "dup_dates": [str(r["d"]) for r in dups.to_dicts()],
        "names": [{k: str(v) for k, v in r.items()} for r in names.to_dicts()],
        "oi_min": int(sel["Open_Interest_All"].str.strip_chars().cast(pl.Int64).min() or 0),
        "oi_max": int(sel["Open_Interest_All"].str.strip_chars().cast(pl.Int64).max() or 0),
    }


def main() -> None:
    raw = Path(sys.argv[1])
    code = sys.argv[2] if len(sys.argv) > 2 else "067651"
    out: dict[str, object] = {}
    for kind in ("fut", "com"):
        files = sorted(raw.glob(f"{kind}_disagg_txt_20*.zip"))
        frames = [read_zip(f) for f in files]
        df = pl.concat(frames)
        s = summarize(df, code)
        s["files"] = [f.name for f in files]
        s["member_mtimes"] = {f["file"][0]: f["member_mtime"][0] for f in frames}
        crude = (
            df.filter(
                pl.col("name").str.contains("(?i)CRUDE|WTI") & (pl.col("d").dt.year() >= 2025)
            )
            .group_by("code", "name")
            .agg(pl.col("d").max().alias("last"))
            .sort("code")
        )
        s["crude_markets_2025plus"] = [{k: str(v) for k, v in r.items()} for r in crude.to_dicts()]
        out[kind] = s
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()

"""F1 (CFTC COT): prueba indirecta de revisiones en 067651.

Las columnas Change_in_* se calculan al publicar la semana t frente a t-1. Si una
semana se revisó después, Change_in_X(t) != X(t) - X(t-1) en el histórico actual.
Cuenta discrepancias por columna y lista las fechas. Uso:
uv run python -I scripts/f1_audit/cftc_revision_check.py RAW_DIR PREFIX
"""

import io
import json
import sys
import zipfile
from pathlib import Path

import polars as pl

CODE = "067651"
PAIRS = {
    "Open_Interest_All": "Change_in_Open_Interest_All",
    "Prod_Merc_Positions_Long_All": "Change_in_Prod_Merc_Long_All",
    "Prod_Merc_Positions_Short_All": "Change_in_Prod_Merc_Short_All",
    "Swap_Positions_Long_All": "Change_in_Swap_Long_All",
    "Swap__Positions_Short_All": "Change_in_Swap_Short_All",
    "M_Money_Positions_Long_All": "Change_in_M_Money_Long_All",
    "M_Money_Positions_Short_All": "Change_in_M_Money_Short_All",
    "Other_Rept_Positions_Long_All": "Change_in_Other_Rept_Long_All",
    "Other_Rept_Positions_Short_All": "Change_in_Other_Rept_Short_All",
    "NonRept_Positions_Long_All": "Change_in_NonRept_Long_All",
    "NonRept_Positions_Short_All": "Change_in_NonRept_Short_All",
}


def load(raw: Path, prefix: str) -> pl.DataFrame:
    frames = []
    for f in sorted(raw.glob(f"{prefix}_disagg_txt_20*.zip")):
        with zipfile.ZipFile(f) as z:
            (m,) = z.infolist()
            df = pl.read_csv(io.BytesIO(z.read(m.filename)), infer_schema=False, encoding="latin1")
        frames.append(df.filter(pl.col("CFTC_Contract_Market_Code").str.strip_chars() == CODE))
    cols = ["Report_Date_as_YYYY-MM-DD", *PAIRS, *PAIRS.values()]
    df = pl.concat([f.select(cols) for f in frames])
    return df.with_columns(
        pl.col("Report_Date_as_YYYY-MM-DD").str.slice(0, 10).alias("d"),
        *[pl.col(c).str.strip_chars().cast(pl.Int64) for c in [*PAIRS, *PAIRS.values()]],
    ).sort("d")


def main() -> None:
    df = load(Path(sys.argv[1]), sys.argv[2])
    out: dict[str, object] = {"n_weeks": df.height - 1, "by_column": {}}
    bad_dates: set[str] = set()
    for lvl, chg in PAIRS.items():
        diff = df.with_columns((pl.col(lvl) - pl.col(lvl).shift(1)).alias("impl")).slice(1)
        bad = diff.filter(pl.col("impl") != pl.col(chg))
        rows = bad.select("d", chg, "impl").to_dicts()
        out["by_column"][lvl] = {"n_mismatch": bad.height, "examples": rows[:6]}  # type: ignore[index]
        bad_dates.update(r["d"] for r in rows)
    out["n_dates_with_any_mismatch"] = len(bad_dates)
    out["dates_with_any_mismatch"] = sorted(bad_dates)
    json.dump(out, sys.stdout, indent=1, default=str)


if __name__ == "__main__":
    main()

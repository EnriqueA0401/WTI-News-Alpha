"""F1 (CFTC COT): compara filas 067651 de la API Socrata con los zips anuales.

Para cada (fecha, columna) común (nombres comparados en minúsculas) cuenta
coincidencias y discrepancias numéricas. Uso:
uv run python -I scripts/f1_audit/cftc_api_compare.py API.json ZIP_GLOB_DIR PREFIX
(PREFIX = fut o com)
"""

import io
import json
import sys
import zipfile
from pathlib import Path

import polars as pl

CODE = "067651"


def zip_rows(raw: Path, prefix: str) -> pl.DataFrame:
    frames = []
    for f in sorted(raw.glob(f"{prefix}_disagg_txt_20*.zip")):
        with zipfile.ZipFile(f) as z:
            (m,) = z.infolist()
            df = pl.read_csv(io.BytesIO(z.read(m.filename)), infer_schema=False, encoding="latin1")
        df = df.filter(pl.col("CFTC_Contract_Market_Code").str.strip_chars() == CODE)
        frames.append(df.rename({c: c.lower() for c in df.columns}))
    return pl.concat(frames, how="diagonal")


def norm(v: object) -> str:
    s = str(v).strip() if v is not None else ""
    try:
        x = float(s)
    except ValueError:
        return s
    return repr(round(x, 6))


def main() -> None:
    api = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    z = zip_rows(Path(sys.argv[2]), sys.argv[3])
    zmap = {r["report_date_as_yyyy-mm-dd"][:10]: r for r in z.to_dicts()}
    amap = {r["report_date_as_yyyy_mm_dd"][:10]: r for r in api}
    common_dates = sorted(set(zmap) & set(amap))
    zcols = {c.replace("-", "_") for c in z.columns}
    acols = set().union(*(r.keys() for r in api))
    cols = sorted((zcols & acols) - {"report_date_as_yyyy_mm_dd", "as_of_date_in_form_yymmdd"})
    mism: list[tuple[str, str, str, str]] = []
    n = 0
    for d in common_dates:
        zr = {k.replace("-", "_"): v for k, v in zmap[d].items()}
        for c in cols:
            if c not in amap[d]:
                continue
            n += 1
            if norm(zr[c]) != norm(amap[d][c]):
                mism.append((d, c, str(zr[c]), str(amap[d][c])))
    out = {
        "api_rows": len(api),
        "zip_rows": z.height,
        "api_first": min(amap),
        "api_last": max(amap),
        "dates_only_api": sorted(set(amap) - set(zmap)),
        "dates_only_zip": sorted(set(zmap) - set(amap)),
        "n_common_cols": len(cols),
        "n_cells_compared": n,
        "n_mismatch": len(mism),
        "mismatch_examples": mism[:15],
        "mismatch_cols": sorted({m[1] for m in mism}),
        "api_id_example": api[-1].get("id"),
        "api_extra_cols": sorted(acols - zcols),
        "api_names": sorted({r.get("market_and_exchange_names", "") for r in api}),
        "api_contract_market_names": sorted({r.get("contract_market_name", "") for r in api}),
    }
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()

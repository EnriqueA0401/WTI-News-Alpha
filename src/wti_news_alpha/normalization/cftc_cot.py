"""COT Disaggregated Futures Only (CFTC) point-in-time para NYMEX WTI (067651).

Especificación: ``docs/plans/FASE_02_fundamentales.md`` §B; reglas D9 / R-PIT
(ADR-014) y ADR-021.

Invariantes (``available_at`` en UTC):
- as-of ≥ ``api_first_as_of``: ``:created_at`` de la API + margen (hora real);
  si además hay fecha verificada, deben coincidir (fecha NY) o se lanza error.
- Publicación verificada (anuncios, PR): 18:00 ET de esa fecha (R-PIT 1).
- Resto: regla oficial "3.er día hábil federal tras el as-of". Sin festivos ni
  cierres en medio → 18:00 ET de ese día (R-PIT 1). Con desplazamiento no
  confirmado → 18:00 ET del siguiente día hábil (R-PIT 2).
- Puesta al día de 2019 (fechas reales no verificadas): fecha prevista por la
  CFTC (PR 7864-19) y 18:00 ET del siguiente día hábil federal (R-PIT 2, ADR-022).
- Sin vintages: ``revision_risk`` siempre verdadero.
"""

import io
import json
import sys
import zipfile
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from wti_news_alpha import timeutils
from wti_news_alpha.config import CftcCotConfig
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.normalization import federal_calendar
from wti_news_alpha.normalization.federal_calendar import (
    FederalCalendar,
    check_statute,
    parse_closure_eos,
)
from wti_news_alpha.normalization.store import (
    LayerSpec,
    Published,
    code_hashes,
    publish_or_verify,
)
from wti_news_alpha.timeutils import NEW_YORK, ny_wall_to_utc, require_utc_column

SOURCE = "cftc_cot"
_TUESDAY, _MONDAY = 1, 0
POSITION_COLUMNS = {
    "Open_Interest_All": "open_interest",
    "Prod_Merc_Positions_Long_All": "prod_merc_long",
    "Prod_Merc_Positions_Short_All": "prod_merc_short",
    "Swap_Positions_Long_All": "swap_long",
    "Swap__Positions_Short_All": "swap_short",
    "Swap__Positions_Spread_All": "swap_spread",
    "M_Money_Positions_Long_All": "mm_long",
    "M_Money_Positions_Short_All": "mm_short",
    "M_Money_Positions_Spread_All": "mm_spread",
    "Other_Rept_Positions_Long_All": "other_long",
    "Other_Rept_Positions_Short_All": "other_short",
    "Other_Rept_Positions_Spread_All": "other_spread",
    "NonRept_Positions_Long_All": "nonrept_long",
    "NonRept_Positions_Short_All": "nonrept_short",
}
_UTC: pl.DataType = pl.Datetime("us", "UTC")


# ---------------------------------------------------------------- parseo


def parse_zip(content: bytes, code: str) -> pl.DataFrame:
    """Fichero anual ``fut_disagg_txt_YYYY.zip`` → filas de ``code`` con posiciones Int64."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = z.namelist()
        if len(names) != 1:
            raise SchemaError(f"zip COT con {len(names)} miembros (se espera 1)")
        text = z.read(names[0]).decode("utf-8", errors="strict")
    raw = pl.read_csv(io.StringIO(text), infer_schema=False)
    df = raw.filter(pl.col("CFTC_Contract_Market_Code").str.strip_chars() == code)
    if df.height == 0:
        raise SchemaError(f"zip COT sin filas para {code}")
    out = df.select(
        as_of_date=pl.col("Report_Date_as_YYYY-MM-DD").str.strptime(pl.Date, "%Y-%m-%d"),
        market_name=pl.col("Market_and_Exchange_Names").str.strip_chars(),
        **{
            new: pl.col(old).str.strip_chars().cast(pl.Int64, strict=False)
            for old, new in POSITION_COLUMNS.items()
        },
    )
    n_bad = sum(out.select(list(POSITION_COLUMNS.values())).null_count().row(0))
    if n_bad:
        raise SchemaError(f"COT {code}: {n_bad} posiciones que no son entero")
    dup = out.filter(pl.col("as_of_date").is_duplicated())
    if dup.height:
        raise SchemaError(
            f"COT {code}: as-of duplicados {dup['as_of_date'].unique().to_list()[:3]}"
        )
    bad_day = out.filter(~pl.col("as_of_date").dt.weekday().is_in([_MONDAY + 1, _TUESDAY + 1]))
    if bad_day.height:
        raise SchemaError(
            f"COT {code}: as-of que no son martes (ni lunes) {bad_day['as_of_date'].to_list()[:3]}"
        )
    return out.sort("as_of_date")


def parse_created_at(text: str, code: str) -> dict[date, datetime]:
    """``:created_at`` (UTC) por as-of de las filas Futures Only de ``code``."""
    out: dict[date, datetime] = {}
    for r in json.loads(text):
        if not r["id"].endswith(f"{code}F"):
            continue
        as_of = date.fromisoformat(r["report_date_as_yyyy_mm_dd"][:10])
        if as_of in out:
            raise SchemaError(f"created_at duplicado para el as-of {as_of}")
        out[as_of] = datetime.fromisoformat(r[":created_at"].replace("Z", "+00:00"))
    return out


# ---------------------------------------------------------------- disponibilidad


def _row(as_of: date, release: date, avail: datetime, status: str) -> dict[str, Any]:
    return {
        "as_of_date": as_of,
        "release_date": release,
        "available_at": avail,
        "time_status": status,
    }


def _api_row(
    a: date, created: dict[date, datetime], verified: dict[date, date], margin: timedelta
) -> dict[str, Any]:
    """Fila de un as-of con hora real de la API (``:created_at`` + margen)."""
    if a not in created:
        raise SchemaError(f"as-of {a} sin created_at en la API")
    ts = created[a]
    local = ts.astimezone(NEW_YORK).date()
    if local <= a:
        raise SchemaError(f"as-of {a}: created_at {ts} antes de cerrar las posiciones")
    if a in verified and verified[a] != local:
        raise SchemaError(f"as-of {a}: created_at {local} ≠ publicación verificada {verified[a]}")
    return _row(a, local, ts + margin, "api_created_at")


def release_rows(
    as_ofs: Sequence[date],
    created: dict[date, datetime],
    cal: FederalCalendar,
    cfg: CftcCotConfig,
) -> pl.DataFrame:
    """Una fila por as-of conservado: ``as_of_date, release_date, available_at, time_status``."""
    n_bday = cfg.release_business_day.require("release_business_day")
    rpit = cfg.rpit_time_et.require("rpit_time_et")
    api_first = cfg.api_first_as_of.require("api_first_as_of")
    margin = timedelta(
        minutes=cfg.api_created_at_margin_minutes.require("api_created_at_margin_minutes")
    )
    verified = cfg.verified_releases.require("verified_releases")
    cu_lo, cu_hi = cfg.catch_up_window.require("catch_up_window")
    expected = cfg.catch_up_expected_releases.require("catch_up_expected_releases")
    rows = []
    for a in sorted(as_ofs):
        monday_ok = not cal.is_business_day(a + timedelta(days=1)) or a in verified
        if a.weekday() == _MONDAY and not monday_ok:  # p. ej. 2020-12-21 (anunciado)
            raise SchemaError(f"as-of en lunes {a} sin martes festivo ni anuncio verificado")
        in_catch_up = cu_lo <= a <= cu_hi and a not in verified
        if in_catch_up and a not in expected:
            raise SchemaError(f"as-of {a} en la puesta al día sin fecha prevista")
        if in_catch_up:
            nxt = cal.nth_business_day_after(expected[a], 1)
            rows.append(_row(a, expected[a], ny_wall_to_utc(nxt, rpit), "catch_up_expected_rpit"))
        elif a >= api_first:
            rows.append(_api_row(a, created, verified, margin))
        elif a in verified:
            rows.append(_row(a, verified[a], ny_wall_to_utc(verified[a], rpit), "verified_release"))
        else:
            rule = cal.nth_business_day_after(a, n_bday)
            if cal.non_business_weekdays_between(a, rule):
                nxt = cal.nth_business_day_after(rule, 1)
                rows.append(_row(a, rule, ny_wall_to_utc(nxt, rpit), "rule_date_unconfirmed_shift"))
            else:
                rows.append(_row(a, rule, ny_wall_to_utc(rule, rpit), "rule_date"))
    schema = pl.Schema(
        {
            "as_of_date": pl.Date(),
            "release_date": pl.Date(),
            "available_at": _UTC,
            "time_status": pl.Utf8(),
        }
    )
    out = pl.DataFrame(rows, schema=schema)
    bad = out.filter(pl.col("release_date") <= pl.col("as_of_date"))
    if bad.height:
        raise SchemaError(f"publicación no posterior al as-of: {bad['as_of_date'].to_list()[:3]}")
    return out


# ---------------------------------------------------------------- ejecución


def _build(
    zips: dict[str, bytes],
    sys_text: str,
    statute: str,
    eos: str,
    cfg: CftcCotConfig,
) -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
    check_statute(statute)
    closures = parse_closure_eos(eos)
    first = cfg.first_as_of.require("first_as_of")
    pos = pl.concat([parse_zip(zips[k], cfg.contract_code) for k in sorted(zips)])
    if pos["as_of_date"].is_duplicated().any():
        raise SchemaError("as-of repetidos entre ficheros anuales")
    n_before = pos.filter(pl.col("as_of_date") < first).height
    pos = pos.filter(pl.col("as_of_date") >= first)
    last_year = pos["as_of_date"].max().year + 1  # type: ignore[union-attr]
    cal = FederalCalendar.build(first.year - 1, last_year, closures)
    created = parse_created_at(sys_text, cfg.contract_code)
    rel = release_rows(pos["as_of_date"].to_list(), created, cal, cfg)
    out = (
        pos.join(rel, on="as_of_date", how="inner", validate="1:1")
        .with_columns(revision_risk=pl.lit(True))
        .sort("as_of_date")
    )
    require_utc_column(out, "available_at")
    extra = {
        "n_rows": out.height,
        "before_window": n_before,
        "time_status_counts": dict(sorted(Counter(out["time_status"].to_list()).items())),
        "first_as_of": str(out["as_of_date"].min()),
        "last_as_of": str(out["as_of_date"].max()),
    }
    return {"cot.parquet": out}, extra


def run_normalize(
    *, raw_root: Path, out_root: Path, manifest_dir: Path, cfg: CftcCotConfig, commit: str
) -> Published:
    """Verifica las entradas raw y publica (o verifica) ``cot.parquet``."""
    store = RawStore(raw_root, manifest_dir, commit)
    hashes: dict[str, str] = {}

    def read(source: str, rel: str) -> bytes:
        hashes[rel] = store.verify(source, rel).sha256
        return (raw_root / rel).read_bytes()

    zip_rels = sorted(
        p.relative_to(raw_root).as_posix() for p in raw_root.glob(cfg.inputs.zips_glob)
    )
    if not zip_rels:
        raise RawIntegrityError(f"no hay ficheros {cfg.inputs.zips_glob}")
    zips = {rel: read("cftc", rel) for rel in zip_rels}
    sys_text = read("cftc", cfg.inputs.socrata_systemfields).decode("utf-8")
    statute = read("federal_calendar", cfg.inputs.statute).decode("utf-8")
    eos = read("federal_calendar", cfg.inputs.closure_eos).decode("utf-8")
    spec = LayerSpec(out_root, manifest_dir, SOURCE, cfg.version)
    params = cfg.model_dump(mode="json", by_alias=True, exclude={"version"})
    return publish_or_verify(
        spec,
        hashes,
        params,
        lambda: _build(zips, sys_text, statute, eos, cfg),
        commit=commit,
        code=code_hashes(sys.modules[__name__], federal_calendar, timeutils),
    )

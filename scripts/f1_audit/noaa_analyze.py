"""F1 (NOAA Storm Events): cobertura, retraso de publicación, revisiones y relevancia.

Subcomandos (todas las entradas son ficheros .csv.gz guardados en data/raw/noaa):
  coverage FILE...        nº eventos por mes de inicio y columnas temporales presentes
  certlag FILE            (formato "database") retraso LAST_CERT/LAST_MOD vs fin del evento
  diff OLD NEW            eventos añadidos/eliminados/modificados entre dos versiones
  relevance FILE...       eventos en estados/zonas petroleras por tipo de evento
Uso: uv run python -I scripts/f1_audit/noaa_analyze.py SUBCMD FILE...
"""

import json
import sys
from pathlib import Path

import polars as pl

OIL_STATES = ["TEXAS", "LOUISIANA", "OKLAHOMA", "NEW MEXICO", "NORTH DAKOTA"]
GULF_MARINE = ["GULF OF MEXICO", "GULF OF AMERICA"]
TYPES = [
    "Hurricane",
    "Hurricane (Typhoon)",
    "Tropical Storm",
    "Tropical Depression",
    "Storm Surge/Tide",
    "Marine Hurricane/Typhoon",
    "Marine Tropical Storm",
    "Marine Tropical Depression",
    "Winter Storm",
    "Ice Storm",
    "Extreme Cold/Wind Chill",
    "Cold/Wind Chill",
    "Frost/Freeze",
    "Heavy Snow",
    "Blizzard",
    "Winter Weather",
    "Flood",
    "Flash Flood",
    "Coastal Flood",
]


def read(path: str) -> pl.DataFrame:
    return pl.read_csv(path, infer_schema=False, encoding="utf8-lossy")


def coverage(paths: list[str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for p in paths:
        df = read(p)
        by_month = df.group_by("BEGIN_YEARMONTH").len().sort("BEGIN_YEARMONTH")
        out[Path(p).name] = {
            "n_events": df.height,
            "n_episodes": df["EPISODE_ID"].n_unique(),
            "by_begin_month": {r["BEGIN_YEARMONTH"]: r["len"] for r in by_month.to_dicts()},
            "max_end_yearmonth": df["END_YEARMONTH"].max(),
            "time_like_columns": [
                c for c in df.columns if any(k in c for k in ("DATE", "TIME", "MOD", "CERT"))
            ],
            "data_source_values": df["DATA_SOURCE"].unique().sort().to_list(),
        }
    return out


def certlag(path: str) -> dict[str, object]:
    df = read(path).with_columns(
        pl.col("END_DATE_TIME").str.to_datetime("%d-%b-%y %H:%M:%S", strict=False).alias("end"),
        pl.col("LAST_CERT").str.to_datetime("%m/%d/%Y %H:%M:%S", strict=False).alias("cert"),
        pl.col("LAST_MOD").str.to_datetime("%m/%d/%Y %H:%M:%S", strict=False).alias("mod"),
    )
    lag = df.with_columns(
        ((pl.col("cert") - pl.col("end")).dt.total_hours() / 24).alias("cert_lag_d")
    )
    ok = lag.filter(pl.col("cert_lag_d").is_not_null())
    q = ok["cert_lag_d"]
    by_month = (
        ok.group_by("BEGIN_YEARMONTH")
        .agg(
            pl.len(),
            pl.col("cert_lag_d").min().alias("min_d"),
            pl.col("cert_lag_d").median().alias("median_d"),
            pl.col("cert_lag_d").max().alias("max_d"),
        )
        .sort("BEGIN_YEARMONTH")
    )
    return {
        "file": Path(path).name,
        "n_events": df.height,
        "n_with_cert": ok.height,
        "n_cert_null": df.height - ok.height,
        "cert_min": str(df["cert"].min()),
        "cert_max": str(df["cert"].max()),
        "mod_max": str(df["mod"].max()),
        "lag_days_quantiles": {
            str(k): round(float(q.quantile(k) or 0), 1) for k in (0.0, 0.1, 0.5, 0.9, 1.0)
        },
        "n_cert_before_event_end": int((q < 0).sum()),
        "by_begin_month": [
            {k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()}
            for r in by_month.to_dicts()
        ],
        "addcorr_flg_values": df["ADDCORR_FLG"].value_counts().sort("ADDCORR_FLG").to_dicts(),
    }


COMPARE = [
    "EVENT_TYPE",
    "BEGIN_DATE_TIME",
    "END_DATE_TIME",
    "DAMAGE_PROPERTY",
    "DAMAGE_CROPS",
    "DEATHS_DIRECT",
    "INJURIES_DIRECT",
    "MAGNITUDE",
    "CZ_FIPS",
    "STATE",
]


def diff(old_p: str, new_p: str) -> dict[str, object]:
    old, new = read(old_p), read(new_p)
    months = old["BEGIN_YEARMONTH"].unique().to_list()
    new_same = new.filter(pl.col("BEGIN_YEARMONTH").is_in(months))
    o_ids, n_ids = set(old["EVENT_ID"]), set(new_same["EVENT_ID"])
    j = old.select(["EVENT_ID", *COMPARE]).join(
        new.select(["EVENT_ID", *COMPARE]), on="EVENT_ID", how="inner", validate="1:1"
    )
    changed = {c: int((j[c].fill_null("") != j[f"{c}_right"].fill_null("")).sum()) for c in COMPARE}
    any_changed = j.filter(
        pl.any_horizontal(
            [(pl.col(c).fill_null("") != pl.col(f"{c}_right").fill_null("")) for c in COMPARE]
        )
    ).height
    added = new_same.filter(pl.col("EVENT_ID").is_in(sorted(n_ids - o_ids)))
    return {
        "old": Path(old_p).name,
        "new": Path(new_p).name,
        "months_in_old": sorted(months),
        "n_old": old.height,
        "n_new_same_months": new_same.height,
        "n_added": len(n_ids - o_ids),
        "n_removed": len(o_ids - n_ids),
        "n_common": j.height,
        "n_common_any_field_changed": any_changed,
        "changed_by_field": changed,
        "added_by_month": {
            r["BEGIN_YEARMONTH"]: r["len"]
            for r in added.group_by("BEGIN_YEARMONTH").len().sort("BEGIN_YEARMONTH").to_dicts()
        },
    }


def relevance(paths: list[str]) -> dict[str, object]:
    out: dict[str, object] = {}
    for p in paths:
        df = read(p)
        region = (
            pl.when(pl.col("STATE").is_in(OIL_STATES))
            .then(pl.col("STATE"))
            .when(pl.col("STATE").is_in(GULF_MARINE))
            .then(pl.lit("GULF MARINE"))
            .otherwise(None)
            .alias("region")
        )
        sel = df.with_columns(region).filter(pl.col("region").is_not_null())
        tab = (
            sel.filter(pl.col("EVENT_TYPE").is_in(TYPES))
            .group_by("EVENT_TYPE", "region")
            .len()
            .pivot(on="region", index="EVENT_TYPE", values="len")
            .fill_null(0)
            .sort("EVENT_TYPE")
        )
        tropical = (
            sel.filter(pl.col("EVENT_TYPE").str.contains("(?i)hurricane|tropical|surge"))
            .group_by("BEGIN_YEARMONTH", "region")
            .len()
            .sort("BEGIN_YEARMONTH", "region")
        )
        cold = (
            sel.filter(
                pl.col("EVENT_TYPE").str.contains("(?i)winter|ice|cold|freeze|snow|blizzard")
            )
            .group_by("BEGIN_YEARMONTH", "region")
            .len()
            .sort("BEGIN_YEARMONTH", "region")
        )
        out[Path(p).name] = {
            "n_events_total": df.height,
            "n_events_oil_regions": sel.height,
            "n_by_region": {
                r["region"]: r["len"]
                for r in sel.group_by("region").len().sort("region").to_dicts()
            },
            "marine_state_values": df.filter(pl.col("CZ_TYPE") == "M")["STATE"]
            .value_counts()
            .sort("count", descending=True)
            .head(12)
            .to_dicts(),
            "by_type_region": tab.to_dicts(),
            "tropical_by_month": tropical.to_dicts(),
            "winter_cold_by_month": cold.to_dicts(),
        }
    return out


def main() -> None:
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "coverage":
        res: object = coverage(args)
    elif cmd == "certlag":
        res = certlag(args[0])
    elif cmd == "diff":
        res = diff(args[0], args[1])
    elif cmd == "relevance":
        res = relevance(args)
    else:
        raise SystemExit(f"subcomando desconocido: {cmd}")
    json.dump(res, sys.stdout, indent=1, default=str, ensure_ascii=False)


if __name__ == "__main__":
    main()

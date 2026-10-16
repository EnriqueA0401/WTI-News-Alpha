"""Vintages ALFRED point-in-time de series diarias de FRED.

Especificación: ``docs/plans/FASE_02_fundamentales.md`` §C; regla D9 / R-PIT 4.

Invariantes:
- Entrada: respuestas ``output_type=1`` por ventanas anuales de tiempo real; cada
  fila es (fecha de observación, valor, periodo de vigencia [realtime_start,
  realtime_end]). Los periodos partidos por el borde de ventana se fusionan.
- Un valor "." (sin dato o dato borrado) se conserva como nulo: así un borrado
  posterior no deja vivo el valor anterior.
- ``available_at`` = 00:00 ET del día siguiente a ``vintage_start`` (ALFRED no da
  la hora), y nunca antes del día siguiente a la observación (ADR-024).
- La primera ventana recorta los periodos previos: se marca
  ``vintage_start_clipped`` (disponibilidad más tardía que la real: conservador).
"""

import itertools
import json
import sys
from datetime import date, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from wti_news_alpha import timeutils
from wti_news_alpha.config import FredConfig
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.normalization.store import (
    LayerSpec,
    Published,
    code_hashes,
    publish_or_verify,
)
from wti_news_alpha.timeutils import NEW_YORK, require_utc_column

SOURCE = "fred_alfred"
OPEN_END = date(9999, 12, 31)


def parse_window(text: str, series_id: str) -> tuple[date, date, pl.DataFrame]:
    """Una ventana ALFRED → (inicio, fin, filas).

    Columnas: ``series_id, obs_date, value, rt_start, rt_end``.
    """
    d = json.loads(text)
    if d["output_type"] != 1:
        raise SchemaError(f"{series_id}: output_type={d['output_type']} (se espera 1)")
    obs = d["observations"]
    if d["count"] != len(obs) or len(obs) >= d["limit"]:
        raise SchemaError(f"{series_id}: respuesta truncada ({len(obs)} de {d['count']})")
    lo, hi = date.fromisoformat(d["realtime_start"]), date.fromisoformat(d["realtime_end"])
    values: list[float | None] = []
    for o in obs:
        v = o["value"]
        try:
            values.append(None if v == "." else float(v))
        except ValueError as exc:
            raise SchemaError(f"{series_id} {o['date']}: valor no numérico {v!r}") from exc
    df = pl.DataFrame(
        {
            "series_id": [series_id] * len(obs),
            "obs_date": [date.fromisoformat(o["date"]) for o in obs],
            "value": values,
            "rt_start": [date.fromisoformat(o["realtime_start"]) for o in obs],
            "rt_end": [date.fromisoformat(o["realtime_end"]) for o in obs],
        },
        schema={
            "series_id": pl.Utf8,
            "obs_date": pl.Date,
            "value": pl.Float64,
            "rt_start": pl.Date,
            "rt_end": pl.Date,
        },
    )
    out_of_window = df.filter(
        (pl.col("rt_start") < lo)
        | (pl.col("rt_end") > hi)
        | (pl.col("rt_start") > pl.col("rt_end"))
    )
    if out_of_window.height:
        raise SchemaError(
            f"{series_id}: {out_of_window.height} periodos fuera de la ventana {lo}-{hi}"
        )
    return lo, hi, df


def merge_windows(windows: list[tuple[date, date, pl.DataFrame]]) -> pl.DataFrame:
    """Fusiona ventanas contiguas: une periodos partidos por el borde con el mismo valor."""
    windows = sorted(windows, key=lambda w: w[0])
    for (_, hi, _), (lo2, _, _) in itertools.pairwise(windows):
        if lo2 != hi + timedelta(days=1):
            raise SchemaError(f"ventanas no contiguas: {hi} → {lo2}")
    first_lo, last_hi = windows[0][0], windows[-1][1]
    rows = pl.concat([w[2] for w in windows]).sort("series_id", "obs_date", "rt_start")
    merged: list[dict[str, Any]] = []
    for r in rows.iter_rows(named=True):
        prev = merged[-1] if merged else None
        same = (
            prev is not None
            and prev["series_id"] == r["series_id"]
            and prev["obs_date"] == r["obs_date"]
        )
        if same and prev is not None:
            if r["rt_start"] <= prev["rt_end"]:
                raise SchemaError(f"{r['series_id']} {r['obs_date']}: periodos solapados")
            if prev["value"] == r["value"] and prev["rt_end"] + timedelta(days=1) == r["rt_start"]:
                prev["rt_end"] = r["rt_end"]
                continue
        merged.append(dict(r))
    out = pl.DataFrame(merged, schema=rows.schema)
    return out.with_columns(
        vintage_start_clipped=pl.col("rt_start") == first_lo,
        rt_end=pl.when(pl.col("rt_end") == last_hi)
        .then(pl.lit(OPEN_END))
        .otherwise(pl.col("rt_end")),
    ).rename({"rt_start": "vintage_start", "rt_end": "vintage_end"})


def add_available_at(df: pl.DataFrame) -> pl.DataFrame:
    """``available_at`` = 00:00 ET del día siguiente a max(``vintage_start``, ``obs_date``), UTC.

    R-PIT 4 sobre la fecha de vintage. ALFRED fecha algunos valores antes de su
    observación (VIXCLS en festivos federales: el valor del lunes con vintage del
    viernes, ADR-024): el dato nunca puede conocerse antes de que termine su día,
    así que se toma la fecha más tardía y se marca ``vintage_before_obs``.
    """
    base = df.with_columns(
        vintage_before_obs=pl.col("vintage_start") < pl.col("obs_date"),
        _avail_day=pl.max_horizontal("vintage_start", "obs_date"),
    )
    days = sorted(set(base["_avail_day"].to_list()))
    avail = {d: timeutils.ny_wall_to_utc(d + timedelta(days=1), time(0, 0)) for d in days}
    out = base.with_columns(
        available_at=pl.col("_avail_day").replace_strict(
            avail, return_dtype=pl.Datetime("us", "UTC")
        )
    ).drop("_avail_day")
    require_utc_column(out, "available_at")
    early = out.filter(
        pl.col("available_at").dt.convert_time_zone(str(NEW_YORK)).dt.date() <= pl.col("obs_date")
    )
    if early.height:
        raise SchemaError(
            f"{early.height} valores disponibles el mismo día de su observación o antes"
        )
    return out


def _build(texts: dict[str, dict[str, str]]) -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
    parts = []
    for sid in sorted(texts):
        windows = [parse_window(t, sid) for _, t in sorted(texts[sid].items())]
        parts.append(merge_windows(windows))
    out = add_available_at(pl.concat(parts)).sort("series_id", "obs_date", "vintage_start")
    stats = out.group_by("series_id").agg(
        n_rows=pl.len(),
        n_obs=pl.col("obs_date").n_unique(),
        n_null=pl.col("value").null_count(),
        n_clipped=pl.col("vintage_start_clipped").sum(),
        n_vintage_before_obs=pl.col("vintage_before_obs").sum(),
    )
    extra = {
        "by_series": {
            r["series_id"]: {k: int(v) for k, v in r.items() if k != "series_id"}
            for r in stats.sort("series_id").iter_rows(named=True)
        },
        "n_rows": out.height,
    }
    return {"vintages.parquet": out}, extra


def run_normalize(
    *, raw_root: Path, out_root: Path, manifest_dir: Path, cfg: FredConfig, commit: str
) -> Published:
    """Verifica las ventanas raw de cada serie y publica (o verifica) ``vintages.parquet``."""
    cfg.available_rule.require("available_rule")
    store = RawStore(raw_root, manifest_dir, commit)
    hashes: dict[str, str] = {}
    texts: dict[str, dict[str, str]] = {}
    for sid in cfg.series.require("series"):
        rels = sorted(
            p.relative_to(raw_root).as_posix()
            for p in raw_root.glob(cfg.series_glob.format(series=sid))
        )
        if not rels:
            raise RawIntegrityError(f"no hay ventanas ALFRED para {sid}")
        texts[sid] = {}
        for rel in rels:
            hashes[rel] = store.verify("fred", rel).sha256
            texts[sid][rel] = (raw_root / rel).read_text(encoding="utf-8")
    spec = LayerSpec(out_root, manifest_dir, SOURCE, cfg.version)
    params = cfg.model_dump(mode="json", by_alias=True, exclude={"version"})
    return publish_or_verify(
        spec,
        hashes,
        params,
        lambda: _build(texts),
        commit=commit,
        code=code_hashes(sys.modules[__name__], timeutils),
    )

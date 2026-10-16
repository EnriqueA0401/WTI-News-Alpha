"""Vintages ALFRED point-in-time de series diarias de FRED.

Especificación: ``docs/plans/FASE_02_fundamentales.md`` §C; regla D9 / R-PIT 4;
ADR-024 y ADR-025.

Invariantes:
- Entrada: respuestas ``output_type=1`` por ventanas anuales de tiempo real; cada
  fila es (fecha de observación, valor, periodo [realtime_start, realtime_end]).
  Los periodos partidos por el borde de ventana se fusionan (vectorizado).
- Un valor "." (sin dato o dato borrado) se conserva como nulo: así un borrado
  posterior no deja vivo el valor anterior. Valores no finitos → error.
- ``available_at`` (UTC) = el máximo de:
  (a) 00:00 ET del día siguiente a ``vintage_start`` (R-PIT 4);
  (b) la hora oficial de publicación de la observación según su calendario
      (H.15: siguiente día hábil federal 16:15 ET; H.10: lunes siguiente a la
      semana, o siguiente hábil, 16:15 ET; sin calendario: 00:00 ET del día
      siguiente a la observación). ALFRED a veces fecha vintages antes de que
      existan (ADR-024/025): (b) es la cota de seguridad.
- Por observación, ``available_at`` es estrictamente creciente; si varias
  vintages caen en el mismo instante, solo la última es visible y se cuenta.
- ``superseded_at`` = ``available_at`` de la vintage siguiente (nulo si es la
  vigente). Consulta point-in-time: ``available_at ≤ t < superseded_at``.
"""

import itertools
import json
import math
import re
import sys
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from wti_news_alpha import timeutils
from wti_news_alpha.config import FredConfig
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
from wti_news_alpha.timeutils import require_utc_column

SOURCE = "fred_alfred"
OPEN_END = date(9999, 12, 31)
_KEYS = ("series_id", "obs_date")
_UTC = pl.Datetime("us", "UTC")
_MONDAY = 0
_WINDOW_RE = re.compile(r"vintages_rt(\d{4})_")


def parse_window(text: str, series_id: str) -> tuple[date, date, pl.DataFrame, str]:
    """Una ventana ALFRED → (inicio, fin, filas, ``observation_start``).

    Filas: ``series_id, obs_date, value, rt_start, rt_end``.
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
            x = None if v == "." else float(v)
        except ValueError as exc:
            raise SchemaError(f"{series_id} {o['date']}: valor no numérico {v!r}") from exc
        if x is not None and not math.isfinite(x):
            raise SchemaError(f"{series_id} {o['date']}: valor no finito {v!r}")
        values.append(x)
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
    bad = df.filter(
        (pl.col("rt_start") < lo)
        | (pl.col("rt_end") > hi)
        | (pl.col("rt_start") > pl.col("rt_end"))
    )
    if bad.height:
        raise SchemaError(f"{series_id}: {bad.height} periodos fuera de la ventana {lo}-{hi}")
    return lo, hi, df, d["observation_start"]


def merge_windows(
    windows: list[tuple[date, date, pl.DataFrame, str]], first_vintage: date | None = None
) -> pl.DataFrame:
    """Fusiona ventanas contiguas: une periodos partidos por el borde con el mismo valor."""
    windows = sorted(windows, key=lambda w: w[0])
    for (_, hi, _, os1), (lo2, _, _, os2) in itertools.pairwise(windows):
        if lo2 != hi + timedelta(days=1):
            raise SchemaError(f"ventanas no contiguas: {hi} → {lo2}")
        if os1 != os2:
            raise SchemaError(f"observation_start distinto entre ventanas: {os1} ≠ {os2}")
    first_lo, last_hi = windows[0][0], windows[-1][1]
    rows = pl.concat([w[2] for w in windows]).sort(*_KEYS, "rt_start")
    prev_end = pl.col("rt_end").shift(1).over(_KEYS)
    prev_val = pl.col("value").shift(1).over(_KEYS)
    rows = rows.with_columns(_prev_end=prev_end, _prev_val=prev_val)
    overlap = rows.filter(pl.col("rt_start") <= pl.col("_prev_end"))
    if overlap.height:
        r = overlap.row(0, named=True)
        raise SchemaError(f"{r['series_id']} {r['obs_date']}: periodos solapados")
    same_val = (pl.col("value") == pl.col("_prev_val")).fill_null(
        pl.col("value").is_null() & pl.col("_prev_val").is_null()
    )
    contiguous = pl.col("rt_start") == pl.col("_prev_end") + timedelta(days=1)
    new_run = pl.col("_prev_end").is_null() | ~(same_val & contiguous.fill_null(False))
    merged = (
        rows.with_columns(_run=new_run.cast(pl.UInt32).cum_sum())
        .group_by(*_KEYS, "_run", maintain_order=True)
        .agg(
            value=pl.col("value").first(),
            vintage_start=pl.col("rt_start").min(),
            vintage_end=pl.col("rt_end").max(),
        )
        .drop("_run")
    )
    clipped = pl.col("vintage_start") == first_lo
    if first_vintage is not None and first_vintage == first_lo:
        clipped = pl.lit(False)
    return merged.with_columns(
        vintage_start_clipped=clipped,
        vintage_end=pl.when(pl.col("vintage_end") == last_hi)
        .then(pl.lit(OPEN_END))
        .otherwise(pl.col("vintage_end")),
    )


def official_release(
    obs: date, schedule: str, cal: FederalCalendar, release_time: time
) -> datetime:
    """Primer instante en que la observación puede estar publicada según su calendario."""
    if schedule == "h15":
        return timeutils.ny_wall_to_utc(cal.nth_business_day_after(obs, 1), release_time)
    if schedule == "h10":
        monday = obs + timedelta(days=7 - obs.weekday())
        day = monday if cal.is_business_day(monday) else cal.nth_business_day_after(monday, 1)
        return timeutils.ny_wall_to_utc(day, release_time)
    if schedule == "none":
        return timeutils.ny_wall_to_utc(obs + timedelta(days=1), time(0, 0))
    raise SchemaError(f"calendario desconocido {schedule!r}")


def add_available_at(
    df: pl.DataFrame,
    schedules: Mapping[str, str],
    cal: FederalCalendar,
    release_time: time,
) -> tuple[pl.DataFrame, int]:
    """Añade ``available_at``, ``superseded_at`` y marcas. Devuelve también las vintages ocultas."""
    pairs = df.select("series_id", "obs_date").unique()
    floor = {
        (s, o): official_release(o, schedules[s], cal, release_time) for s, o in pairs.iter_rows()
    }
    floors = pl.DataFrame(
        {
            "series_id": [k[0] for k in floor],
            "obs_date": [k[1] for k in floor],
            "_floor": list(floor.values()),
        },
        schema={"series_id": pl.Utf8, "obs_date": pl.Date, "_floor": _UTC},
    )
    starts = sorted(set(df["vintage_start"].to_list()))
    rpit = {d: timeutils.ny_wall_to_utc(d + timedelta(days=1), time(0, 0)) for d in starts}
    n0 = df.height
    out = df.join(floors, on=list(_KEYS), how="left", validate="m:1").with_columns(
        _rpit=pl.col("vintage_start").replace_strict(rpit, return_dtype=_UTC)
    )
    if out.height != n0 or out["_floor"].null_count():
        raise SchemaError("cota oficial sin calcular para alguna observación")
    out = out.with_columns(
        available_at=pl.max_horizontal("_rpit", "_floor"),
        vintage_before_obs=pl.col("vintage_start") < pl.col("obs_date"),
        floored_by_schedule=pl.col("_floor") > pl.col("_rpit"),
    ).drop("_rpit", "_floor")
    # Varias vintages en el mismo instante: solo la última es visible.
    out = out.sort(*_KEYS, "vintage_start")
    hidden = out.select(
        pl.col("available_at").is_duplicated().over(_KEYS)
        & (pl.col("vintage_start") != pl.col("vintage_start").last().over(*_KEYS, "available_at"))
    ).to_series()
    n_hidden = int(hidden.sum())
    out = out.filter(~hidden)
    out = out.with_columns(superseded_at=pl.col("available_at").shift(-1).over(_KEYS))
    require_utc_column(out, "available_at")
    require_utc_column(out, "superseded_at", allow_nulls=True)
    if out.filter(pl.col("superseded_at") <= pl.col("available_at")).height:
        raise SchemaError("available_at no estrictamente creciente por observación")
    return out, n_hidden


def _build(
    texts: dict[str, dict[str, str]],
    cal: FederalCalendar,
    cfg: FredConfig,
) -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
    schedules = cfg.release_schedule.require("release_schedule")
    first_vintage = cfg.first_vintage.require("first_vintage")
    release_time = cfg.official_release_time_et.require("official_release_time_et")
    parts = []
    for sid in sorted(texts):
        if sid not in schedules:
            raise SchemaError(f"{sid}: sin calendario de publicación en la configuración")
        windows = []
        for rel, text in sorted(texts[sid].items()):
            win = parse_window(text, sid)
            m = _WINDOW_RE.search(rel)
            if m is None or int(m.group(1)) != win[0].year:
                raise SchemaError(f"{rel}: el año del fichero no coincide con su ventana {win[0]}")
            windows.append(win)
        parts.append(merge_windows(windows, first_vintage.get(sid)))
    out, n_hidden = add_available_at(pl.concat(parts), schedules, cal, release_time)
    out = out.sort("series_id", "obs_date", "vintage_start")
    stats = out.group_by("series_id").agg(
        n_rows=pl.len(),
        n_obs=pl.col("obs_date").n_unique(),
        n_null=pl.col("value").null_count(),
        n_clipped=pl.col("vintage_start_clipped").sum(),
        n_vintage_before_obs=pl.col("vintage_before_obs").sum(),
        n_floored_by_schedule=pl.col("floored_by_schedule").sum(),
    )
    extra = {
        "by_series": {
            r["series_id"]: {k: int(v) for k, v in r.items() if k != "series_id"}
            for r in stats.sort("series_id").iter_rows(named=True)
        },
        "n_rows": out.height,
        "hidden_same_instant_vintages": n_hidden,
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

    def read(source: str, rel: str) -> str:
        hashes[rel] = store.verify(source, rel).sha256
        return (raw_root / rel).read_text(encoding="utf-8")

    for sid in cfg.series.require("series"):
        rels = sorted(
            p.relative_to(raw_root).as_posix()
            for p in raw_root.glob(cfg.series_glob.format(series=sid))
        )
        if not rels:
            raise RawIntegrityError(f"no hay ventanas ALFRED para {sid}")
        texts[sid] = {rel: read("fred", rel) for rel in rels}
    statute = read("federal_calendar", cfg.calendar_inputs.statute)
    eos = read("federal_calendar", cfg.calendar_inputs.closure_eos)
    check_statute(statute)
    cal = FederalCalendar.build(2014, 2027, parse_closure_eos(eos))
    spec = LayerSpec(out_root, manifest_dir, SOURCE, cfg.version)
    params = cfg.model_dump(mode="json", by_alias=True, exclude={"version"})
    return publish_or_verify(
        spec,
        hashes,
        params,
        lambda: _build(texts, cal, cfg),
        commit=commit,
        code=code_hashes(sys.modules[__name__], federal_calendar, timeutils),
    )

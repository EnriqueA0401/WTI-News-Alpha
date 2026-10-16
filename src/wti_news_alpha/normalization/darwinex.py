"""Capa normalizada de precio: Darwinex XTIUSD M1 (hora servidor) → UTC.

Especificación: ``docs/plans/FASE_02_precio.md``.

Invariantes:
- Entrada: CSV SQX con hora de pared del servidor (= America/New_York + offset,
  ``config/darwinex.yaml``), sello = inicio de barra, OHLC sobre el bid, USD/bbl.
- Salida: una fila por barra presente (sin rellenar minutos ausentes), ordenada
  por ``bar_start_utc``; todas las marcas en UTC; ``available_at = bar_end_utc``.
- Una hora NY inexistente o ambigua (DST) lanza ``CalendarError``.
- Las barras en la pausa diaria o fuera de la semana se descartan y se cuentan;
  si superan ``max_drop_fraction`` → ``SchemaError``.
- Las salidas se escriben una vez, en solo lectura, con manifiesto sha256 de
  entradas, parámetros y salidas (``normalization/store.py``).
"""

import hashlib
import io
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandera.errors as pa_errors
import pandera.polars as pa
import polars as pl

from wti_news_alpha.config import DarwinexConfig
from wti_news_alpha.errors import CalendarError, RawIntegrityError, SchemaError
from wti_news_alpha.normalization.store import LayerSpec, publish_or_verify
from wti_news_alpha.timeutils import require_utc_column

SOURCE = "darwinex_xti_m1"
RAW_COLUMNS = ("Date", "Time", "Open", "High", "Low", "Close", "TickVolume", "AvgSpread")
RAW_SCHEMA = pl.Schema(
    {
        "Date": pl.Utf8,
        "Time": pl.Utf8,
        "Open": pl.Float64,
        "High": pl.Float64,
        "Low": pl.Float64,
        "Close": pl.Float64,
        "TickVolume": pl.Int64,
        "AvgSpread": pl.Float64,
    }
)
_UTC: pl.DataType = pl.Datetime("us", "UTC")
OUTPUT_SCHEMA = pl.Schema(
    {
        "bar_start_utc": _UTC,
        "bar_end_utc": _UTC,
        "available_at": _UTC,
        "trade_date": pl.Date(),
        "bid_open": pl.Float64(),
        "bid_high": pl.Float64(),
        "bid_low": pl.Float64(),
        "bid_close": pl.Float64(),
        "tick_volume": pl.Int64(),
        "avg_spread": pl.Float64(),
        "source_line": pl.Int64(),
    }
)
_PRICE = pa.Column(pl.Float64, pa.Check.gt(0), nullable=False)
PANDERA_SCHEMA = pa.DataFrameSchema(
    {
        "bar_start_utc": pa.Column(_UTC, nullable=False, unique=True),
        "bar_end_utc": pa.Column(_UTC, nullable=False),
        "available_at": pa.Column(_UTC, nullable=False),
        "trade_date": pa.Column(pl.Date, nullable=False),
        "bid_open": _PRICE,
        "bid_high": _PRICE,
        "bid_low": _PRICE,
        "bid_close": _PRICE,
        "tick_volume": pa.Column(pl.Int64, pa.Check.gt(0), nullable=False),
        "avg_spread": pa.Column(pl.Float64, pa.Check.ge(0), nullable=False),
        "source_line": pa.Column(pl.Int64, pa.Check.ge(2), nullable=False, unique=True),
    },
    strict=True,
    ordered=True,
)
BAR = timedelta(minutes=1)
_DATE_RE = r"^\d{4}\.\d{2}\.\d{2}$"
_TIME_RE = r"^\d{2}:\d{2}$"
_SATURDAY, _SUNDAY, _FRIDAY = 6, 7, 5


@dataclass(frozen=True)
class DropStats:
    rows_in: int
    rows_out: int
    dropped: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------- parse


def parse_csv(text: str) -> pl.DataFrame:
    """Lee el CSV SQX con tipos explícitos y añade ``source_line`` (1 = cabecera)."""
    header = text.split("\n", 1)[0].strip()
    if header != ",".join(RAW_COLUMNS):
        raise SchemaError(f"cabecera inesperada: {header!r}")
    try:
        df = pl.read_csv(io.StringIO(text), schema=RAW_SCHEMA, has_header=True)
    except pl.exceptions.PolarsError as exc:
        raise SchemaError(f"CSV inválido: {exc}") from exc
    if df.height == 0:
        raise SchemaError("CSV sin filas")
    n_null = sum(df.null_count().row(0))
    if n_null:
        raise SchemaError(f"CSV con {n_null} nulos")
    return df.with_columns(source_line=pl.int_range(2, df.height + 2, dtype=pl.Int64))


# ---------------------------------------------------------------- lógica pura


def _check_raw(raw: pl.DataFrame) -> None:
    floats = ("Open", "High", "Low", "Close", "AvgSpread")
    checks = [
        ("valor no finito (NaN/inf)", ~pl.all_horizontal(pl.col(c).is_finite() for c in floats)),
        ("fecha con formato distinto de YYYY.MM.DD", ~pl.col("Date").str.contains(_DATE_RE)),
        ("hora con formato distinto de HH:MM", ~pl.col("Time").str.contains(_TIME_RE)),
        ("precio ≤ 0", pl.min_horizontal("Open", "High", "Low", "Close") <= 0),
        ("High < max(Open, Close)", pl.col("High") < pl.max_horizontal("Open", "Close")),
        ("Low > min(Open, Close)", pl.col("Low") > pl.min_horizontal("Open", "Close")),
        ("spread < 0", pl.col("AvgSpread") < 0),
        ("TickVolume ≤ 0", pl.col("TickVolume") <= 0),
    ]
    for name, expr in checks:
        bad = raw.filter(expr)
        if bad.height:
            raise SchemaError(
                f"{bad.height} barras con {name} (líneas {bad['source_line'].head(5).to_list()})"
            )


def normalize_bars(raw: pl.DataFrame, cfg: DarwinexConfig) -> tuple[pl.DataFrame, DropStats]:
    """Convierte barras crudas (``parse_csv``) a la capa normalizada en UTC.

    Función pura: no toca disco. Lanza ``SchemaError`` o ``CalendarError``.
    """
    if not cfg.bar_timestamp_is_start.require("bar_timestamp_is_start"):
        raise SchemaError("solo está implementado el sello de barra = inicio del minuto")
    offset = cfg.server_offset_vs_new_york_hours.require("server_offset_vs_new_york_hours")
    brk_start = cfg.daily_break_start_et.require("daily_break_start_et")
    brk_end = cfg.daily_break_end_et.require("daily_break_end_et")
    max_drop = cfg.max_drop_fraction.require("max_drop_fraction")

    _check_raw(raw)
    df = raw.with_columns(
        server=pl.concat_str(["Date", "Time"], separator=" ").str.strptime(
            pl.Datetime("us"), "%Y.%m.%d %H:%M", strict=False
        )
    )
    n_bad = df["server"].null_count()
    if n_bad:
        raise SchemaError(f"{n_bad} filas con fecha/hora inválida (formato YYYY.MM.DD HH:MM)")
    lo, hi = cfg.plausible_server_range.require("plausible_server_range")
    out_of_range = df.filter((pl.col("server").dt.date() < lo) | (pl.col("server").dt.date() > hi))
    if out_of_range.height:
        raise SchemaError(
            f"{out_of_range.height} barras fuera del rango plausible [{lo}, {hi}] "
            f"(líneas {out_of_range['source_line'].head(5).to_list()})"
        )
    if df.height > 1 and not (df["server"].diff().drop_nulls() > timedelta(0)).all():
        raise SchemaError("barras duplicadas o fuera de orden")

    df = df.with_columns(ny=pl.col("server") - pl.duration(hours=offset))
    df = df.with_columns(
        ny_tz=pl.col("ny").dt.replace_time_zone(
            "America/New_York", ambiguous="null", non_existent="null"
        ),
        wd=pl.col("ny").dt.weekday(),
        tod=pl.col("ny").dt.time(),
    )
    n_dst = df["ny_tz"].null_count()
    if n_dst:
        first = df.filter(pl.col("ny_tz").is_null())["ny"][0]
        raise CalendarError(
            f"{n_dst} barras con hora NY inexistente o ambigua (DST), p. ej. {first}"
        )

    outside_week = (
        (pl.col("wd") == _SATURDAY)
        | ((pl.col("wd") == _SUNDAY) & (pl.col("tod") < brk_end))
        | ((pl.col("wd") == _FRIDAY) & (pl.col("tod") >= brk_start))
    )
    in_break = (pl.col("tod") >= brk_start) & (pl.col("tod") < brk_end)
    df = df.with_columns(
        drop_reason=pl.when(outside_week)
        .then(pl.lit("outside_week"))
        .when(in_break)
        .then(pl.lit("daily_break"))
        .otherwise(None)
    )
    counts = df.filter(pl.col("drop_reason").is_not_null())["drop_reason"].value_counts()
    dropped = {r: int(n) for r, n in sorted(counts.iter_rows())}
    n_dropped = sum(dropped.values())
    if n_dropped / df.height > max_drop:
        raise SchemaError(
            f"{n_dropped}/{df.height} barras descartadas ({dropped}) > max_drop_fraction={max_drop}"
        )

    start = pl.col("ny_tz").dt.convert_time_zone("UTC").dt.cast_time_unit("us")
    out = (
        df.filter(pl.col("drop_reason").is_null())
        .select(
            bar_start_utc=start,
            bar_end_utc=start + BAR,
            available_at=start + BAR,
            trade_date=pl.when(pl.col("tod") >= brk_end)
            .then(pl.col("ny").dt.date() + timedelta(days=1))
            .otherwise(pl.col("ny").dt.date()),
            bid_open=pl.col("Open"),
            bid_high=pl.col("High"),
            bid_low=pl.col("Low"),
            bid_close=pl.col("Close"),
            tick_volume=pl.col("TickVolume"),
            avg_spread=pl.col("AvgSpread"),
            source_line=pl.col("source_line"),
        )
        .sort("bar_start_utc")
    )
    validate_output(out, cfg)
    return out, DropStats(rows_in=raw.height, rows_out=out.height, dropped=dropped)


def validate_output(df: pl.DataFrame, cfg: DarwinexConfig) -> None:
    """Valida esquema (pandera) e invariantes temporales de la capa normalizada.

    Toda barra cae en su ventana [``trade_date`` - 1 ``brk_end``, ``trade_date``
    ``brk_start``) en hora de NY y ``trade_date`` es lunes-viernes: así ninguna
    barra queda en la pausa diaria ni en el fin de semana.
    """
    brk_start = cfg.daily_break_start_et.require("daily_break_start_et")
    brk_end = cfg.daily_break_end_et.require("daily_break_end_et")
    try:
        PANDERA_SCHEMA.validate(df, lazy=True)
    except (pa_errors.SchemaError, pa_errors.SchemaErrors) as exc:
        raise SchemaError(f"esquema de salida inválido: {exc}") from exc
    for col in ("bar_start_utc", "bar_end_utc", "available_at"):
        require_utc_column(df, col)
    bad_avail = df.filter(
        (pl.col("available_at") != pl.col("bar_end_utc"))
        | (pl.col("bar_end_utc") - pl.col("bar_start_utc") != BAR)
    )
    if bad_avail.height:
        raise SchemaError(f"{bad_avail.height} filas con available_at/bar_end_utc incoherentes")
    ny = pl.col("bar_start_utc").dt.convert_time_zone("America/New_York").dt.replace_time_zone(None)
    td = pl.col("trade_date").cast(pl.Datetime("us"))
    win_start = td - timedelta(days=1) + pl.duration(hours=brk_end.hour, minutes=brk_end.minute)
    win_end = td + pl.duration(hours=brk_start.hour, minutes=brk_start.minute)
    bad_td = df.filter(
        (ny < win_start) | (ny >= win_end) | (pl.col("trade_date").dt.weekday() > _FRIDAY)
    )
    if bad_td.height:
        raise SchemaError(
            f"{bad_td.height} filas con trade_date incoherente o en pausa/fin de semana "
            f"(líneas {bad_td['source_line'].head(5).to_list()})"
        )


# ---------------------------------------------------------------- I/O


@dataclass(frozen=True)
class NormalizeResult:
    created: bool
    outputs: dict[str, str]
    stats: DropStats


def run_normalize(
    *, raw_root: Path, out_root: Path, manifest_dir: Path, cfg: DarwinexConfig, commit: str
) -> NormalizeResult:
    """Normaliza el CSV raw de ``cfg.input`` y publica un fichero por año de ``trade_date``.

    Si la versión ya existe, solo la verifica (mismas entradas y parámetros) sin
    reescribir. Crearla exige árbol git limpio (``store.publish_or_verify``).
    """
    src = raw_root / cfg.input.rel_path
    if not src.is_file():
        raise RawIntegrityError(f"la entrada {src} no existe")
    data = src.read_bytes()  # se hashea y se decodifica el mismo buffer
    if hashlib.sha256(data).hexdigest() != cfg.input.sha256:
        raise RawIntegrityError(f"sha256 de {cfg.input.rel_path} distinto de config/darwinex.yaml")
    spec = LayerSpec(out_root, manifest_dir, SOURCE, cfg.version)
    params = cfg.model_dump(mode="json", by_alias=True, exclude={"version"})

    def build() -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
        out, stats = normalize_bars(parse_csv(data.decode("utf-8", errors="strict")), cfg)
        frames = {
            f"{year}.parquet": part
            for (year,), part in out.group_by(pl.col("trade_date").dt.year(), maintain_order=True)
        }
        extra = {
            "rows_in": stats.rows_in,
            "rows_out": stats.rows_out,
            "dropped": stats.dropped,
            "bar_start_utc_min": str(out["bar_start_utc"].min()),
            "bar_start_utc_max": str(out["bar_start_utc"].max()),
        }
        return frames, extra

    pub = publish_or_verify(
        spec, {cfg.input.rel_path: cfg.input.sha256}, params, build, commit=commit
    )
    man = pub.manifest
    stats = DropStats(rows_in=man["rows_in"], rows_out=man["rows_out"], dropped=man["dropped"])
    return NormalizeResult(created=pub.created, outputs=pub.outputs, stats=stats)

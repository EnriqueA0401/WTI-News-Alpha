"""Calendario de publicaciones y valores WPSR point-in-time (EIA).

Especificación: ``docs/plans/FASE_02_fundamentales.md`` §A; reglas D7-D9 (ADR-014)
aplicadas según ADR-020.

Invariantes:
- Fecha de publicación verificada = presente en el índice del archivo EIA **y**
  en FRED release 212.
- Semana (viernes de cierre) → primera publicación con fecha ≥ viernes +
  ``min_lag_days``. Validaciones duras: retraso ≤ ``max_lag_days``; toda
  publicación de la ventana recibe ≥ 1 semana; solo las publicaciones
  declaradas reciben 2 semanas; coincide con las excepciones oficiales y con
  las páginas "week ending" del archivo.
- ``available_at`` (UTC): excepción oficial → su hora; miércoles con retraso de
  5 días → 10:30 ET (D8); resto de fechas verificadas → 18:00 ET (R-PIT 1);
  fecha no confirmada por FRED → 18:00 ET del siguiente día laborable (R-PIT 2).
- Valores de la API (revisados, D7): ``revision_risk`` siempre verdadero.
"""

import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import polars as pl

from wti_news_alpha import timeutils
from wti_news_alpha.config import EiaWpsrConfig
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.normalization.store import (
    LayerSpec,
    Published,
    code_hashes,
    publish_or_verify,
)
from wti_news_alpha.timeutils import ny_wall_to_utc, require_utc_column

SOURCE = "eia_wpsr"
_FRIDAY, _WEDNESDAY = 4, 2
_INDEX_RE = re.compile(r"/petroleum/supply/weekly/archive/(\d{4})/(\d{4})_(\d{2})_(\d{2})/")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*([ap])\.m\.$")
_WEEK_ENDING_RE = re.compile(r"week ending ([A-Z][a-z]+ \d{1,2}, \d{4})")
_RELEASE_DATE_RE = re.compile(r"Release Date: ([A-Z][a-z]+ \d{1,2}, \d{4})")
_DATE_FMT = "%B %d, %Y"
_UTC = pl.Datetime("us", "UTC")


# ---------------------------------------------------------------- parseo


def parse_archive_index(html: str) -> list[date]:
    """Fechas de publicación (carpetas ``archive/YYYY/YYYY_MM_DD/``), ordenadas y únicas."""
    found = _INDEX_RE.findall(html)
    bad = [f for f in found if f[0] != f[1]]
    if bad:
        raise SchemaError(f"índice WPSR: año de carpeta incoherente {bad[:3]}")
    days = sorted({date(int(y), int(m), int(d)) for _, y, m, d in found})
    if not days:
        raise SchemaError("índice del archivo WPSR sin publicaciones")
    return days


def parse_fred_release_dates(text: str) -> set[date]:
    """Fechas del release 212 de FRED. Falla si la respuesta está truncada."""
    data = json.loads(text)
    rows = data["release_dates"]
    if data["count"] != len(rows):
        raise SchemaError(f"FRED release dates: count={data['count']} ≠ {len(rows)} filas")
    return {date.fromisoformat(r["date"]) for r in rows}


@dataclass(frozen=True)
class ScheduleException:
    week_ending: date
    release_date: date
    release_time_et: time


def _parse_long_date(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), _DATE_FMT).date()  # noqa: DTZ007 - fecha civil
    except ValueError as exc:
        raise SchemaError(f"fecha ilegible en el calendario: {text!r}") from exc


def _parse_12h(text: str) -> time:
    m = _TIME_RE.match(text.strip())
    if not m:
        raise SchemaError(f"hora ilegible en el calendario: {text!r}")
    h12, minute = int(m.group(1)), int(m.group(2))
    if not 1 <= h12 <= 12 or minute > 59:
        raise SchemaError(f"hora fuera de rango en el calendario: {text!r}")
    return time(h12 % 12 + (12 if m.group(3) == "p" else 0), minute)


class _TableRows(HTMLParser):
    """Filas (texto de cada celda) del ``<tbody>`` de la tabla ``schedule``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.n_row_headers = 0
        self._in_table = self._in_body = False
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table" and "schedule" in (a.get("class") or "").split():
            self._in_table = True
        elif self._in_table and tag == "tbody":
            self._in_body = True
        elif self._in_body and tag == "tr":
            self.rows.append([])
        elif self._in_body and tag in {"td", "th"}:
            self.n_row_headers += tag == "th" and a.get("scope") == "row"
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self.rows:
            self.rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tbody":
            self._in_body = False
        elif tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_schedule_exceptions(html: str) -> list[ScheduleException]:
    """Tabla oficial de excepciones (semana, fecha alternativa, día, hora ET).

    Toda fila del ``<tbody>`` debe parsearse (5 celdas); si no, ``SchemaError``.
    """
    parser = _TableRows()
    parser.feed(html)
    rows = [r for r in parser.rows if r]
    if len(rows) != parser.n_row_headers or html.count('scope="row"') != len(rows):
        raise SchemaError(
            f"calendario WPSR: {len(rows)} filas vs {html.count('scope="row"')} cabeceras de fila"
        )
    out = []
    for cells in rows:
        if len(cells) != 5:
            raise SchemaError(f"fila del calendario con {len(cells)} celdas: {cells}")
        we, rd, day, t, _holiday = cells
        ex = ScheduleException(_parse_long_date(we), _parse_long_date(rd), _parse_12h(t))
        if ex.release_date.strftime("%A") != day.strip():
            raise SchemaError(f"día de la semana incoherente en el calendario: {rd} {day}")
        out.append(ex)
    if not out:
        raise SchemaError("calendario WPSR sin excepciones")
    return out


def parse_week_ending_page(html: str) -> date | None:
    """'week ending <fecha>' de una página del archivo, o ``None`` si no aparece."""
    m = _WEEK_ENDING_RE.search(html)
    return _parse_long_date(m.group(1)) if m else None


def parse_release_date_page(html: str) -> date | None:
    """'Release Date: <fecha>' de una página del archivo, o ``None`` si no aparece."""
    text = " ".join(re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ").split())
    m = _RELEASE_DATE_RE.search(text)
    return _parse_long_date(m.group(1)) if m else None


def parse_api_series(text: str, series_id: str) -> pl.DataFrame:
    """Serie semanal de la API v2 → ``series_id, week_ending, value, units`` ordenado."""
    resp = json.loads(text)["response"]
    rows = resp["data"]
    if not rows:
        raise SchemaError(f"{series_id}: respuesta vacía")
    if int(resp["total"]) != len(rows):
        raise SchemaError(f"{series_id}: total={resp['total']} ≠ {len(rows)} filas (truncada)")
    bad_series = {r["series"] for r in rows} - {series_id}
    if bad_series:
        raise SchemaError(f"{series_id}: filas de otra serie {sorted(bad_series)}")
    for r in rows:
        v = r["value"]
        if isinstance(v, bool) or not isinstance(v, int | float):
            raise SchemaError(f"{series_id} {r['period']}: valor no numérico {v!r}")
    units = {r["units"] for r in rows}
    if len(units) != 1:
        raise SchemaError(f"{series_id}: varias unidades {sorted(units)}")
    weeks = [date.fromisoformat(r["period"]) for r in rows]
    not_friday = [d for d in weeks if d.weekday() != _FRIDAY]
    if not_friday:
        raise SchemaError(f"{series_id}: periodos que no son viernes {not_friday[:3]}")
    dup = [d for d, n in Counter(weeks).items() if n > 1]
    if dup:
        raise SchemaError(f"{series_id}: periodos duplicados {dup[:3]}")
    return pl.DataFrame(
        {
            "series_id": [series_id] * len(rows),
            "week_ending": weeks,
            "value": [float(r["value"]) for r in rows],
            "units": [r["units"] for r in rows],
        },
        schema={
            "series_id": pl.Utf8,
            "week_ending": pl.Date,
            "value": pl.Float64,
            "units": pl.Utf8,
        },
    ).sort("week_ending")


# ---------------------------------------------------------------- calendario


def assign_first_release(
    weeks: Sequence[date],
    releases: Sequence[date],
    *,
    min_lag: int,
    max_lag: int,
    shared: set[date],
) -> dict[date, date]:
    """Semana → primera publicación con fecha ≥ semana + ``min_lag`` (validado)."""
    rel = sorted(releases)
    out: dict[date, date] = {}
    for w in sorted(weeks):
        cand = [r for r in rel if r >= w + timedelta(days=min_lag)]
        if not cand:
            raise SchemaError(f"semana {w} sin publicación posterior en el índice")
        r = cand[0]
        if (r - w).days > max_lag:
            raise SchemaError(f"semana {w} → {r}: retraso de {(r - w).days} días > {max_lag}")
        out[w] = r
    counts = Counter(out.values())
    lo, hi = min(counts), max(counts)
    orphan = [r for r in rel if lo <= r <= hi and r not in counts]
    if orphan:
        raise SchemaError(f"publicaciones sin semana asignada: {orphan[:3]}")
    multi = sorted(r for r, n in counts.items() if n > 1)
    unexpected = [r for r in multi if r not in shared or counts[r] != 2]
    if unexpected:
        raise SchemaError(f"publicaciones con 2 semanas o más no declaradas: {unexpected}")
    missing_shared = sorted(r for r in shared if lo <= r <= hi and counts.get(r, 0) != 2)
    if missing_shared:
        raise SchemaError(f"publicación compartida declarada sin 2 semanas: {missing_shared}")
    return out


def _next_weekday(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def release_table(
    assign: Mapping[date, date],
    fred_dates: set[date],
    exceptions: Sequence[ScheduleException],
    cfg: EiaWpsrConfig,
) -> pl.DataFrame:
    """Una fila por publicación.

    Columnas: ``release_date, available_at, time_status, n_weeks, min_lag_days``.
    """
    standard = cfg.standard_release_time_et.require("standard_release_time_et")
    rpit = cfg.rpit_time_et.require("rpit_time_et")
    min_lag = cfg.min_lag_days.require("min_lag_days")
    by_release: dict[date, list[date]] = {}
    for w, r in assign.items():
        by_release.setdefault(r, []).append(w)
    dup = [d for d, n in Counter(e.release_date for e in exceptions).items() if n > 1]
    if dup:
        raise SchemaError(f"excepciones oficiales duplicadas para {dup}")
    exc = {e.release_date: e for e in exceptions}
    rows: list[dict[str, Any]] = []
    for r in sorted(by_release):
        weeks = by_release[r]
        lag = min((r - w).days for w in weeks)
        max_lag = max((r - w).days for w in weeks)
        if r in exc:
            if exc[r].week_ending not in weeks:
                raise SchemaError(f"excepción oficial {r}: semana {exc[r].week_ending} ∉ {weeks}")
            avail, status = ny_wall_to_utc(r, exc[r].release_time_et), "official_exception"
        elif r not in fred_dates:
            avail, status = ny_wall_to_utc(_next_weekday(r), rpit), "rpit_date_unverified"
        elif r.weekday() == _WEDNESDAY and max_lag == min_lag:  # una sola semana, calendario normal
            avail, status = ny_wall_to_utc(r, standard), "d8_standard"
        else:
            avail, status = ny_wall_to_utc(r, rpit), "rpit_date_verified"
        rows.append(
            {
                "release_date": r,
                "available_at": avail,
                "time_status": status,
                "n_weeks": len(weeks),
                "min_lag_days": lag,
            }
        )
    return pl.DataFrame(
        rows,
        schema={
            "release_date": pl.Date,
            "available_at": _UTC,
            "time_status": pl.Utf8,
            "n_weeks": pl.Int64,
            "min_lag_days": pl.Int64,
        },
    )


def validate_against_pages(
    assign: Mapping[date, date], pages: Mapping[date, str]
) -> dict[str, int]:
    """Comprueba la asignación con las páginas del archivo (fecha de publicación → HTML)."""
    checked = without = 0
    for rd, html in sorted(pages.items()):
        stated = parse_release_date_page(html)
        if stated is not None and stated != rd:
            raise SchemaError(f"página {rd}: 'Release Date' dice {stated}")
        we = parse_week_ending_page(html)
        if we is None:
            without += 1
            continue
        if assign.get(we) != rd:
            raise SchemaError(f"página {rd}: semana {we} asignada a {assign.get(we)}")
        checked += 1
    return {"checked": checked, "without_week_ending": without}


def values_table(
    series: pl.DataFrame,
    assign: Mapping[date, date],
    releases: pl.DataFrame,
    cfg: EiaWpsrConfig,
) -> pl.DataFrame:
    """Valores con su publicación y ``available_at`` (join m:1 validado)."""
    redef = cfg.redefinition.require("redefinition")
    amap = pl.DataFrame(
        {"week_ending": list(assign), "release_date": list(assign.values())},
        schema={"week_ending": pl.Date, "release_date": pl.Date},
    )
    n0 = series.height
    out = series.join(amap, on="week_ending", how="inner", validate="m:1").join(
        releases.select("release_date", "available_at", "time_status"),
        on="release_date",
        how="inner",
        validate="m:1",
    )
    if out.height != n0:
        raise SchemaError(f"join de valores con calendario: {n0} → {out.height} filas")
    out = out.with_columns(
        revision_risk=pl.lit(True),
        known_redefinition=(pl.col("series_id") == redef.series_id)
        & (pl.col("week_ending") < redef.before_week_ending),
    ).sort("series_id", "week_ending")
    require_utc_column(out, "available_at")
    min_lag = cfg.min_lag_days.require("min_lag_days")
    ny_date = pl.col("available_at").dt.convert_time_zone("America/New_York").dt.date()
    early = out.filter(
        (pl.col("release_date") < pl.col("week_ending") + timedelta(days=min_lag))
        | (ny_date < pl.col("release_date"))
    )
    if early.height:
        raise SchemaError(f"{early.height} valores disponibles antes de su publicación")
    return out


# ---------------------------------------------------------------- ejecución


@dataclass(frozen=True)
class _Inputs:
    hashes: dict[str, str]
    texts: dict[str, str]
    pages: dict[date, str]


def _load_inputs(store: RawStore, cfg: EiaWpsrConfig) -> _Inputs:
    hashes: dict[str, str] = {}
    texts: dict[str, str] = {}

    def read(source: str, rel: str) -> str:
        entry = store.verify(source, rel)
        hashes[rel] = entry.sha256
        texts[rel] = (store.raw_root / rel).read_text(encoding="utf-8", errors="strict")
        return texts[rel]

    read("eia", cfg.inputs.archive_index)
    read("fred", cfg.inputs.fred_release_dates)
    read("eia", cfg.inputs.schedule)
    for rel in cfg.series.values():
        read("eia", rel)
    pages: dict[date, str] = {}
    for path in sorted(store.raw_root.glob(cfg.inputs.archive_pages_glob)):
        rel = path.relative_to(store.raw_root).as_posix()
        m = re.search(r"wpsr_(\d{4})_(\d{2})_(\d{2})\.php\.html$", rel)
        if m is None:
            raise RawIntegrityError(f"nombre de página inesperado: {rel}")
        if rel not in store.entries("eia"):
            raise RawIntegrityError(f"{rel} no está en el manifiesto de eia")
        pages[date(int(m.group(1)), int(m.group(2)), int(m.group(3)))] = read("eia", rel)
    return _Inputs(hashes, texts, pages)


def _build(inp: _Inputs, cfg: EiaWpsrConfig) -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
    first = cfg.first_release_date.require("first_release_date")
    releases = parse_archive_index(inp.texts[cfg.inputs.archive_index])
    fred = parse_fred_release_dates(inp.texts[cfg.inputs.fred_release_dates])
    exceptions = parse_schedule_exceptions(inp.texts[cfg.inputs.schedule])
    series = pl.concat(
        [parse_api_series(inp.texts[rel], sid) for sid, rel in sorted(cfg.series.items())]
    )

    min_lag = cfg.min_lag_days.require("min_lag_days")
    max_lag = cfg.max_lag_days.require("max_lag_days")
    lo = first - timedelta(days=max_lag + 7)
    hi = releases[-1] - timedelta(days=min_lag)
    all_weeks = sorted(set(series["week_ending"].to_list()))
    after = [w for w in all_weeks if w > hi]
    weeks = [w for w in all_weeks if lo <= w <= hi]
    assign_all = assign_first_release(
        weeks,
        [r for r in releases if r >= lo],
        min_lag=min_lag,
        max_lag=max_lag,
        shared=set(cfg.known_shared_releases.require("known_shared_releases")),
    )
    pages_stats = validate_against_pages(
        assign_all, {d: t for d, t in inp.pages.items() if d >= first}
    )
    assign = {w: r for w, r in assign_all.items() if r >= first}
    window_releases = [r for r in releases if r >= first]
    unassigned = sorted(set(window_releases) - set(assign.values()))
    if unassigned:
        raise SchemaError(f"publicaciones de la ventana sin semana: {unassigned[:3]}")
    in_index = [e for e in exceptions if e.release_date <= releases[-1]]
    missing_exc = [e.release_date for e in in_index if e.release_date not in set(window_releases)]
    if missing_exc:
        raise SchemaError(f"excepciones oficiales que no están en el índice: {missing_exc}")

    rel_df = release_table(assign, fred, in_index, cfg)
    in_window = pl.col("week_ending").is_in(list(assign))
    kept = series.filter(in_window)
    dropped_rows = (
        series.filter(~in_window)
        .with_columns(
            reason=pl.when(pl.col("week_ending") > hi)
            .then(pl.lit("after_last_release"))
            .otherwise(pl.lit("before_window"))
        )
        .group_by("series_id", "reason")
        .len()
        .sort("series_id", "reason")
    )
    values = values_table(kept, assign, rel_df, cfg)
    extra = {
        "n_releases": rel_df.height,
        "n_values": values.height,
        "time_status_counts": dict(sorted(Counter(rel_df["time_status"].to_list()).items())),
        "excluded_weeks": {
            # semanas del tramo previo (desde ``lo``) publicadas antes de la ventana
            "before_window": sum(1 for r in assign_all.values() if r < first),
            "after_last_release": len(after),
        },
        "dropped_rows_by_series": {
            f"{sid}|{reason}": int(n) for sid, reason, n in dropped_rows.iter_rows()
        },
        "pages": pages_stats,
        "first_release": str(min(window_releases)),
        "last_release": str(releases[-1]),
    }
    return {"releases.parquet": rel_df, "values.parquet": values}, extra


def run_normalize(
    *, raw_root: Path, out_root: Path, manifest_dir: Path, cfg: EiaWpsrConfig, commit: str
) -> Published:
    """Verifica las entradas raw y publica (o verifica) ``releases`` y ``values``."""
    store = RawStore(raw_root, manifest_dir, commit)
    inp = _load_inputs(store, cfg)
    spec = LayerSpec(out_root, manifest_dir, SOURCE, cfg.version)
    params = cfg.model_dump(mode="json", by_alias=True, exclude={"version"})
    return publish_or_verify(
        spec,
        inp.hashes,
        params,
        lambda: _build(inp, cfg),
        commit=commit,
        code=code_hashes(sys.modules[__name__], timeutils),
    )

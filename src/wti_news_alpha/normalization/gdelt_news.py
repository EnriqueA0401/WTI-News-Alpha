"""Noticias normalizadas con ``first_available_at`` (GDELT GKG filtrado).

Especificación: ``docs/plans/FASE_02_noticias.md``; regla D6 (ADR-014).

Invariantes:
- ``first_available_at`` = max(``source_file_ts`` + ``availability_lag_minutes``,
  ``Last-Modified`` del fichero) en UTC (D6 + ADR-026). ``first_seen_at`` es igual
  en el histórico (no hubo captura en vivo).
- ``exclude_reason``: fecha de publicación imposible (más de 14 h por delante de
  ``first_available_at`` o año < 1995). Adelantos menores (husos horarios) solo
  se marcan (``published_ahead``). Los ficheros mensuales se parten por
  ``source_file_ts``: para seleccionar por disponibilidad, filtrar por
  ``first_available_at``.
- Cada día raw se verifica (sha256) contra su manifiesto antes de leerlo.
- No se borra ninguna fila: las incoherencias se marcan (``late_file``,
  ``exclude_reason``). La deduplicación y la relevancia son de F3.
- Escritura mes a mes (streaming) y orden determinista
  (``source_file_ts``, ``row_in_file``).
"""

import gzip
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from wti_news_alpha import timeutils
from wti_news_alpha.config import NewsConfig
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.normalization.store import (
    LayerSpec,
    Published,
    Writer,
    code_hashes,
    publish_or_verify_streaming,
    sha256_file,
)
from wti_news_alpha.timeutils import require_utc_column

SOURCE = "gdelt_news"
RAW_SOURCE = "gdelt_gkg_oil"
_UTC = pl.Datetime("us", "UTC")
_PUB_RE = r"<PAGE_PRECISEPUBTIMESTAMP>(\d{14})</PAGE_PRECISEPUBTIMESTAMP>"
_TITLE_RE = r"<PAGE_TITLE>(.*?)</PAGE_TITLE>"
OUTPUT_COLUMNS = (
    "source_file_ts",
    "row_in_file",
    "gkg_record_id",
    "filter_version",
    "first_available_at",
    "first_seen_at",
    "file_last_modified_utc",
    "late_file",
    "ingested_at",
    "published_at",
    "published_ahead",
    "exclude_reason",
    "url",
    "source_common_name",
    "title",
    "themes",
    "organizations",
    "persons",
    "locations",
    "amounts",
    "tone",
    "match_theme",
    "match_title",
    "match_org",
    "match_url",
)


@dataclass(frozen=True)
class DayEntry:
    day: date
    filter_version: str
    rel_path: str
    sha256: str
    ingested_at: datetime
    last_modified: dict[datetime, datetime | None]


def _utc(text: str) -> datetime:
    ts = datetime.fromisoformat(text)
    return timeutils.ensure_utc(ts)


def _file_ts(name: str) -> datetime:
    return timeutils.ensure_utc(datetime.strptime(name[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC))


def load_day_entries(manifest_dir: Path, filter_version: str) -> dict[date, DayEntry]:
    """Entradas de día de ``manifests/gdelt_gkg_oil/<versión>/*.jsonl.gz``."""
    out: dict[date, DayEntry] = {}
    for path in sorted((manifest_dir / RAW_SOURCE / filter_version).glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                d = json.loads(line)
                if d["filter_version"] != filter_version:
                    raise RawIntegrityError(f"{path.name}: versión {d['filter_version']}")
                day = date.fromisoformat(d["day"])
                if day in out:
                    raise RawIntegrityError(f"{path.name}: día {day} duplicado en el manifiesto")
                lm: dict[datetime, datetime | None] = {}
                for f in d["files"]:
                    ts = _file_ts(f["name"])
                    if ts.date() != day or ts in lm:
                        raise RawIntegrityError(
                            f"{path.name}: fichero {f['name']} incoherente con {day}"
                        )
                    lm[ts] = _utc(f["last_modified_utc"]) if f.get("last_modified_utc") else None
                out[day] = DayEntry(
                    day, filter_version, d["path"], d["sha256"], _utc(d["recorded_at_utc"]), lm
                )
    return out


@dataclass(frozen=True)
class PubRules:
    """Exclusión por fecha de publicación (decisión del usuario, ADR-026)."""

    ahead_tolerance: timedelta
    min_year: int


def normalize_day(
    raw: pl.DataFrame, entry: DayEntry, lag: timedelta, rules: PubRules
) -> tuple[pl.DataFrame, int]:
    """Un día raw → filas normalizadas. Devuelve también los ``published_at`` ilegibles.

    ``first_available_at`` = max(``source_file_ts`` + ``lag``, ``Last-Modified``)
    (ADR-026). Un fichero con filas y sin ``Last-Modified`` → error.
    """
    require_utc_column(raw, "source_file_ts")
    lm = pl.DataFrame(
        {
            "source_file_ts": list(entry.last_modified),
            "file_last_modified_utc": list(entry.last_modified.values()),
        },
        schema={"source_file_ts": _UTC, "file_last_modified_utc": _UTC},
    )
    n0 = raw.height
    df = raw.join(lm, on="source_file_ts", how="left", validate="m:1")
    if df.height != n0:
        raise SchemaError(f"{entry.day}: join con Last-Modified cambia las filas")
    missing = df.filter(~pl.col("source_file_ts").is_in(lm["source_file_ts"].implode()))
    if missing.height:
        raise SchemaError(f"{entry.day}: {missing.height} filas de ficheros fuera del manifiesto")
    no_lm = df.filter(pl.col("file_last_modified_utc").is_null())
    if no_lm.height:
        raise SchemaError(f"{entry.day}: {no_lm.height} filas de ficheros sin Last-Modified")
    pub_raw = pl.col("v2_extras_xml").str.extract(_PUB_RE, 1)
    tone_raw = pl.col("v15_tone").str.split(",").list.first()
    pub_parsed = pub_raw.str.strptime(pl.Datetime("us"), "%Y%m%d%H%M%S", strict=False)
    nominal = pl.col("source_file_ts") + lag
    df = df.with_columns(
        first_available_at=pl.max_horizontal(nominal, "file_last_modified_utc"),
        late_file=pl.col("file_last_modified_utc") > nominal,
        _pub_raw=pub_raw,
        _pub_incoherent=pub_parsed.dt.year() < rules.min_year,
        published_at=pl.when(pub_parsed.dt.year() >= rules.min_year)
        .then(pub_parsed)
        .dt.replace_time_zone("UTC"),
        tone=tone_raw.cast(pl.Float64, strict=False),
        _tone_raw=tone_raw,
    )
    bad_tone = df.filter(pl.col("tone").is_null() & (pl.col("_tone_raw").fill_null("") != ""))
    if bad_tone.height:
        raise SchemaError(f"{entry.day}: {bad_tone.height} tonos no numéricos")
    n_pub_invalid = df.filter(
        pl.col("_pub_raw").is_not_null()
        & pl.col("published_at").is_null()
        & ~pl.col("_pub_incoherent").fill_null(False)
    ).height
    out = df.select(
        "source_file_ts",
        "row_in_file",
        "gkg_record_id",
        filter_version=pl.lit(entry.filter_version),
        first_available_at="first_available_at",
        first_seen_at="first_available_at",
        file_last_modified_utc="file_last_modified_utc",
        late_file="late_file",
        ingested_at=pl.lit(entry.ingested_at).cast(_UTC),
        published_at="published_at",
        published_ahead=(pl.col("published_at") > pl.col("first_available_at")).fill_null(False),
        exclude_reason=pl.when(pl.col("_pub_incoherent").fill_null(False))
        .then(pl.lit("published_at_incoherent"))
        .when(pl.col("published_at") > pl.col("first_available_at") + rules.ahead_tolerance)
        .then(pl.lit("published_after_available"))
        .otherwise(pl.lit(None, dtype=pl.Utf8)),
        url="v2_document_identifier",
        source_common_name="v2_source_common_name",
        title=pl.col("v2_extras_xml").str.extract(_TITLE_RE, 1),
        themes="v1_themes",
        organizations="v1_organizations",
        persons="v1_persons",
        locations="v1_locations",
        amounts="v21_amounts",
        tone="tone",
        match_theme="match_theme",
        match_title="match_title",
        match_org="match_org",
        match_url="match_url",
    ).sort("source_file_ts", "row_in_file")
    for col in ("first_available_at", "first_seen_at", "ingested_at"):
        require_utc_column(out, col)
    if out.filter(pl.col("ingested_at") < pl.col("first_available_at")).height:
        raise SchemaError(f"{entry.day}: ingesta anterior a la disponibilidad (incoherente)")
    return out, n_pub_invalid


def _select_entries(cfg: NewsConfig, manifest_dir: Path) -> list[DayEntry]:
    selected: list[DayEntry] = []
    for src in cfg.sources:
        entries = load_day_entries(manifest_dir, src.filter_version)
        days = [src.start + timedelta(days=i) for i in range((src.end - src.start).days)]
        missing = [d for d in days if d not in entries]
        if missing:
            raise RawIntegrityError(
                f"{src.filter_version}: faltan {len(missing)} días, p. ej. {missing[:3]}"
            )
        selected += [entries[d] for d in days]
    return selected


def _writer(entries: list[DayEntry], raw_root: Path, lag: timedelta, rules: PubRules) -> Writer:
    def write(staging: Path) -> dict[str, Any]:
        by_month: dict[tuple[int, int], list[DayEntry]] = {}
        for e in entries:
            by_month.setdefault((e.day.year, e.day.month), []).append(e)
        counts: dict[str, dict[str, int]] = {}
        for (y, m), month_entries in sorted(by_month.items()):
            parts = []
            n_pub_invalid = 0
            for e in month_entries:
                day_df, n_bad = normalize_day(pl.read_parquet(raw_root / e.rel_path), e, lag, rules)
                parts.append(day_df)
                n_pub_invalid += n_bad
            month = pl.concat(parts).sort("source_file_ts", "row_in_file")
            if month.select(
                pl.struct("source_file_ts", "row_in_file").is_duplicated().any()
            ).item():
                raise SchemaError(f"{y}-{m:02d}: claves de fila duplicadas")
            month.write_parquet(
                staging / f"{y}{m:02d}.parquet", compression="zstd", statistics=True
            )
            counts[f"{y}-{m:02d}"] = {
                "rows": month.height,
                "late_file_rows": int(month["late_file"].sum()),
                "published_after_available": int(
                    (month["exclude_reason"] == "published_after_available").sum()
                ),
                "published_at_incoherent": int(
                    (month["exclude_reason"] == "published_at_incoherent").sum()
                ),
                "published_ahead_flag_only": int(
                    (month["published_ahead"] & month["exclude_reason"].is_null()).sum()
                ),
                "published_at_missing": int(month["published_at"].null_count()),
                "published_at_invalid": n_pub_invalid,
                "title_missing": int(month["title"].null_count()),
            }
        totals = {k: sum(c[k] for c in counts.values()) for k in next(iter(counts.values()))}
        return {"by_month": counts, "totals": totals}

    return write


def run_normalize(
    *, raw_root: Path, out_root: Path, manifest_dir: Path, cfg: NewsConfig, commit: str
) -> Published:
    """Verifica cada día raw contra su manifiesto y publica (o verifica) la capa mensual."""
    code = code_hashes(sys.modules[__name__], timeutils)  # al inicio, junto al commit
    lag = timedelta(minutes=cfg.availability_lag_minutes.require("availability_lag_minutes"))
    rules = PubRules(
        ahead_tolerance=timedelta(
            hours=cfg.published_ahead_tolerance_hours.require("published_ahead_tolerance_hours")
        ),
        min_year=cfg.published_min_year.require("published_min_year"),
    )
    entries = _select_entries(cfg, manifest_dir)
    inputs: dict[str, str] = {}
    for e in entries:
        path = raw_root / e.rel_path
        if not path.is_file() or sha256_file(path) != e.sha256:
            raise RawIntegrityError(f"integridad rota en {e.rel_path}")
        inputs[e.rel_path] = e.sha256
    spec = LayerSpec(out_root, manifest_dir, SOURCE, cfg.version)
    params = cfg.model_dump(mode="json", by_alias=True, exclude={"version"})
    return publish_or_verify_streaming(
        spec,
        inputs,
        params,
        _writer(entries, raw_root, lag, rules),
        commit=commit,
        code=code,
    )

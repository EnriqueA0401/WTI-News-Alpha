"""Ingesta del GKG 2.1 de GDELT por filtrado en streaming (ADR-011, D5).

Cada fichero de 15 min se descarga en memoria, se verifica contra el índice
maestro (tamaño y md5), se filtra con el filtro versionado de
``config/gdelt.yaml`` y se descarta. Por cada día UTC queda:

- ``raw_root/gdelt_gkg_oil/<version>/YYYY/MM/YYYYMMDD.parquet``: las 27
  columnas originales (texto, sin rellenar) de las filas conservadas, más
  ``source_file_ts`` (UTC, timestamp nominal del fichero), ``row_in_file``
  (línea 0-based en el fichero) y los motivos de coincidencia. Se escribe una
  vez, en solo lectura.
- una línea en ``manifest_dir/gdelt_gkg_oil/<version>/YYYY-MM.jsonl.gz`` con
  el sha256 de la salida, el commit y el detalle por fichero de origen
  (md5, tamaño, ``Last-Modified`` en UTC, estado HTTP, filas leídas,
  conservadas y rechazadas).

Política de fallos (CLAUDE.md §6.3): una línea con un nº de campos distinto
de 27, que no empieza por un GKGRECORDID o que va seguida de un fragmento
(registro partido por un salto de línea interno) se rechaza y se cuenta; si
los rechazos superan el umbral del día, el día falla. Un 404 queda registrado
como ``missing_404``. Errores de red/5xx se reintentan con backoff acotado.
Un día fallido no escribe nada y se reporta; la siguiente ejecución lo
reintenta.

Esta capa es raw: no calcula ``first_available_at`` (ADR-012 pendiente).
"""

import gzip
import hashlib
import io
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable
from concurrent.futures import (
    FIRST_COMPLETED,
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import partial
from multiprocessing import get_context
from pathlib import Path
from typing import Literal

import httpx
import polars as pl

from wti_news_alpha.config import GdeltConfig, GkgFilterConfig
from wti_news_alpha.errors import RawIntegrityError, SchemaError, SourceAccessError, WNAError
from wti_news_alpha.ingestion.rawstore import RawStore
from wti_news_alpha.timeutils import ensure_utc

SOURCE = "gdelt_gkg_oil"
N_GKG_FIELDS = 27
GKG_COLUMNS: tuple[str, ...] = (
    "gkg_record_id",
    "v21_date",
    "v2_source_collection_identifier",
    "v2_source_common_name",
    "v2_document_identifier",
    "v1_counts",
    "v21_counts",
    "v1_themes",
    "v2_enhanced_themes",
    "v1_locations",
    "v2_enhanced_locations",
    "v1_persons",
    "v2_enhanced_persons",
    "v1_organizations",
    "v2_enhanced_organizations",
    "v15_tone",
    "v21_enhanced_dates",
    "v2_gcam",
    "v21_sharing_image",
    "v21_related_images",
    "v21_social_image_embeds",
    "v21_social_video_embeds",
    "v21_quotations",
    "v21_all_names",
    "v21_amounts",
    "v21_translation_info",
    "v2_extras_xml",
)
EXTRA_COLUMNS: tuple[str, ...] = (
    "source_file_ts",
    "row_in_file",
    "match_theme",
    "match_title",
    "match_org",
)
OUTPUT_SCHEMA = pl.Schema(
    [(c, pl.String()) for c in GKG_COLUMNS]
    + [
        ("source_file_ts", pl.Datetime("us", "UTC")),
        ("row_in_file", pl.UInt32()),
        ("match_theme", pl.Boolean()),
        ("match_title", pl.Boolean()),
        ("match_org", pl.Boolean()),
    ]
)

_INDEX_URL_RE = re.compile(
    r"^https?://data\.gdeltproject\.org/gdeltv2/(\d{14})\.(export|mentions|gkg)\.(?:csv|CSV)\.zip$"
)
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_RECORD_START_RE = r"^\d{14}-T?\d+\t"
_TITLE_RE = r"<PAGE_TITLE>(.*?)</PAGE_TITLE>"
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
TS14 = "%Y%m%d%H%M%S"

IndexKind = Literal["export", "mentions", "gkg"]
FileStatus = Literal["ok", "missing_404"]
DayStatus = Literal["todo", "done", "orphan"]


# ------------------------------------------------------------------ índice maestro


@dataclass(frozen=True)
class IndexEntry:
    file_ts: datetime  # UTC, timestamp nominal del fichero
    kind: IndexKind
    size: int
    md5: str
    url: str

    @property
    def name(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def parse_masterfilelist(text: str, *, max_malformed: int) -> tuple[list[IndexEntry], int]:
    """Parsea ``masterfilelist.txt`` (``size md5 url`` por línea).

    Devuelve las entradas y el nº de líneas mal formadas (F1: líneas
    truncadas). Más de ``max_malformed`` mal formadas, un timestamp fuera de
    la rejilla de 15 min o un (timestamp, tipo) duplicado → ``SchemaError``.
    """
    entries: list[IndexEntry] = []
    malformed = 0
    seen: set[tuple[datetime, str]] = set()
    for raw in text.splitlines():
        if not raw.strip():
            continue
        parts = raw.split()
        m = _INDEX_URL_RE.match(parts[2]) if len(parts) == 3 else None
        if m is None or not parts[0].isdigit() or not _MD5_RE.match(parts[1]):
            malformed += 1
            continue
        ts = datetime.strptime(m.group(1), TS14).replace(tzinfo=UTC)
        if ts.minute % 15 or ts.second:
            raise SchemaError(f"timestamp fuera de la rejilla de 15 min en el índice: {raw!r}")
        kind: IndexKind = m.group(2)  # type: ignore[assignment]
        if (ts, kind) in seen:
            raise SchemaError(f"entrada duplicada en el índice: {raw!r}")
        seen.add((ts, kind))
        entries.append(IndexEntry(ts, kind, int(parts[0]), parts[1], parts[2]))
    if malformed > max_malformed:
        raise SchemaError(f"{malformed} líneas mal formadas en el índice (máximo {max_malformed})")
    return entries, malformed


def gkg_entries_by_day(
    entries: Iterable[IndexEntry], start: date, end: date
) -> dict[date, list[IndexEntry]]:
    """Ficheros GKG por día UTC en ``[start, end)``; los días sin ficheros quedan vacíos."""
    if end <= start:
        raise SchemaError(f"rango vacío o invertido: [{start}, {end})")
    days: dict[date, list[IndexEntry]] = {}
    d = start
    while d < end:
        days[d] = []
        d += timedelta(days=1)
    for e in sorted(entries, key=lambda x: x.file_ts):
        if e.kind == "gkg" and start <= e.file_ts.date() < end:
            days[e.file_ts.date()].append(e)
    return days


# ------------------------------------------------------------------ filtro


@dataclass(frozen=True)
class FilterStats:
    rows_in: int
    kept: int
    rejected: int
    rejected_maybe_relevant: int
    date_mismatch: int
    match_theme: int
    match_title: int
    match_org: int


def _empty_output() -> pl.DataFrame:
    return pl.DataFrame(schema=OUTPUT_SCHEMA)


def filter_gkg(
    text: str, file_ts: datetime, flt: GkgFilterConfig
) -> tuple[pl.DataFrame, FilterStats]:
    """Valida y filtra el contenido de un fichero GKG.

    ``file_ts`` es el timestamp nominal (UTC) del fichero. Devuelve las filas
    conservadas con ``OUTPUT_SCHEMA`` en el orden del fichero y estadísticas.
    """
    file_ts = ensure_utc(file_ts)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # salto final del fichero, no es un registro
    if not lines:
        return _empty_output(), FilterStats(0, 0, 0, 0, 0, 0, 0, 0)

    raw = pl.DataFrame({"raw": lines}).with_row_index("row_in_file")
    is_start = pl.col("raw").str.contains(_RECORD_START_RE)
    raw = raw.with_columns(
        n_fields=pl.col("raw").str.count_matches("\t") + 1,
        # un fragmento (línea que no empieza por GKGRECORDID) delata que la
        # línea anterior es un registro partido: se rechaza también.
        next_is_fragment=(~is_start).shift(-1, fill_value=False),
        is_start=is_start,
    )
    valid = (pl.col("n_fields") == N_GKG_FIELDS) & pl.col("is_start") & ~pl.col("next_is_fragment")
    rejected = raw.filter(~valid)
    good = raw.filter(valid)

    fields = pl.col("raw").str.split_exact("\t", N_GKG_FIELDS - 1)
    good = good.select(
        fields.struct.rename_fields(list(GKG_COLUMNS)).alias("rec"), "row_in_file"
    ).unnest("rec")

    token_re = "(?:^|_)(?:" + "|".join(flt.theme_tokens) + ")(?:_|$)"
    theme_ok = pl.element().str.contains(token_re)
    for prefix in flt.theme_exclude_prefixes:
        theme_ok = theme_ok & ~pl.element().str.starts_with(prefix)
    title = pl.col("v2_extras_xml").str.extract(_TITLE_RE)
    good = good.with_columns(
        match_theme=pl.col("v1_themes").str.split(";").list.eval(theme_ok).list.any(),
        # sin título no hay coincidencia por título (no es un relleno de datos)
        match_title=pl.when(title.is_null())
        .then(pl.lit(value=False))
        .otherwise(title.str.contains(flt.title_regex)),
        match_org=pl.col("v1_organizations").str.contains(flt.org_regex),
        source_file_ts=pl.lit(file_ts).cast(pl.Datetime("us", "UTC")),
        row_in_file=pl.col("row_in_file").cast(pl.UInt32),
    )
    kept = good.filter(pl.col("match_theme") | pl.col("match_title") | pl.col("match_org"))
    kept = kept.select(list(OUTPUT_SCHEMA.names()))
    if kept.schema != OUTPUT_SCHEMA:  # pragma: no cover - invariante de construcción
        raise SchemaError(f"esquema de salida inesperado: {kept.schema}")
    stats = FilterStats(
        rows_in=good.height,
        kept=kept.height,
        rejected=rejected.height,
        rejected_maybe_relevant=int(rejected["raw"].str.contains(flt.reject_relevance_regex).sum()),
        date_mismatch=int((good["v21_date"] != file_ts.strftime(TS14)).sum()),
        match_theme=int(good["match_theme"].sum()),
        match_title=int(good["match_title"].sum()),
        match_org=int(good["match_org"].sum()),
    )
    return kept, stats


def decode_gkg_zip(content: bytes, expected_member: str) -> tuple[str, bool]:
    """Extrae el único miembro esperado del zip. Devuelve (texto, hubo_reemplazos_utf8)."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = z.namelist()
            if names != [expected_member]:
                raise SchemaError(f"miembro(s) inesperado(s) en el zip: {names}")
            data = z.read(expected_member)
    except zipfile.BadZipFile as exc:
        raise SchemaError(f"zip inválido ({expected_member})") from exc
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        # Se conserva el registro con U+FFFD y queda marcado en el manifiesto.
        return data.decode("utf-8", errors="replace"), True


# ------------------------------------------------------------------ descarga


@dataclass(frozen=True)
class FileRecord:
    name: str
    md5: str
    size: int
    http_status: int
    status: FileStatus
    attempts: int
    last_modified_utc: str | None
    utf8_replaced: bool = False
    rows_in: int = 0
    kept: int = 0
    rejected: int = 0
    rejected_maybe_relevant: int = 0
    date_mismatch: int = 0
    match_theme: int = 0
    match_title: int = 0
    match_org: int = 0


def _last_modified(resp: httpx.Response) -> str | None:
    value = resp.headers.get("last-modified")
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"cabecera Last-Modified inválida: {value!r}") from exc
    return ensure_utc(parsed).isoformat()


def fetch_and_filter(
    client: httpx.Client,
    entry: IndexEntry,
    cfg: GdeltConfig,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[pl.DataFrame | None, FileRecord]:
    """Descarga, verifica (tamaño y md5 del índice) y filtra un fichero GKG."""
    url = cfg.ingest.url_prefix + entry.name
    backoff = cfg.ingest.retry_backoff_s
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = client.get(url, timeout=cfg.ingest.http_timeout_s)
        except httpx.TransportError as exc:
            reason = type(exc).__name__
        else:
            if resp.status_code == 404:
                return None, FileRecord(
                    entry.name, entry.md5, entry.size, 404, "missing_404", attempt, None
                )
            if resp.is_success:
                break
            if resp.status_code not in _RETRY_STATUSES:
                raise SourceAccessError(f"{SOURCE}: HTTP {resp.status_code} en {url}")
            reason = f"HTTP {resp.status_code}"
        if attempt > len(backoff):
            raise SourceAccessError(f"{SOURCE}: {url} falló tras {attempt} intentos ({reason})")
        sleep(backoff[attempt - 1])

    content = resp.content
    if len(content) != entry.size:
        raise RawIntegrityError(f"{entry.name}: tamaño {len(content)} != índice {entry.size}")
    md5 = hashlib.md5(content).hexdigest()  # noqa: S324 - el índice GDELT publica md5
    if md5 != entry.md5:
        raise RawIntegrityError(f"{entry.name}: md5 {md5} != índice {entry.md5}")
    text, replaced = decode_gkg_zip(content, entry.name.removesuffix(".zip"))
    df, st = filter_gkg(text, entry.file_ts, cfg.gkg_filter)
    rec = FileRecord(
        entry.name,
        entry.md5,
        entry.size,
        resp.status_code,
        "ok",
        attempt,
        _last_modified(resp),
        replaced,
        **asdict(st),
    )
    return df, rec


def make_client(timeout_s: float, max_connections: int) -> httpx.Client:
    """Cliente HTTP por proceso (sin redirecciones: el índice fija la URL)."""
    return httpx.Client(
        timeout=timeout_s,
        follow_redirects=False,
        limits=httpx.Limits(max_connections=max_connections),
    )


# ------------------------------------------------------------------ almacén por día


@dataclass(frozen=True)
class StoreSpec:
    """Parte serializable del almacén (se envía a los procesos de trabajo)."""

    raw_root: Path
    filter_version: str

    def rel_path(self, day: date) -> str:
        return f"{SOURCE}/{self.filter_version}/{day:%Y}/{day:%m}/{day:%Y%m%d}.parquet"


@dataclass(frozen=True)
class DayOutput:
    day: date
    rel_path: str
    sha256: str
    n_bytes: int
    n_rows: int
    files: tuple[FileRecord, ...]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class GkgDayStore:
    """Salidas diarias inmutables y su manifiesto (una línea por día)."""

    def __init__(
        self, raw_root: Path, manifest_dir: Path, filter_version: str, commit: str
    ) -> None:
        self.raw_root = raw_root
        self.manifest_dir = manifest_dir
        self.filter_version = filter_version
        self.commit = commit

    def spec(self) -> StoreSpec:
        return StoreSpec(self.raw_root, self.filter_version)

    def manifest_path(self, day: date) -> Path:
        return self.manifest_dir / SOURCE / self.filter_version / f"{day:%Y-%m}.jsonl.gz"

    def _month(self, day: date) -> dict[str, dict[str, object]]:
        path = self.manifest_path(day)
        if not path.exists():
            return {}
        out: dict[str, dict[str, object]] = {}
        for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines():
            rec = json.loads(line)
            if rec["day"] in out:
                raise RawIntegrityError(f"{path}: día duplicado {rec['day']}")
            out[rec["day"]] = rec
        return out

    def status(self, day: date) -> DayStatus:
        """``done`` (registrado y verificado), ``todo`` u ``orphan`` (salida sin manifiesto)."""
        rel = self.spec().rel_path(day)
        path = self.raw_root / rel
        rec = self._month(day).get(day.isoformat())
        if rec is None:
            return "orphan" if path.exists() else "todo"
        if not path.exists():
            raise RawIntegrityError(f"falta el fichero {rel} registrado en el manifiesto")
        if _sha256(path) != rec["sha256"]:
            raise RawIntegrityError(f"checksum distinto del manifiesto en {rel}")
        return "done"

    def record(self, out: DayOutput) -> None:
        """Añade la línea del día al manifiesto (miembro gzip determinista)."""
        if out.day.isoformat() in self._month(out.day):
            raise RawIntegrityError(f"día {out.day} ya registrado en el manifiesto")
        if _sha256(self.raw_root / out.rel_path) != out.sha256:
            raise RawIntegrityError(f"checksum de {out.rel_path} distinto del calculado")
        ok_files = [f for f in out.files if f.status == "ok"]
        line = {
            "day": out.day.isoformat(),
            "source": SOURCE,
            "filter_version": self.filter_version,
            "code_commit": self.commit,
            "recorded_at_utc": datetime.now(tz=UTC).isoformat(),
            "path": out.rel_path,
            "sha256": out.sha256,
            "n_bytes": out.n_bytes,
            "n_rows": out.n_rows,
            "n_files_index": len(out.files),
            "n_files_ok": len(ok_files),
            "n_missing_404": sum(f.status == "missing_404" for f in out.files),
            "rows_in": sum(f.rows_in for f in ok_files),
            "rejected": sum(f.rejected for f in ok_files),
            "rejected_maybe_relevant": sum(f.rejected_maybe_relevant for f in ok_files),
            "files": [asdict(f) for f in out.files],
        }
        payload = (json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        path = self.manifest_path(out.day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as fh:
            fh.write(gzip.compress(payload, mtime=0))


def process_day(  # noqa: PLR0913 - se ejecuta en otro proceso: todo explícito
    day: date,
    entries: list[IndexEntry],
    cfg: GdeltConfig,
    spec: StoreSpec,
    *,
    client_factory: Callable[[], httpx.Client],
    sleep: Callable[[float], None] = time.sleep,
) -> DayOutput:
    """Descarga y filtra los ficheros del día y escribe su salida (una sola vez).

    No toca el manifiesto: lo hace el proceso principal con ``GkgDayStore.record``.
    """
    rel = spec.rel_path(day)
    dest = spec.raw_root / rel
    if dest.exists():
        raise RawIntegrityError(f"{rel} ya existe: no se sobrescribe")
    ordered = sorted(entries, key=lambda e: e.file_ts)
    with client_factory() as client, ThreadPoolExecutor(cfg.ingest.download_threads) as pool:
        results = list(pool.map(lambda e: fetch_and_filter(client, e, cfg, sleep=sleep), ordered))
    records = tuple(r for _, r in results)
    n_lines = sum(r.rows_in + r.rejected for r in records)
    n_rej = sum(r.rejected for r in records)
    if n_lines and n_rej / n_lines > cfg.ingest.max_reject_fraction_per_day:
        raise SchemaError(
            f"{day}: {n_rej}/{n_lines} líneas rechazadas "
            f"(> {cfg.ingest.max_reject_fraction_per_day:.3%})"
        )
    frames = [df for df, _ in results if df is not None]
    out = pl.concat(frames) if frames else _empty_output()
    out = out.sort(["source_file_ts", "row_in_file"], maintain_order=True)

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".partial-")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        out.write_parquet(tmp, compression="zstd", statistics=True)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.link(tmp, dest)  # falla si dest ya existe: nunca sobrescribe
    finally:
        tmp.unlink(missing_ok=True)
    return DayOutput(day, rel, _sha256(dest), dest.stat().st_size, out.height, records)


# ------------------------------------------------------------------ ejecución


@dataclass
class RunSummary:
    already_done: list[date] = field(default_factory=list)
    written: list[date] = field(default_factory=list)
    failed: dict[date, str] = field(default_factory=dict)
    not_run: list[date] = field(default_factory=list)
    stopped_reason: str | None = None

    @property
    def ok(self) -> bool:
        return not self.failed and not self.not_run and self.stopped_reason is None


def _log(logger: logging.Logger, msg: str, **data: object) -> None:
    logger.info(msg, extra={"run": {"step": "ingest_gdelt_gkg", "event": msg, "data": data}})


def _log_error(logger: logging.Logger, msg: str, **data: object) -> None:
    logger.error(msg, extra={"run": {"step": "ingest_gdelt_gkg", "event": msg, "data": data}})


def _pending_days(days: Iterable[date], store: GkgDayStore, summary: RunSummary) -> list[date]:
    """Clasifica cada día: ya hecho, inconsistente (fallo) o pendiente."""
    pending: list[date] = []
    for day in sorted(days):
        try:
            st = store.status(day)
        except RawIntegrityError as exc:
            summary.failed[day] = f"RawIntegrityError: {exc}"
            continue
        if st == "done":
            summary.already_done.append(day)
        elif st == "orphan":
            summary.failed[day] = f"salida huérfana sin manifiesto: {store.spec().rel_path(day)}"
        else:
            pending.append(day)
    return pending


def _collect(
    fut: Future[DayOutput],
    day: date,
    store: GkgDayStore,
    summary: RunSummary,
    logger: logging.Logger,
) -> None:
    """Registra un día terminado; los errores del proyecto quedan como fallo del día."""
    exc = fut.exception()
    if exc is None:
        out = fut.result()
        store.record(out)
        summary.written.append(day)
        _log(logger, "day_written", day=day.isoformat(), n_rows=out.n_rows)
    elif isinstance(exc, WNAError):
        summary.failed[day] = f"{type(exc).__name__}: {exc}"
        _log_error(logger, "day_failed", day=day.isoformat(), error=str(exc))
    else:
        raise exc


def ingest_gkg(  # noqa: PLR0913 - dependencias inyectadas explícitamente
    days: dict[date, list[IndexEntry]],
    cfg: GdeltConfig,
    store: GkgDayStore,
    *,
    executor: Executor,
    client_factory: Callable[[], httpx.Client],
    free_disk_gb: Callable[[], float],
    logger: logging.Logger,
    sleep: Callable[[float], None] = time.sleep,
    allow_dirty: bool = False,
) -> RunSummary:
    """Procesa los días pendientes en ``executor``; registra cada día al terminar.

    Un día que falla con un error del proyecto (``WNAError``) se anota en
    ``failed`` y el resto continúa; cualquier otra excepción es un bug y se
    propaga. Antes de lanzar cada día se comprueba el disco libre.
    """
    if store.commit.endswith("-dirty") and not allow_dirty:
        raise RawIntegrityError(f"árbol git dirty ({store.commit}): commitea antes de ingerir")
    summary = RunSummary()
    queue = iter(_pending_days(days, store, summary))
    in_flight: dict[Future[DayOutput], date] = {}
    max_in_flight = 2 * cfg.ingest.workers
    while True:
        while summary.stopped_reason is None and len(in_flight) < max_in_flight:
            day = next(queue, None)
            if day is None:
                break
            free = free_disk_gb()
            if free < cfg.ingest.min_free_disk_gb:
                summary.stopped_reason = (
                    f"disco libre {free:.1f} GB < mínimo {cfg.ingest.min_free_disk_gb} GB"
                )
                summary.not_run = [day, *queue]
                _log_error(logger, "stopped", reason=summary.stopped_reason)
                break
            fut = executor.submit(
                process_day,
                day,
                days[day],
                cfg,
                store.spec(),
                client_factory=client_factory,
                sleep=sleep,
            )
            in_flight[fut] = day
        if not in_flight:
            break
        done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
        for fut in done:
            _collect(fut, in_flight.pop(fut), store, summary, logger)
    summary.written.sort()
    summary.failed = dict(sorted(summary.failed.items()))
    return summary


def disk_free_gb(path: Path) -> float:
    """Espacio libre (GB) del sistema de ficheros que contiene ``path``."""
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 1e9


def default_executor(workers: int) -> Executor:
    """Procesos ``spawn`` (sin ``fork`` con hilos activos)."""
    return ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"))


def run_pipeline(  # noqa: PLR0913 - punto de entrada con dependencias inyectables
    start: date,
    end: date,
    cfg: GdeltConfig,
    *,
    raw_root: Path,
    manifest_dir: Path,
    index_rel: str,
    commit: str,
    logger: logging.Logger,
    workers: int | None = None,
    executor_factory: Callable[[int], Executor] = default_executor,
    client_factory: Callable[[], httpx.Client] | None = None,
    allow_dirty: bool = False,
) -> RunSummary:
    """Verifica el índice maestro contra su manifiesto y ejecuta la ingesta de ``[start, end)``."""
    index = RawStore(raw_root, manifest_dir, commit).verify("gdelt", index_rel)
    entries, malformed = parse_masterfilelist(
        (raw_root / index_rel).read_text(encoding="utf-8"),
        max_malformed=cfg.ingest.max_malformed_index_lines,
    )
    days = gkg_entries_by_day(entries, start, end)
    _log(
        logger,
        "index_loaded",
        index=index_rel,
        index_sha256=index.sha256,
        malformed_lines=malformed,
        days=len(days),
        files=sum(len(v) for v in days.values()),
    )
    n_workers = workers if workers is not None else cfg.ingest.workers
    factory = client_factory or partial(
        make_client, cfg.ingest.http_timeout_s, cfg.ingest.download_threads
    )
    store = GkgDayStore(raw_root, manifest_dir, cfg.gkg_filter.version, commit)
    with executor_factory(n_workers) as ex:
        return ingest_gkg(
            days,
            cfg,
            store,
            executor=ex,
            client_factory=factory,
            free_disk_gb=partial(disk_free_gb, raw_root),
            logger=logger,
            allow_dirty=allow_dirty,
        )

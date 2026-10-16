"""Ingesta del GKG 2.1 de GDELT por filtrado en streaming (ADR-011, ADR-013).

Cada fichero de 15 min se descarga en memoria, se verifica contra el índice
maestro (tamaño y md5), se filtra con el filtro versionado de
``config/gdelt.yaml`` y se descarta. Por cada día UTC queda:

- ``raw_root/gdelt_gkg_oil/<version>/YYYY/MM/YYYYMMDD.parquet``: las 27
  columnas originales (texto, sin rellenar) de las filas conservadas, más
  ``source_file_ts`` (UTC, timestamp nominal del fichero), ``row_in_file``
  (columnas GKG de ``drop_columns`` excluidas, p. ej. GCAM desde v3),
  (línea 0-based en el fichero) y los motivos de coincidencia. Clave de fila:
  ``(source_file_ts, row_in_file)``; ``gkg_record_id`` NO es único (2015-2018).
  Se escribe una vez, en solo lectura.
- una línea en ``manifest_dir/gdelt_gkg_oil/<version>/YYYY-MM.jsonl.gz`` con
  el sha256 de la salida, el commit, el hash del filtro, el índice usado y el
  detalle por fichero de origen (md5, tamaño, ``Last-Modified`` en UTC,
  estado HTTP, filas leídas, conservadas y rechazadas).

Flujo: los procesos de trabajo escriben en ``staging`` (fuera de raw); solo
el proceso principal, con un lock exclusivo, enlaza la salida en raw y la
registra. Si una ejecución muere entre ambos pasos, la siguiente regenera el
día y adopta la salida solo si el sha256 coincide (la salida es determinista).

Política de fallos (CLAUDE.md §6.3): una línea con un nº de campos distinto
de 27, que no empieza por un GKGRECORDID o cuya línea siguiente no es un
registro bien formado (registro partido) se rechaza y se cuenta; si los
rechazos superan el umbral por fichero o por día, o los 404 superan el
máximo diario, el día falla. Errores de red/5xx se reintentan con backoff
acotado. Un día fallido no escribe nada en raw y se reporta; la siguiente
ejecución lo reintenta.

Esta capa es raw: no calcula ``first_available_at`` (ADR-012 pendiente).
"""

import fcntl
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
import zlib
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import (
    FIRST_COMPLETED,
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from contextlib import contextmanager
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
    "match_url",
)
OUTPUT_SCHEMA = pl.Schema(
    [(c, pl.String()) for c in GKG_COLUMNS]
    + [
        ("source_file_ts", pl.Datetime("us", "UTC")),
        ("row_in_file", pl.UInt32()),
        ("match_theme", pl.Boolean()),
        ("match_title", pl.Boolean()),
        ("match_org", pl.Boolean()),
        ("match_url", pl.Boolean()),
    ]
)

_INDEX_URL_RE = re.compile(
    r"^https?://data\.gdeltproject\.org/gdeltv2/(\d{14})\.(export|mentions|gkg)\.(?:csv|CSV)\.zip$"
)
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_RECORD_START_RE = r"^\d{14}-T?\d+\t"
_ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError)
_MANIFEST_ERRORS = (
    OSError,
    EOFError,
    json.JSONDecodeError,
    UnicodeDecodeError,
    KeyError,
    TypeError,
)
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
    match_url: int


def output_schema(flt: GkgFilterConfig) -> pl.Schema:
    """Esquema de salida: ``OUTPUT_SCHEMA`` sin las columnas GKG de ``flt.drop_columns``."""
    unknown = sorted(set(flt.drop_columns) - set(GKG_COLUMNS))
    if unknown:
        raise SchemaError(f"drop_columns desconocidas o no eliminables: {unknown}")
    return pl.Schema([(n, t) for n, t in OUTPUT_SCHEMA.items() if n not in flt.drop_columns])


def _empty_output(schema: pl.Schema) -> pl.DataFrame:
    return pl.DataFrame(schema=schema)


def filter_sha256(flt: GkgFilterConfig) -> str:
    """Hash del filtro (JSON canónico): detecta cambios sin nueva ``version``."""
    canon = json.dumps(flt.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def filter_gkg(
    text: str, file_ts: datetime, flt: GkgFilterConfig
) -> tuple[pl.DataFrame, FilterStats]:
    """Valida y filtra el contenido de un fichero GKG.

    ``file_ts`` es el timestamp nominal (UTC) del fichero. Una línea es un
    registro válido si tiene 27 campos, empieza por un GKGRECORDID y la línea
    siguiente (si existe) también lo es; si no, se rechaza y se cuenta.
    Devuelve las filas conservadas con ``output_schema(flt)`` en el orden del
    fichero y estadísticas.
    """
    file_ts = ensure_utc(file_ts)
    schema = output_schema(flt)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # salto final del fichero, no es un registro
    if not lines:
        return _empty_output(schema), FilterStats(0, 0, 0, 0, 0, 0, 0, 0, 0)

    raw = pl.DataFrame({"raw": lines}).with_row_index("row_in_file")
    well_formed = pl.col("raw").str.contains(_RECORD_START_RE) & (
        pl.col("raw").str.count_matches("\t") == N_GKG_FIELDS - 1
    )
    raw = raw.with_columns(well_formed=well_formed)
    # Si la línea siguiente no es un registro bien formado, esta puede ser la
    # primera mitad de un registro partido por un salto interno: se rechaza.
    valid = pl.col("well_formed") & pl.col("well_formed").shift(-1, fill_value=True)
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
        match_url=pl.col("v2_document_identifier").str.contains(flt.url_regex),
        source_file_ts=pl.lit(file_ts).cast(pl.Datetime("us", "UTC")),
        row_in_file=pl.col("row_in_file").cast(pl.UInt32),
    )
    kept = good.filter(
        pl.col("match_theme") | pl.col("match_title") | pl.col("match_org") | pl.col("match_url")
    )
    kept = kept.select(list(schema.names()))
    if kept.schema != schema:  # pragma: no cover - invariante de construcción
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
        match_url=int(good["match_url"].sum()),
    )
    return kept, stats


def decode_gkg_zip(content: bytes, expected_member: str) -> tuple[str, int]:
    """Extrae el único miembro esperado del zip.

    Devuelve (texto, nº de caracteres U+FFFD introducidos por UTF-8 inválido).
    Cualquier zip ilegible (cabecera, deflate corrupto, truncado) → ``SchemaError``.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = z.namelist()
            if names != [expected_member]:
                raise SchemaError(f"miembro(s) inesperado(s) en el zip: {names}")
            data = z.read(expected_member)
    except _ZIP_ERRORS as exc:
        raise SchemaError(f"zip inválido ({expected_member}): {type(exc).__name__}") from exc
    try:
        return data.decode("utf-8"), 0
    except UnicodeDecodeError:
        # Se conserva el registro con U+FFFD y se cuenta en el manifiesto.
        text = data.decode("utf-8", errors="replace")
        return text, text.count("�") - data.decode("utf-8", "ignore").count("�")


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
    utf8_replacements: int = 0
    rows_in: int = 0
    kept: int = 0
    rejected: int = 0
    rejected_maybe_relevant: int = 0
    date_mismatch: int = 0
    match_theme: int = 0
    match_title: int = 0
    match_org: int = 0
    match_url: int = 0


def _last_modified(resp: httpx.Response) -> str | None:
    """``Last-Modified`` en ISO UTC; ``None`` si falta (se contabiliza aguas abajo)."""
    value = resp.headers.get("last-modified")
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"cabecera Last-Modified inválida: {value!r}") from exc
    if parsed.tzinfo is None:
        # parsedate devuelve naive con "-0000"; un HTTP-date siempre es GMT (RFC 9110 §5.6.7).
        parsed = parsed.replace(tzinfo=UTC)
    return ensure_utc(parsed).isoformat()


def _check_file_rejects(name: str, st: FilterStats, cfg: GdeltConfig) -> None:
    n_lines = st.rows_in + st.rejected
    tol = cfg.ingest.reject_lines_tolerance_per_file
    if st.rejected > tol and st.rejected / n_lines > cfg.ingest.max_reject_fraction_per_file:
        raise SchemaError(f"{name}: {st.rejected}/{n_lines} líneas rechazadas en el fichero")


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
    text, n_repl = decode_gkg_zip(content, entry.name.removesuffix(".zip"))
    df, st = filter_gkg(text, entry.file_ts, cfg.gkg_filter)
    _check_file_rejects(entry.name, st, cfg)
    rec = FileRecord(
        entry.name,
        entry.md5,
        entry.size,
        resp.status_code,
        "ok",
        attempt,
        _last_modified(resp),
        n_repl,
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
class IndexRef:
    """Índice maestro con el que se generó una salida (va al manifiesto)."""

    rel_path: str
    sha256: str
    last_file_ts: str


@dataclass(frozen=True)
class StoreSpec:
    """Parte serializable del almacén (se envía a los procesos de trabajo)."""

    raw_root: Path
    staging_dir: Path
    filter_version: str

    def rel_path(self, day: date) -> str:
        return f"{SOURCE}/{self.filter_version}/{day:%Y}/{day:%m}/{day:%Y%m%d}.parquet"


@dataclass(frozen=True)
class DayOutput:
    day: date
    rel_path: str
    staged_path: str
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
    """Salidas diarias inmutables y su manifiesto (una línea por día).

    Solo el proceso principal (con ``lock``) publica en raw y escribe el manifiesto.
    """

    def __init__(
        self,
        raw_root: Path,
        manifest_dir: Path,
        flt: GkgFilterConfig,
        commit: str,
        index: IndexRef,
    ) -> None:
        self.raw_root = raw_root
        self.manifest_dir = manifest_dir
        self.filter_version = flt.version
        self.filter_sha256 = filter_sha256(flt)
        self.commit = commit
        self.index = index
        self.staging_dir = raw_root.parent / "staging" / SOURCE / flt.version

    def spec(self) -> StoreSpec:
        return StoreSpec(self.raw_root, self.staging_dir, self.filter_version)

    def _version_dir(self) -> Path:
        return self.manifest_dir / SOURCE / self.filter_version

    def manifest_path(self, day: date) -> Path:
        return self._version_dir() / f"{day:%Y-%m}.jsonl.gz"

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Lock exclusivo por versión; limpia ``staging`` (temporales de ejecuciones muertas)."""
        self._version_dir().mkdir(parents=True, exist_ok=True)
        with (self._version_dir() / ".lock").open("w") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RawIntegrityError(f"otra ejecución de {SOURCE} en curso") from exc
            if self.staging_dir.exists():
                for leftover in self.staging_dir.glob("*.parquet"):
                    leftover.unlink()  # staging no es raw: son temporales sin publicar
            self.staging_dir.mkdir(parents=True, exist_ok=True)
            yield

    def _month(self, day: date) -> dict[str, dict[str, object]]:
        path = self.manifest_path(day)
        if not path.exists():
            return {}
        out: dict[str, dict[str, object]] = {}
        try:
            for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines():
                rec = json.loads(line)
                key = rec["day"]
                if key in out:
                    raise RawIntegrityError(f"{path}: día duplicado {key}")
                out[key] = rec
        except _MANIFEST_ERRORS as exc:
            raise RawIntegrityError(f"manifiesto ilegible {path}: {type(exc).__name__}") from exc
        return out

    def status(self, day: date) -> DayStatus:
        """``done`` (registrado y verificado), ``todo`` u ``orphan`` (salida sin manifiesto)."""
        rel = self.spec().rel_path(day)
        path = self.raw_root / rel
        rec = self._month(day).get(day.isoformat())
        if rec is None:
            return "orphan" if path.exists() else "todo"
        if rec.get("filter_sha256") != self.filter_sha256:
            raise RawIntegrityError(
                f"{day}: el filtro cambió sin cambiar version ({self.filter_version})"
            )
        if not path.exists():
            raise RawIntegrityError(f"falta el fichero {rel} registrado en el manifiesto")
        if _sha256(path) != rec["sha256"]:
            raise RawIntegrityError(f"checksum distinto del manifiesto en {rel}")
        return "done"

    def publish(self, out: DayOutput) -> bool:
        """Enlaza la salida de ``staging`` en raw y la registra. Devuelve si se adoptó.

        Si la salida ya existe en raw sin manifiesto (ejecución muerta entre enlace y
        registro), se adopta solo si su sha256 coincide con la regenerada.
        """
        staged = Path(out.staged_path)
        if _sha256(staged) != out.sha256:
            raise RawIntegrityError(f"checksum de {staged.name} distinto del calculado")
        dest = self.raw_root / out.rel_path
        adopted = dest.exists()
        if adopted:
            if _sha256(dest) != out.sha256:
                raise RawIntegrityError(f"{out.rel_path} huérfana con contenido distinto")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.link(staged, dest)  # falla si dest ya existe: nunca sobrescribe
        staged.unlink()
        self.record(out, adopted=adopted)
        return adopted

    def record(self, out: DayOutput, *, adopted: bool = False) -> None:
        """Añade la línea del día al manifiesto (miembro gzip determinista, con fsync)."""
        if out.day.isoformat() in self._month(out.day):
            raise RawIntegrityError(f"día {out.day} ya registrado en el manifiesto")
        if _sha256(self.raw_root / out.rel_path) != out.sha256:
            raise RawIntegrityError(f"checksum de {out.rel_path} distinto del calculado")
        ok_files = [f for f in out.files if f.status == "ok"]
        line = {
            "day": out.day.isoformat(),
            "source": SOURCE,
            "filter_version": self.filter_version,
            "filter_sha256": self.filter_sha256,
            "code_commit": self.commit,
            "polars_version": pl.__version__,
            "index": asdict(self.index),
            "recorded_at_utc": datetime.now(tz=UTC).isoformat(),
            "adopted": adopted,
            "path": out.rel_path,
            "sha256": out.sha256,
            "n_bytes": out.n_bytes,
            "n_rows": out.n_rows,
            "n_files_index": len(out.files),
            "n_files_ok": len(ok_files),
            "n_missing_404": sum(f.status == "missing_404" for f in out.files),
            "n_no_last_modified": sum(f.last_modified_utc is None for f in ok_files),
            "rows_in": sum(f.rows_in for f in ok_files),
            "rejected": sum(f.rejected for f in ok_files),
            "rejected_maybe_relevant": sum(f.rejected_maybe_relevant for f in ok_files),
            "utf8_replacements": sum(f.utf8_replacements for f in ok_files),
            "files": [asdict(f) for f in out.files],
        }
        payload = (json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        path = self.manifest_path(out.day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as fh:
            fh.write(gzip.compress(payload, mtime=0))
            fh.flush()
            os.fsync(fh.fileno())


def process_day(  # noqa: PLR0913 - se ejecuta en otro proceso: todo explícito
    day: date,
    entries: list[IndexEntry],
    cfg: GdeltConfig,
    spec: StoreSpec,
    *,
    client_factory: Callable[[], httpx.Client],
    sleep: Callable[[float], None] = time.sleep,
) -> DayOutput:
    """Descarga y filtra los ficheros del día y escribe la salida en ``staging``.

    No toca raw ni el manifiesto: lo hace el proceso principal con ``GkgDayStore.publish``.
    """
    ordered = sorted(entries, key=lambda e: e.file_ts)
    with client_factory() as client, ThreadPoolExecutor(cfg.ingest.download_threads) as pool:
        results = list(pool.map(lambda e: fetch_and_filter(client, e, cfg, sleep=sleep), ordered))
    records = tuple(r for _, r in results)
    n_404 = sum(r.status == "missing_404" for r in records)
    if n_404 > cfg.ingest.max_missing_404_per_day:
        raise SourceAccessError(
            f"{day}: {n_404} ficheros 404 (> {cfg.ingest.max_missing_404_per_day})"
        )
    n_lines = sum(r.rows_in + r.rejected for r in records)
    n_rej = sum(r.rejected for r in records)
    if n_lines and n_rej / n_lines > cfg.ingest.max_reject_fraction_per_day:
        raise SchemaError(
            f"{day}: {n_rej}/{n_lines} líneas rechazadas "
            f"(> {cfg.ingest.max_reject_fraction_per_day:.3%})"
        )
    frames = [df for df, _ in results if df is not None]
    out = pl.concat(frames) if frames else _empty_output(output_schema(cfg.gkg_filter))
    out = out.sort(["source_file_ts", "row_in_file"], maintain_order=True)

    spec.staging_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=spec.staging_dir, prefix=f"{day:%Y%m%d}-", suffix=".parquet"
    )
    os.close(fd)
    staged = Path(tmp_name)
    out.write_parquet(staged, compression="zstd", statistics=True)
    os.chmod(staged, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return DayOutput(
        day,
        spec.rel_path(day),
        str(staged),
        _sha256(staged),
        staged.stat().st_size,
        out.height,
        records,
    )


# ------------------------------------------------------------------ ejecución


@dataclass
class RunSummary:
    already_done: list[date] = field(default_factory=list)
    written: list[date] = field(default_factory=list)
    adopted: list[date] = field(default_factory=list)
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


def _pending_days(
    days: Iterable[date], store: GkgDayStore, summary: RunSummary, logger: logging.Logger
) -> list[date]:
    """Clasifica cada día: ya hecho, inconsistente (fallo) o pendiente (incluye huérfanos)."""
    pending: list[date] = []
    for day in sorted(days):
        try:
            st = store.status(day)
        except RawIntegrityError as exc:
            summary.failed[day] = f"RawIntegrityError: {exc}"
            continue
        if st == "done":
            summary.already_done.append(day)
        else:
            if st == "orphan":
                _log(logger, "orphan_found", day=day.isoformat())
            pending.append(day)
    return pending


def _collect(
    fut: Future[DayOutput],
    day: date,
    store: GkgDayStore,
    summary: RunSummary,
    logger: logging.Logger,
) -> None:
    """Publica un día terminado; los errores del proyecto quedan como fallo del día."""
    exc = fut.exception()
    if exc is None:
        out = fut.result()
        try:
            adopted = store.publish(out)
        except RawIntegrityError as err:
            summary.failed[day] = f"RawIntegrityError: {err}"
            _log_error(logger, "day_failed", day=day.isoformat(), error=str(err))
            return
        (summary.adopted if adopted else summary.written).append(day)
        _log(logger, "day_written", day=day.isoformat(), n_rows=out.n_rows, adopted=adopted)
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
    """Procesa los días pendientes en ``executor`` y publica cada día al terminar.

    Un día que falla con un error del proyecto (``WNAError``) se anota en
    ``failed`` y el resto continúa; cualquier otra excepción es un bug: se
    cancelan los días no iniciados y se propaga (lo ya publicado queda
    registrado; lo que quede en ``staging`` se limpia en la siguiente
    ejecución). Antes de lanzar cada día se comprueba el disco libre.
    """
    if store.commit.endswith("-dirty") and not allow_dirty:
        raise RawIntegrityError(f"árbol git dirty ({store.commit}): commitea antes de ingerir")
    summary = RunSummary()
    with store.lock():
        queue = iter(_pending_days(days, store, summary, logger))
        in_flight: dict[Future[DayOutput], date] = {}
        max_in_flight = 2 * cfg.ingest.workers
        try:
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
        except BaseException:
            for fut in in_flight:
                fut.cancel()
            raise
    summary.written.sort()
    summary.adopted.sort()
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
    """Verifica el índice maestro contra su manifiesto y ejecuta la ingesta de ``[start, end)``.

    ``end`` no puede pasar del día del último fichero del índice: los días no
    cubiertos (o cubiertos a medias) se registrarían como completos.
    """
    index = RawStore(raw_root, manifest_dir, commit).verify("gdelt", index_rel)
    entries, malformed = parse_masterfilelist(
        (raw_root / index_rel).read_text(encoding="utf-8"),
        max_malformed=cfg.ingest.max_malformed_index_lines,
    )
    gkg_ts = [e.file_ts for e in entries if e.kind == "gkg"]
    if not gkg_ts:
        raise SchemaError(f"el índice {index_rel} no contiene ficheros GKG")
    last_ts = max(gkg_ts)
    if end > last_ts.date():
        raise SchemaError(
            f"end={end} supera la cobertura completa del índice (último fichero "
            f"{last_ts:%Y-%m-%d %H:%M} UTC): usa end <= {last_ts.date()} o un índice más reciente"
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
    store = GkgDayStore(
        raw_root,
        manifest_dir,
        cfg.gkg_filter,
        commit,
        IndexRef(index_rel, index.sha256, last_ts.isoformat()),
    )
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

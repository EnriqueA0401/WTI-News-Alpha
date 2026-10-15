"""Almacén de datos crudos inmutable con manifiesto (CLAUDE.md §3.3, §13).

Invariantes:
- Un fichero en ``raw_root`` nunca se sobrescribe ni se modifica: se escribe
  una vez (``os.link`` exclusivo desde un temporal) y queda en solo lectura.
- Cada fichero tiene exactamente una entrada en ``manifest_dir/<source>.jsonl``
  con URL, parámetros (secretos redactados), fecha UTC, sha256, bytes y commit.
- Una segunda petición del mismo destino no toca la red si el checksum del
  fichero coincide con el manifiesto (idempotencia); cualquier discrepancia
  (fichero alterado, huérfano o ausente) lanza ``RawIntegrityError``.
"""

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import httpx

from wti_news_alpha.errors import RawIntegrityError, SourceAccessError
from wti_news_alpha.runlog import REDACTED, redact

CHUNK = 1 << 20


@dataclass(frozen=True)
class ManifestEntry:
    source: str
    path: str
    url: str
    params: dict[str, str]
    retrieved_at_utc: str
    http_status: int
    content_type: str
    sha256: str
    n_bytes: int
    code_commit: str


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class RawStore:
    def __init__(self, raw_root: Path, manifest_dir: Path, commit: str) -> None:
        self.raw_root = raw_root
        self.manifest_dir = manifest_dir
        self.commit = commit

    # ---------------------------------------------------------------- manifest

    def _manifest_path(self, source: str) -> Path:
        return self.manifest_dir / f"{source}.jsonl"

    def entries(self, source: str) -> dict[str, ManifestEntry]:
        """Entradas del manifiesto de ``source`` indexadas por ruta relativa."""
        mpath = self._manifest_path(source)
        if not mpath.exists():
            return {}
        out: dict[str, ManifestEntry] = {}
        for line in mpath.read_text(encoding="utf-8").splitlines():
            e = ManifestEntry(**json.loads(line))
            if e.path in out:
                raise RawIntegrityError(f"{mpath}: entrada duplicada para {e.path}")
            out[e.path] = e
        return out

    def _append(self, entry: ManifestEntry) -> None:
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(entry), ensure_ascii=False, sort_keys=True)
        with self._manifest_path(entry.source).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ---------------------------------------------------------------- paths

    def _resolve(self, rel: str) -> Path:
        p = PurePosixPath(rel)
        if p.is_absolute() or ".." in p.parts:
            raise RawIntegrityError(f"ruta {rel!r} fuera de raw_root")
        return self.raw_root / p

    # ---------------------------------------------------------------- fetch

    def fetch(  # noqa: PLR0913 - parámetros explícitos de la petición
        self,
        client: httpx.Client,
        source: str,
        url: str,
        rel_path: str,
        *,
        params: Mapping[str, str] | None = None,
        secret_params: Sequence[str] = (),
        basic_auth: tuple[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> ManifestEntry:
        """Descarga ``url`` a ``raw_root/rel_path`` una sola vez y la registra.

        Si ya existe con checksum válido, devuelve la entrada sin red.
        """
        dest = self._resolve(rel_path)
        known = self.entries(source).get(rel_path)
        if known is not None:
            if not dest.exists():
                raise RawIntegrityError(f"falta el fichero {rel_path} registrado en el manifiesto")
            if _sha256(dest) != known.sha256:
                raise RawIntegrityError(f"checksum distinto del manifiesto en {rel_path}")
            return known
        if dest.exists():
            raise RawIntegrityError(f"{rel_path} existe sin manifiesto: no se sobrescribe")

        full_url = httpx.URL(url).copy_merge_params(dict(params or {}))
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".partial-")
        tmp = Path(tmp_name)
        try:
            h = hashlib.sha256()
            n = 0
            with os.fdopen(fd, "wb") as out:
                try:
                    with client.stream("GET", full_url, auth=basic_auth) as resp:
                        if not 200 <= resp.status_code < 300:
                            raise SourceAccessError(
                                f"{source}: HTTP {resp.status_code} en {redact(str(full_url))}"
                            )
                        status, ctype = resp.status_code, resp.headers.get("content-type", "")
                        for chunk in resp.iter_bytes(CHUNK):
                            n += len(chunk)
                            if max_bytes is not None and n > max_bytes:
                                raise SourceAccessError(f"{source}: supera max_bytes={max_bytes}")
                            h.update(chunk)
                            out.write(chunk)
                except httpx.HTTPError as exc:
                    raise SourceAccessError(f"{source}: {type(exc).__name__}") from exc
            os.chmod(tmp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.link(tmp, dest)  # falla si dest ya existe: nunca sobrescribe
        finally:
            tmp.unlink(missing_ok=True)

        recorded = {k: (REDACTED if k in secret_params else v) for k, v in (params or {}).items()}
        entry = ManifestEntry(
            source=source,
            path=rel_path,
            url=redact(url),
            params=recorded,
            retrieved_at_utc=datetime.now(tz=UTC).isoformat(),
            http_status=status,
            content_type=ctype,
            sha256=h.hexdigest(),
            n_bytes=n,
            code_commit=self.commit,
        )
        self._append(entry)
        return entry

    def verify(self, source: str, rel_path: str) -> ManifestEntry:
        """Comprueba un fichero registrado; devuelve su entrada o lanza ``RawIntegrityError``."""
        entry = self.entries(source).get(rel_path)
        if entry is None:
            raise RawIntegrityError(f"{rel_path} no está en el manifiesto de {source}")
        path = self._resolve(rel_path)
        if not path.exists() or _sha256(path) != entry.sha256:
            raise RawIntegrityError(f"integridad rota en {rel_path}")
        return entry

    def verify_all(self, source: str) -> int:
        """Comprueba el checksum de todos los ficheros de ``source``; devuelve cuántos."""
        entries = self.entries(source)
        for rel, e in entries.items():
            path = self._resolve(rel)
            if not path.exists() or _sha256(path) != e.sha256:
                raise RawIntegrityError(f"integridad rota en {rel}")
        return len(entries)

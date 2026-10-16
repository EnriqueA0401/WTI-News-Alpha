"""Publicación inmutable de capas normalizadas (un directorio por versión).

Invariantes:
- Un directorio ``<out_root>/<source>/<version>/`` se escribe una sola vez: se
  construye en staging y se publica con ``os.rename``; los ficheros quedan en
  solo lectura.
- Su manifiesto ``<manifest_dir>/normalized/<source>/<version>.json`` guarda el
  sha256 de cada entrada y de cada salida, los parámetros de configuración, el
  commit y la versión de polars. Se escribe de forma atómica (temporal + rename).
- Si la versión ya existe, solo se verifica (mismas entradas, mismos parámetros,
  mismo código, mismos ficheros, mismos hashes); cualquier discrepancia lanza
  ``RawIntegrityError``: cambiar la configuración o el código exige cambiar la versión.
- Crear una versión exige árbol git limpio (reproducible desde ``commit``).
"""

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import polars as pl

from wti_news_alpha.errors import RawIntegrityError, SchemaError

_DIR_MODE = stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]*\.parquet$")
Builder = Callable[[], tuple[dict[str, pl.DataFrame], dict[str, Any]]]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class Published:
    created: bool
    outputs: dict[str, str]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class LayerSpec:
    out_root: Path
    manifest_dir: Path
    source: str
    version: str

    @property
    def out_dir(self) -> Path:
        return self.out_root / self.source / self.version

    @property
    def manifest_path(self) -> Path:
        return self.manifest_dir / "normalized" / self.source / f"{self.version}.json"


def _canonical(obj: Any) -> Any:
    """Forma JSON canónica (fechas y horas como texto) para comparar parámetros."""
    return json.loads(json.dumps(obj, sort_keys=True, default=str))


_RESERVED = frozenset(
    {"source", "version", "inputs", "params", "code", "outputs", "code_commit", "polars_version"}
)


def code_hashes(*modules: ModuleType) -> dict[str, str]:
    """sha256 del fuente de los módulos que transforman los datos (más este publicador)."""
    out: dict[str, str] = {}
    for mod in (*modules, sys.modules[__name__]):
        src = getattr(mod, "__file__", None)
        if src is None:
            raise RawIntegrityError(f"módulo sin fichero fuente: {mod.__name__}")
        out[mod.__name__] = sha256_file(Path(src))
    return dict(sorted(out.items()))


def _read_manifest(mpath: Path) -> dict[str, Any]:
    try:
        man = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RawIntegrityError(f"{mpath}: manifiesto ilegible ({type(exc).__name__})") from exc
    if not isinstance(man, dict) or not {"inputs", "params", "code", "outputs"} <= set(man):
        raise RawIntegrityError(f"{mpath}: manifiesto incompleto")
    return man


def _check_same_origin(
    man: dict[str, Any],
    spec: LayerSpec,
    inputs: Mapping[str, str],
    params: Mapping[str, Any],
    code: Mapping[str, str],
) -> None:
    name = spec.manifest_path.name
    if man["inputs"] != dict(inputs):
        raise RawIntegrityError(f"{name}: la versión {spec.version} se generó con otras entradas")
    if man["params"] != _canonical(dict(params)):
        raise RawIntegrityError(
            f"{name}: la versión {spec.version} se generó con otros parámetros "
            "(cambia la versión en la configuración)"
        )
    if man["code"] != dict(code):
        raise RawIntegrityError(
            f"{name}: la versión {spec.version} se generó con otro código "
            "(cambia la versión en la configuración)"
        )


def _verify(
    spec: LayerSpec,
    inputs: Mapping[str, str],
    params: Mapping[str, Any],
    code: Mapping[str, str],
) -> Published:
    mpath = spec.manifest_path
    if not mpath.exists():
        raise RawIntegrityError(f"{spec.out_dir} existe sin manifiesto: no se sobrescribe")
    man = _read_manifest(mpath)
    _check_same_origin(man, spec, inputs, params, code)
    present = {p.name for p in spec.out_dir.iterdir()}
    if present != set(man["outputs"]):
        raise RawIntegrityError(f"{spec.out_dir}: ficheros {sorted(present)} ≠ manifiesto")
    for name, sha in man["outputs"].items():
        if sha256_file(spec.out_dir / name) != sha:
            raise RawIntegrityError(f"checksum distinto del manifiesto en {name}")
    return Published(created=False, outputs=dict(man["outputs"]), manifest=man)


def publish_or_verify(  # noqa: PLR0913 - todo lo que determina la salida es explícito
    spec: LayerSpec,
    inputs: Mapping[str, str],
    params: Mapping[str, Any],
    build: Builder,
    *,
    commit: str,
    code: Mapping[str, str],
) -> Published:
    """Publica la versión con ``build()`` (ficheros + campos extra) o verifica la existente.

    ``inputs``: ruta relativa → sha256 de cada entrada ya verificada.
    ``params``: configuración que determina la salida (se compara al verificar).
    ``code``: hashes del código de transformación (``code_hashes``).
    Si un fallo previo dejó el manifiesto sin directorio, se reconstruye y solo
    se publica si los hashes coinciden con el manifiesto (recuperación determinista).
    """
    if spec.out_dir.exists():
        return _verify(spec, inputs, params, code)
    stale = sorted(spec.out_dir.parent.glob(f".staging-{spec.version}-*"))
    if stale:
        raise RawIntegrityError(f"staging abandonado de una ejecución anterior: {stale[0]}")
    if commit.endswith("-dirty"):
        raise RawIntegrityError(f"árbol git dirty ({commit}): commitea antes de normalizar")
    previous = _read_manifest(spec.manifest_path) if spec.manifest_path.exists() else None
    if previous is not None:
        _check_same_origin(previous, spec, inputs, params, code)

    frames, extra = build()
    bad = [n for n in frames if not _NAME_RE.match(n)]
    if bad:
        raise SchemaError(f"nombres de salida no válidos: {bad}")
    clash = sorted(_RESERVED & set(extra))
    if clash:
        raise SchemaError(f"campos extra reservados en el manifiesto: {clash}")
    spec.out_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=spec.out_dir.parent, prefix=f".staging-{spec.version}-"))
    try:
        outputs: dict[str, str] = {}
        for name in sorted(frames):
            path = staging / name
            frames[name].write_parquet(path, compression="zstd", statistics=True)
            os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            outputs[name] = sha256_file(path)
        if previous is None:
            manifest = {
                "source": spec.source,
                "version": spec.version,
                **extra,
                "inputs": dict(sorted(inputs.items())),
                "params": _canonical(dict(params)),
                "code": dict(code),
                "outputs": outputs,
                "code_commit": commit,
                "polars_version": pl.__version__,
            }
            _write_manifest(spec.manifest_path, manifest)  # antes de publicar el directorio
        elif previous["outputs"] != outputs:
            raise RawIntegrityError(
                f"{spec.manifest_path.name}: la reconstrucción no reproduce "
                "los hashes del manifiesto"
            )
        else:
            manifest = previous
        os.chmod(staging, _DIR_MODE)
        os.rename(staging, spec.out_dir)
    finally:
        if staging.exists():  # solo queda si falló antes del rename
            os.chmod(staging, stat.S_IRWXU)
            shutil.rmtree(staging)
    return Published(created=True, outputs=outputs, manifest=manifest)


def _write_manifest(mpath: Path, manifest: dict[str, Any]) -> None:
    """Escritura atómica: temporal en el mismo directorio + rename (no sobrescribe)."""
    mpath.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=mpath.parent, prefix=f".{mpath.name}.")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.link(tmp, mpath)  # falla si ya existe
    finally:
        tmp.unlink()

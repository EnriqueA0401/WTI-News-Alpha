"""Logs estructurados por ejecución (JSON lines).

Cada paso registra inicio/fin (o error), duración, versión de código (commit)
y campos libres (fuente, nº registros, rango temporal, checksum...).
"""

import json
import logging
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wti_news_alpha.errors import WNAError

LOGGER_NAME = "wna"
REPO_ROOT = Path(__file__).resolve().parents[2]


class GitInfoError(WNAError):
    """No se pudo determinar la versión de código."""


def current_commit(repo: Path = REPO_ROOT) -> str:
    """SHA del HEAD; sufijo ``-dirty`` si hay cambios sin commitear."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git del sistema
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise GitInfoError(f"no se pudo leer el commit en {repo}") from exc
    return f"{sha}-dirty" if dirty else sha


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)


def get_logger(log_file: Path | None = None) -> logging.Logger:
    """Logger ``wna`` con salida JSON a stderr y, opcionalmente, a ``log_file``."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(JsonFormatter())
        logger.addHandler(stream)
    if log_file is not None and not any(
        isinstance(h, logging.FileHandler) and Path(h.baseFilename) == log_file.resolve()
        for h in logger.handlers
    ):
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(JsonFormatter())
        logger.addHandler(fh)
    return logger


@contextmanager
def run_step(
    step: str, logger: logging.Logger, commit: str, **fields: Any
) -> Iterator[dict[str, Any]]:
    """Registra inicio/fin de ``step``. El dict devuelto acepta campos de resultado.

    Si el bloque lanza, se registra ``error`` y la excepción se re-lanza.
    """
    base = {"step": step, "commit": commit, **fields}
    result: dict[str, Any] = {}
    logger.info("start", extra={"fields": {**base, "event": "start"}})
    t0 = time.perf_counter()
    try:
        yield result
    except BaseException as exc:
        logger.exception(
            "error",
            extra={
                "fields": {
                    **base,
                    **result,
                    "event": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "duration_s": round(time.perf_counter() - t0, 3),
                }
            },
        )
        raise
    logger.info(
        "end",
        extra={
            "fields": {
                **base,
                **result,
                "event": "end",
                "duration_s": round(time.perf_counter() - t0, 3),
            }
        },
    )

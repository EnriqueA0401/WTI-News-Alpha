"""Guardia del test sellado (CLAUDE.md §3.5, §7, §11.4; ADR-036).

Invariantes:
- Fuera de un acceso abierto, ``assert_research_only`` rechaza cualquier fila con
  ``decision_time`` en la ventana sellada (``SealedTestError``).
- ``open`` exige un flag explícito (``confirm=True``) y un **pre-registro commiteado y sin
  cambios** (``git ls-files`` + ``git diff --quiet HEAD``). Si no, falla sin registrar nada.
- Cada apertura añade una línea JSON a un log *append-only* encadenado por hash
  (``prev_sha256``). Un log vacío, una cadena rota o un log más corto que su versión
  commiteada en git hacen fallar la guardia: el historial no se puede borrar en silencio.
- El segundo acceso y los siguientes quedan marcados ``repeated`` y se deben reportar.
- Revisión F6: el log debe estar dentro del repo; si falta pero existe en ``HEAD``, falla
  (borrarlo no reinicia el contador). Un ``SealedAccess`` solo lo crea ``open``. Tras cada
  acceso hay que **commitear el log**: un log sin commitear podría reescribirse entero.
- ``require_approved_prereg`` (F7): el pre-registro debe estar commiteado, sin cambios y con
  ``status: approved``; devuelve su sha256 para registrarlo con cada corrida.
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import yaml

from wti_news_alpha.errors import SealedTestError
from wti_news_alpha.timeutils import require_utc_column

DECISION = "decision_time"


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    def mask(self, col: str = DECISION) -> pl.Expr:
        return pl.col(col).is_between(self.start, self.end, closed="left")


_TOKEN = object()


@dataclass(frozen=True)
class SealedAccess:
    access_number: int
    repeated: bool
    window: Window
    _token: object = None

    def __post_init__(self) -> None:
        if self._token is not _TOKEN:
            raise SealedTestError("SealedAccess solo se crea con SealedGuard.open")

    def select_sealed(self, df: pl.DataFrame, col: str = DECISION) -> pl.DataFrame:
        """Filas del periodo sellado (solo dentro de un acceso abierto y registrado)."""
        require_utc_column(df, col)
        return df.filter(self.window.mask(col))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos fijos del propio código
        ["git", "-C", str(repo), *args],  # noqa: S607 - git del sistema
        capture_output=True,
        text=True,
        check=False,
    )


class SealedGuard:
    def __init__(self, window: Window, *, repo: Path, prereg: str, log: Path) -> None:
        self.window = window
        self.repo = repo
        self.prereg = prereg
        self.log = log
        if not log.is_relative_to(repo):
            raise SealedTestError(f"el log de accesos {log} debe estar dentro del repo {repo}")

    def assert_research_only(self, df: pl.DataFrame, col: str = DECISION) -> None:
        require_utc_column(df, col)
        n = df.filter(self.window.mask(col)).height
        if n:
            raise SealedTestError(f"{n} filas en el periodo sellado (ADR-036) sin acceso abierto")

    def _prereg_sha(self) -> str:
        if _git(self.repo, "ls-files", "--error-unmatch", self.prereg).returncode != 0:
            raise SealedTestError(f"pre-registro {self.prereg} no commiteado")
        if _git(self.repo, "diff", "--quiet", "HEAD", "--", self.prereg).returncode != 0:
            raise SealedTestError(f"pre-registro {self.prereg} con cambios sin commitear")
        return hashlib.sha256((self.repo / self.prereg).read_bytes()).hexdigest()

    def require_approved_prereg(self) -> str:
        """sha256 del pre-registro si está commiteado, sin cambios y aprobado."""
        sha = self._prereg_sha()
        doc = yaml.safe_load((self.repo / self.prereg).read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("status") != "approved":
            raise SealedTestError(f"el pre-registro {self.prereg} no está aprobado")
        return sha

    def _history(self) -> list[dict[str, object]]:
        rel = self.log.relative_to(self.repo).as_posix()
        if not self.log.exists():
            if _git(self.repo, "cat-file", "-e", f"HEAD:{rel}").returncode == 0:
                raise SealedTestError(f"log de accesos {self.log} borrado (existe en HEAD)")
            return []
        lines = self.log.read_text(encoding="utf-8").splitlines()
        if not lines:
            raise SealedTestError(f"log de accesos {self.log} vacío: historial borrado")
        prev = ""
        out = []
        for raw in lines:
            rec = json.loads(raw)
            if rec.get("prev_sha256") != prev:
                raise SealedTestError(f"log de accesos {self.log}: cadena de hashes rota")
            prev = hashlib.sha256(raw.encode()).hexdigest()
            out.append(rec)
        head = _git(self.repo, "show", f"HEAD:{rel}")
        if head.returncode == 0 and not "\n".join(lines).startswith(head.stdout.strip()):
            raise SealedTestError(f"log de accesos {self.log} más corto que el commiteado")
        return out

    def open(self, *, confirm: bool, commit: str, reason: str) -> SealedAccess:
        """Abre el sellado: valida flag y pre-registro, y registra el acceso."""
        if not confirm:
            raise SealedTestError("abrir el sellado exige el flag explícito confirm=True")
        sha = self._prereg_sha()
        history = self._history()
        n = len(history) + 1
        prev = (
            hashlib.sha256(
                self.log.read_text(encoding="utf-8").splitlines()[-1].encode()
            ).hexdigest()
            if history
            else ""
        )
        rec = {
            "access_number": n,
            "repeated": n > 1,
            "at": datetime.now(tz=UTC).isoformat(),
            "commit": commit,
            "reason": reason,
            "prereg": self.prereg,
            "prereg_sha256": sha,
            "window": [self.window.start.isoformat(), self.window.end.isoformat()],
            "prev_sha256": prev,
        }
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        return SealedAccess(n, n > 1, self.window, _TOKEN)

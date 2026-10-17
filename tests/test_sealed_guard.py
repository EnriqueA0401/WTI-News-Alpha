"""Guardia del test sellado (CLAUDE.md §3.5, §7; ADR-036): sin pre-registro commiteado y flag
explícito no se abre; cada acceso queda en un log append-only; el segundo se reporta."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from wti_news_alpha.errors import SealedTestError
from wti_news_alpha.evaluation import sealed_guard as sg

WIN = sg.Window(datetime(2025, 7, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)  # noqa: S603, S607


def _repo(tmp_path: Path, *, commit_prereg: bool) -> Path:
    repo = tmp_path / "repo"
    (repo / "preregistration").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README").write_text("x")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "init")
    (repo / "preregistration" / "H1.yaml").write_text("hypothesis: H1\n")
    if commit_prereg:
        _git(repo, "add", "preregistration/H1.yaml")
        _git(repo, "commit", "-q", "-m", "prereg")
    return repo


def _guard(repo: Path) -> sg.SealedGuard:
    return sg.SealedGuard(
        WIN, repo=repo, prereg="preregistration/H1.yaml", log=repo / "runs" / "sealed_access.jsonl"
    )


def test_research_frames_must_not_touch_sealed(tmp_path: Path) -> None:
    g = _guard(_repo(tmp_path, commit_prereg=False))
    ok = pl.DataFrame({"decision_time": [datetime(2025, 6, 30, tzinfo=UTC)]})
    g.assert_research_only(ok)
    bad = pl.DataFrame({"decision_time": [datetime(2025, 7, 1, tzinfo=UTC)]})
    with pytest.raises(SealedTestError, match="1 filas"):
        g.assert_research_only(bad)


def test_open_requires_flag_and_committed_prereg(tmp_path: Path) -> None:
    g = _guard(_repo(tmp_path, commit_prereg=False))
    with pytest.raises(SealedTestError, match="flag"):
        g.open(confirm=False, commit="abc", reason="F10")
    with pytest.raises(SealedTestError, match="pre-registro"):
        g.open(confirm=True, commit="abc", reason="F10")
    assert not (tmp_path / "repo" / "runs" / "sealed_access.jsonl").exists()


def test_prereg_modified_after_commit_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path, commit_prereg=True)
    (repo / "preregistration" / "H1.yaml").write_text("hypothesis: H1 changed\n")
    with pytest.raises(SealedTestError, match="cambios sin commitear"):
        _guard(repo).open(confirm=True, commit="abc", reason="F10")


def test_accesses_are_logged_and_second_is_flagged(tmp_path: Path) -> None:
    repo = _repo(tmp_path, commit_prereg=True)
    g = _guard(repo)
    a1 = g.open(confirm=True, commit="abc", reason="F10 apertura")
    assert a1.access_number == 1
    assert not a1.repeated
    a2 = g.open(confirm=True, commit="def", reason="reintento")
    assert a2.access_number == 2
    assert a2.repeated
    lines = [
        json.loads(x) for x in (repo / "runs" / "sealed_access.jsonl").read_text().splitlines()
    ]
    assert [x["access_number"] for x in lines] == [1, 2]
    assert lines[1]["repeated"] is True
    assert lines[0]["prereg_sha256"] == lines[1]["prereg_sha256"]
    # Dentro del acceso, filtrar el sellado ya no falla.
    df = pl.DataFrame({"decision_time": [datetime(2025, 7, 2, tzinfo=UTC)]})
    assert a1.select_sealed(df).height == 1


def test_tampered_log_is_detected(tmp_path: Path) -> None:
    repo = _repo(tmp_path, commit_prereg=True)
    g = _guard(repo)
    g.open(confirm=True, commit="abc", reason="F10")
    log = repo / "runs" / "sealed_access.jsonl"
    log.write_text("")  # borrar el historial
    with pytest.raises(SealedTestError, match="log"):
        g.open(confirm=True, commit="abc", reason="F10")


def test_edited_or_truncated_committed_log_is_detected(tmp_path: Path) -> None:
    repo = _repo(tmp_path, commit_prereg=True)
    g = _guard(repo)
    g.open(confirm=True, commit="abc", reason="F10")
    g.open(confirm=True, commit="abc", reason="F10 bis")
    log = repo / "runs" / "sealed_access.jsonl"
    _git(repo, "add", "runs/sealed_access.jsonl")
    _git(repo, "commit", "-q", "-m", "log")
    first, second = log.read_text().splitlines()
    log.write_text(first + "\n")  # se quita el segundo acceso commiteado
    with pytest.raises(SealedTestError, match="más corto"):
        g.open(confirm=True, commit="abc", reason="F10")
    rec = json.loads(second)
    rec["reason"] = "editado"
    log.write_text(first + "\n" + json.dumps(rec, sort_keys=True) + "\n")
    rec0 = json.loads(first)
    rec0["commit"] = "zzz"
    log.write_text(json.dumps(rec0, sort_keys=True) + "\n" + second + "\n")
    with pytest.raises(SealedTestError, match="cadena"):
        g.open(confirm=True, commit="abc", reason="F10")

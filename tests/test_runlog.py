"""Tests de logs estructurados y versión de código."""

import json
import logging
import subprocess
from pathlib import Path

import pytest

from wti_news_alpha.runlog import GitInfoError, current_commit, get_logger, redact, run_step


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # noqa: S603, S607


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.org")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f.txt").write_text("a", encoding="utf-8")
    _git(tmp_path, "add", "f.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_current_commit_clean_and_dirty(repo: Path) -> None:
    sha = current_commit(repo)
    assert len(sha) == 40
    (repo / "f.txt").write_text("b", encoding="utf-8")
    assert current_commit(repo) == f"{sha}-dirty"


def test_current_commit_outside_repo_fails(tmp_path: Path) -> None:
    with pytest.raises(GitInfoError):
        current_commit(tmp_path)


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def logger_file(tmp_path: Path) -> tuple[logging.Logger, Path]:
    path = tmp_path / "logs" / "run.jsonl"
    logger = get_logger(path)
    yield logger, path
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)


def test_run_step_logs_start_and_end(logger_file: tuple[logging.Logger, Path]) -> None:
    logger, path = logger_file
    with run_step("ingest", logger, "abc", source="gdelt") as res:
        res["n_records"] = 3
    recs = _records(path)
    assert [r["event"] for r in recs] == ["start", "end"]
    assert recs[1]["data"] == {"source": "gdelt", "n_records": 3}
    assert recs[1]["commit"] == "abc"
    assert recs[1]["step"] == "ingest"


def test_run_step_user_fields_cannot_overwrite_reserved(
    logger_file: tuple[logging.Logger, Path],
) -> None:
    # Regresión (revisión adversarial): ts/level/commit se podían pisar.
    logger, path = logger_file
    with run_step("s", logger, "abc", ts="2001-01-01", level="DEBUG") as res:
        res["commit"] = "forged"
    rec = _records(path)[-1]
    assert rec["commit"] == "abc"
    assert rec["level"] == "INFO"
    assert rec["ts"] != "2001-01-01"
    assert rec["data"]["commit"] == "forged"


def test_logs_redact_secrets(
    logger_file: tuple[logging.Logger, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regresión (revisión adversarial): una URL con api_key en un error acababa en el log.
    secret = "SUPERSECRETKEY123"  # noqa: S105 - valor sintético de test
    monkeypatch.setenv("EIA_API_KEY", secret)
    logger, path = logger_file
    with pytest.raises(RuntimeError), run_step("s", logger, "abc"):
        raise RuntimeError(f"401 for url 'https://api.eia.gov/v2/?api_key={secret}&x=1'")
    text = path.read_text(encoding="utf-8")
    assert secret not in text
    assert "api_key=***" in text


def test_redact_pattern_without_env() -> None:
    assert redact("https://x/?token=abc&k=1") == "https://x/?token=***&k=1"


def test_run_step_logs_error_and_reraises(logger_file: tuple[logging.Logger, Path]) -> None:
    logger, path = logger_file
    with pytest.raises(ValueError, match="x"), run_step("s", logger, "abc"):
        raise ValueError("x")
    recs = _records(path)
    assert recs[-1]["event"] == "error"
    assert recs[-1]["error"] == "ValueError: x"
    assert "Traceback" in recs[-1]["traceback"]


def test_get_logger_does_not_duplicate_file_handler(
    logger_file: tuple[logging.Logger, Path],
) -> None:
    logger, path = logger_file
    n = len(logger.handlers)
    assert get_logger(path) is logger
    assert get_logger() is logger
    assert len(logger.handlers) == n

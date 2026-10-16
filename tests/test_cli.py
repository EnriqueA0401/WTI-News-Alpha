"""Tests de la CLI (sin red)."""

import logging
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wti_news_alpha import cli
from wti_news_alpha.config import SourceSpec
from wti_news_alpha.errors import PhaseNotImplementedError
from wti_news_alpha.ingestion import capture, gdelt_gkg
from wti_news_alpha.ingestion.source_check import AccessStatus, ProbeResult
from wti_news_alpha.normalization import darwinex

runner = CliRunner()


@pytest.mark.parametrize(
    "command", ["build-features", "build-labels", "train", "evaluate", "report"]
)
def test_future_phase_commands_fail_loudly(command: str) -> None:
    result = runner.invoke(cli.app, [command])
    assert result.exit_code != 0
    assert isinstance(result.exception, PhaseNotImplementedError)


def _fake_probe_all(statuses: list[AccessStatus]):  # type: ignore[no-untyped-def]
    def fake(sources: list[SourceSpec], env: object) -> list[ProbeResult]:
        return [
            ProbeResult(f"s{i}", "https://x", st, 200, False, "") for i, st in enumerate(statuses)
        ]

    return fake


def test_sources_check_writes_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "probe_all", _fake_probe_all([AccessStatus.OK]))
    out = tmp_path / "r.md"
    result = runner.invoke(cli.app, ["sources", "check", "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert "| s0 | ok |" in out.read_text(encoding="utf-8")


@pytest.mark.parametrize(("strict", "code"), [(True, 1), (False, 0)])
def test_sources_check_strict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, strict: bool, code: int
) -> None:
    monkeypatch.setattr(cli, "probe_all", _fake_probe_all([AccessStatus.OK, AccessStatus.BLOCKED]))
    args = ["sources", "check", "--output", str(tmp_path / "r.md")]
    result = runner.invoke(cli.app, [*args, "--strict"] if strict else args)
    assert result.exit_code == code


def test_ingest_gdelt_gkg_reports_and_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[date, date]] = []

    def fake(start: date, end: date, *_: object, **__: object) -> gdelt_gkg.RunSummary:
        calls.append((start, end))
        return gdelt_gkg.RunSummary(written=[start], failed={end: "md5"}, stopped_reason="disco")

    monkeypatch.setattr(cli.gdelt_gkg, "run_pipeline", fake)
    monkeypatch.setattr(cli, "get_logger", lambda *_: logging.getLogger("t"))
    args = ["ingest", "gdelt-gkg", "--start", "2024-01-01", "--end", "2024-01-03"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 1
    assert calls == [(date(2024, 1, 1), date(2024, 1, 3))]
    assert "FALLO 2024-01-03: md5" in result.output
    assert "PARADA: disco" in result.output


def test_ingest_gdelt_gkg_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.gdelt_gkg, "run_pipeline", lambda *_, **__: gdelt_gkg.RunSummary())
    monkeypatch.setattr(cli, "get_logger", lambda *_: logging.getLogger("t"))
    args = ["ingest", "gdelt-gkg", "--start", "2024-01-01", "--end", "2024-01-02"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(("errors", "code"), [([], 0), (["gdelt: boom"], 1)])
def test_capture_run_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, errors: list[str], code: int
) -> None:
    def fake(*_: object, **__: object) -> capture.CaptureResult:
        return capture.CaptureResult(n_new=2, errors=errors)

    monkeypatch.setattr(cli.capture, "run_capture", fake)
    result = runner.invoke(
        cli.app, ["capture", "run", "--data-dir", str(tmp_path), "--commit", "abc"]
    )
    assert result.exit_code == code
    assert "nuevas observaciones: 2" in result.output


@pytest.mark.parametrize(("created", "word"), [(True, "creada"), (False, "verificada")])
def test_normalize_darwinex(monkeypatch: pytest.MonkeyPatch, created: bool, word: str) -> None:
    stats = darwinex.DropStats(rows_in=10, rows_out=9, dropped={"daily_break": 1})

    def fake(**_: object) -> darwinex.NormalizeResult:
        return darwinex.NormalizeResult(created=created, outputs={"2018.parquet": "x"}, stats=stats)

    monkeypatch.setattr(cli.darwinex, "run_normalize", fake)
    monkeypatch.setattr(cli, "current_commit", lambda: "abc")
    monkeypatch.setattr(cli, "get_logger", lambda *_: logging.getLogger("t"))
    result = runner.invoke(cli.app, ["normalize", "darwinex"])
    assert result.exit_code == 0, result.output
    assert word in result.output
    assert "9/10" in result.output

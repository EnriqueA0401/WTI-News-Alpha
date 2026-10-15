"""CLI ``wna``. Los comandos de fases futuras fallan de forma explícita."""

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from wti_news_alpha.config import CONFIG_DIR, load_project_config
from wti_news_alpha.errors import PhaseNotImplementedError
from wti_news_alpha.ingestion.source_check import AccessStatus, probe_all, render_markdown
from wti_news_alpha.runlog import REPO_ROOT, current_commit, get_logger, run_step

app = typer.Typer(no_args_is_help=True, help="WTI News Alpha", pretty_exceptions_show_locals=False)
sources_app = typer.Typer(no_args_is_help=True, help="Fuentes de datos")
app.add_typer(sources_app, name="sources")


@sources_app.command("check")
def sources_check(
    output: Annotated[Path, typer.Option(help="Informe Markdown")] = REPO_ROOT
    / "reports"
    / "source_access.md",
    strict: Annotated[
        bool, typer.Option(help="Salir con código 1 si alguna fuente no es OK")
    ] = False,
) -> None:
    """Sondea cada fuente de config/sources.yaml y escribe un informe."""
    cfg = load_project_config(CONFIG_DIR)
    logger = get_logger()
    with run_step("sources_check", logger, current_commit()) as res:
        results = probe_all(cfg.sources.sources, os.environ)
        res["n_sources"] = len(results)
        res["n_ok"] = sum(r.status is AccessStatus.OK for r in results)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(results, datetime.now(tz=UTC)), encoding="utf-8")
    for r in results:
        typer.echo(f"{r.status.value:14} {r.name}")
    typer.echo(f"Informe: {output}")
    if strict and any(r.status is not AccessStatus.OK for r in results):
        raise typer.Exit(code=1)


def _not_yet(command: str, phase: str) -> None:
    raise PhaseNotImplementedError(f"`{command}` se implementa en {phase} (ver CLAUDE.md §12)")


@app.command()
def ingest() -> None:
    """Ingesta de fuentes (F2)."""
    _not_yet("ingest", "F2")


@app.command("build-features")
def build_features() -> None:
    """Features point-in-time (F4)."""
    _not_yet("build-features", "F4")


@app.command("build-labels")
def build_labels() -> None:
    """Etiquetas versionadas (F6)."""
    _not_yet("build-labels", "F6")


@app.command()
def train() -> None:
    """Modelos M0-M4 (F7)."""
    _not_yet("train", "F7")


@app.command()
def evaluate() -> None:
    """Evaluación y robustez (F8)."""
    _not_yet("evaluate", "F8")


@app.command()
def report() -> None:
    """Informes (F8-F10)."""
    _not_yet("report", "F8")

"""CLI ``wna``. Los comandos de fases futuras fallan de forma explícita."""

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer

from wti_news_alpha.config import (
    CONFIG_DIR,
    load_capture_config,
    load_darwinex_config,
    load_gdelt_config,
    load_project_config,
)
from wti_news_alpha.errors import PhaseNotImplementedError
from wti_news_alpha.ingestion import capture, gdelt_gkg
from wti_news_alpha.ingestion.source_check import AccessStatus, probe_all, render_markdown
from wti_news_alpha.normalization import darwinex
from wti_news_alpha.runlog import REPO_ROOT, current_commit, get_logger, run_step

app = typer.Typer(no_args_is_help=True, help="WTI News Alpha", pretty_exceptions_show_locals=False)
sources_app = typer.Typer(no_args_is_help=True, help="Fuentes de datos")
app.add_typer(sources_app, name="sources")
ingest_app = typer.Typer(no_args_is_help=True, help="Ingesta de fuentes (F2)")
app.add_typer(ingest_app, name="ingest")
GDELT_INDEX_REL = "gdelt/index/masterfilelist_20261015.txt"
normalize_app = typer.Typer(no_args_is_help=True, help="Capa normalizada (F2)")
app.add_typer(normalize_app, name="normalize")
capture_app = typer.Typer(no_args_is_help=True, help="Captura prospectiva (D12)")
app.add_typer(capture_app, name="capture")


@capture_app.command("run")
def capture_run(
    data_dir: Annotated[Path, typer.Option(help="Carpeta de observaciones (rama capture-data)")],
    run_id: Annotated[str, typer.Option(help="Identificador de la ejecución")] = "local",
    commit: Annotated[str | None, typer.Option(help="Versión de código (por defecto, git)")] = None,
) -> None:
    """Registra la primera aparición de datos GDELT, WPSR y COT (código 1 si falla una fuente)."""
    cfg = load_capture_config(CONFIG_DIR)
    version = commit or current_commit()
    logger = get_logger()
    with (
        run_step("capture", logger, version, run_id=run_id) as res,
        httpx.Client(timeout=cfg.http_timeout_s, headers={"User-Agent": cfg.user_agent}) as client,
    ):
        result = capture.run_capture(
            data_dir,
            client,
            cfg,
            now=lambda: datetime.now(tz=UTC),
            commit=version,
            run_id=run_id,
        )
        res.update(n_new=result.n_new, errors=result.errors)
    typer.echo(f"nuevas observaciones: {result.n_new}")
    for err in result.errors:
        typer.echo(f"ERROR {err}")
    if result.errors:
        raise typer.Exit(code=1)


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


@normalize_app.command("darwinex")
def normalize_darwinex() -> None:
    """Precio Darwinex XTIUSD M1 → UTC (docs/plans/FASE_02_precio.md). Idempotente."""
    cfg = load_darwinex_config(CONFIG_DIR)
    commit = current_commit()
    logger = get_logger()
    with run_step("normalize_darwinex", logger, commit, version=cfg.version) as res:
        result = darwinex.run_normalize(
            raw_root=REPO_ROOT / "data" / "raw",
            out_root=REPO_ROOT / "data" / "normalized",
            manifest_dir=REPO_ROOT / "manifests",
            cfg=cfg,
            commit=commit,
        )
        res.update(
            created=result.created,
            rows_in=result.stats.rows_in,
            rows_out=result.stats.rows_out,
            dropped=result.stats.dropped,
        )
    action = "creada" if result.created else "verificada (ya existía)"
    typer.echo(
        f"{cfg.version} {action}: {result.stats.rows_out}/{result.stats.rows_in} barras · "
        f"descartadas {result.stats.dropped} · {len(result.outputs)} ficheros"
    )


@ingest_app.command("gdelt-gkg")
def ingest_gdelt_gkg(
    start: Annotated[datetime, typer.Option(formats=["%Y-%m-%d"], help="Día UTC inicial")],
    end: Annotated[datetime, typer.Option(formats=["%Y-%m-%d"], help="Día UTC final (excluido)")],
    index_rel: Annotated[
        str, typer.Option(help="Índice maestro (ruta relativa a data/raw)")
    ] = GDELT_INDEX_REL,
    workers: Annotated[int | None, typer.Option(help="Procesos (por defecto, config)")] = None,
) -> None:
    """GKG de GDELT filtrado en streaming (ADR-011). Idempotente: retoma días pendientes."""
    cfg = load_gdelt_config(CONFIG_DIR)
    commit = current_commit()
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    logger = get_logger(REPO_ROOT / "runs" / "logs" / f"ingest_gdelt_gkg_{stamp}.jsonl")
    with run_step(
        "ingest_gdelt_gkg", logger, commit, start=str(start.date()), end=str(end.date())
    ) as res:
        summary = gdelt_gkg.run_pipeline(
            start.date(),
            end.date(),
            cfg,
            raw_root=REPO_ROOT / "data" / "raw",
            manifest_dir=REPO_ROOT / "manifests",
            index_rel=index_rel,
            commit=commit,
            logger=logger,
            workers=workers,
        )
        res.update(
            already_done=len(summary.already_done),
            written=len(summary.written),
            failed=len(summary.failed),
            not_run=len(summary.not_run),
            stopped_reason=summary.stopped_reason,
        )
    typer.echo(
        f"ya hechos {len(summary.already_done)} · escritos {len(summary.written)} · "
        f"fallidos {len(summary.failed)} · sin ejecutar {len(summary.not_run)}"
    )
    for day, err in summary.failed.items():
        typer.echo(f"FALLO {day}: {err}")
    if summary.stopped_reason:
        typer.echo(f"PARADA: {summary.stopped_reason}")
    if not summary.ok:
        raise typer.Exit(code=1)


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

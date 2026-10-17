"""Publicación de la capa de etiquetas ``label-v1`` (FASE_06.md).

Invariantes:
- Entradas verificadas por sha256 contra su manifiesto: barras de precio y la capa de
  features, de la que salen los ``decision_time``.
- Solo el periodo de investigación: ``decision_time`` < inicio del sellado, y las etiquetas
  cuyo intervalo termina en él se anulan (``labels.build_labels``, ADR-036).
- Horizontes de ``config/horizons.yaml``, latencia de ``config/costs.yaml`` y fin de sesión
  a las 17:00 ET (ADR-037). Salida: ``labels_YYYYMM.parquet`` por mes de ``decision_time``.
"""

import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import polars as pl

from wti_news_alpha.config import LabelsConfig, ProjectConfig
from wti_news_alpha.eda import event_study
from wti_news_alpha.errors import SchemaError
from wti_news_alpha.features import market, pit
from wti_news_alpha.features.build import verified_inputs
from wti_news_alpha.labels import labels
from wti_news_alpha.normalization.store import (
    LayerSpec,
    Published,
    Writer,
    code_hashes,
    publish_or_verify_streaming,
)

SOURCE = "labels"


def label_params(cfg: LabelsConfig, proj: ProjectConfig) -> labels.LabelParams:
    hz = proj.horizons.horizons.require("horizons")
    if proj.horizons.session_end_reference.require("session_end_reference") != "globex_close":
        raise SchemaError("label-v1 solo implementa el fin de sesión 17:00 ET (ADR-037)")
    return labels.LabelParams(
        latency=timedelta(seconds=proj.costs.latency_seconds.require("latency_seconds")),
        max_gap=timedelta(minutes=cfg.max_gap_minutes.require("max_gap_minutes")),
        horizons_min=[h.length for h in hz if h.kind == "market_minutes" and h.length],
        sessions=[h.length for h in hz if h.kind == "sessions" and h.length],
        sealed_start=proj.splits.sealed_test_window.require("sealed_test_window").start,
    )


def _writer(
    bar_files: list[Path], feat_files: list[Path], p: labels.LabelParams, version: str
) -> Writer:
    def write(staging: Path) -> dict[str, Any]:
        bars = pl.concat([pl.read_parquet(f, columns=list(market.REQUIRED)) for f in bar_files])
        bars = bars.filter(pl.col(pit.AVAILABLE) < p.sealed_start)
        grid = pl.concat([pl.read_parquet(f, columns=[pit.DECISION]) for f in feat_files])
        grid = grid.filter(pl.col(pit.DECISION) < p.sealed_start).sort(pit.DECISION)
        out = labels.build_labels(grid, bars, p).with_columns(label_version=pl.lit(version))
        months = out.with_columns(_m=pl.col(pit.DECISION).dt.strftime("%Y%m"))
        n = 0
        for (m,), part in months.partition_by("_m", as_dict=True, maintain_order=True).items():
            part.drop("_m").write_parquet(
                staging / f"labels_{m}.parquet", compression="zstd", statistics=True
            )
            n += part.height
        if n != out.height:
            raise SchemaError(f"filas escritas {n} ≠ calculadas {out.height}")
        rets = [c for c in out.columns if c.startswith("ret_")]
        return {
            "n_rows": out.height,
            "first_decision": str(out[pit.DECISION].min()),
            "last_decision": str(out[pit.DECISION].max()),
            "sealed_start": str(p.sealed_start),
            "non_null_share": {c: round(1 - out[c].null_count() / out.height, 4) for c in rets},
        }

    return write


def run_build(
    *, out_root: Path, manifest_dir: Path, cfg: LabelsConfig, proj: ProjectConfig, commit: str
) -> Published:
    """Verifica las entradas y publica (o verifica) la capa de etiquetas."""
    code = code_hashes(sys.modules[__name__], labels, event_study, market, pit)
    inputs: dict[str, str] = {}
    price = verified_inputs(manifest_dir, out_root, "darwinex_xti_m1", cfg.inputs.price, inputs)
    feats = verified_inputs(manifest_dir, out_root.parent, "features", cfg.inputs.features, inputs)
    p = label_params(cfg, proj)
    params = {
        "labels": cfg.model_dump(mode="json", by_alias=True, exclude={"version"}),
        "horizons": proj.horizons.model_dump(mode="json", by_alias=True),
        "latency_s": p.latency.total_seconds(),
        "sealed_start": str(p.sealed_start),
    }
    spec = LayerSpec(out_root.parent, manifest_dir, SOURCE, cfg.version)  # data/labels/<v>
    writer = _writer(sorted(price.values()), sorted(feats.values()), p, cfg.version)
    return publish_or_verify_streaming(spec, inputs, params, writer, commit=commit, code=code)

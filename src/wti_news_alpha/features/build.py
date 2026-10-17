"""Ensamblado y publicación de la capa de features (FASE_04.md).

Invariantes:
- Entradas verificadas por sha256 contra su manifiesto (``verified_inputs``).
- Una sola pasada sobre todo el periodo: la rejilla sale de las barras de precio y cada
  grupo se une con ``asof`` backward (``pit``). Antes de escribir, ``check_no_leakage``
  sobre todas las filas: si algo falla, no se publica nada.
- Salida: ``features_YYYYMM.parquet`` por mes de ``decision_time`` (UTC), con
  ``data_cutoff_time`` = ``decision_time`` y ``feature_version``.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import polars as pl

from wti_news_alpha.config import FeaturesConfig
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.features import calendar, fundamentals, market, news, pit
from wti_news_alpha.normalization.store import (
    LayerSpec,
    Published,
    Writer,
    code_hashes,
    publish_or_verify_streaming,
    sha256_file,
)

SOURCE = "features"
LAYERS = {
    "price": "darwinex_xti_m1",
    "events": "events",
    "wpsr": "eia_wpsr",
    "cot": "cftc_cot",
    "fred": "fred_alfred",
}
NEWS_COLUMNS = ["first_available_at", "family", "eligible", "is_event_first", "title_source"]


def verified_inputs(
    manifest_dir: Path, out_root: Path, source: str, version: str, shas: dict[str, str]
) -> dict[str, Path]:
    """Ficheros de una capa normalizada, comprobados contra el sha256 de su manifiesto.

    Añade a ``shas`` la ruta relativa → sha256 de cada fichero (entradas del manifiesto).
    """
    mpath = manifest_dir / "normalized" / source / f"{version}.json"
    if not mpath.is_file():
        raise RawIntegrityError(f"falta el manifiesto {mpath}")
    man = json.loads(mpath.read_text(encoding="utf-8"))
    base = out_root / source / version
    files: dict[str, Path] = {}
    for name, sha in sorted(man["outputs"].items()):
        path = base / name
        if not path.is_file() or sha256_file(path) != sha:
            raise RawIntegrityError(f"integridad rota en {path}")
        files[f"{source}/{version}/{name}"] = path
        shas[f"{source}/{version}/{name}"] = sha
    return files


def _event_families(out_root: Path, events_version: str) -> list[str]:
    """Familias que generan eventos en la capa de eventos (las que tienen elegibles)."""
    fams = (
        pl.scan_parquet(out_root / LAYERS["events"] / events_version / "articles_*.parquet")
        .filter(pl.col("eligible"))
        .select(pl.col("family").unique())
        .collect()["family"]
    )
    return sorted(fams.to_list())


def assemble(
    files: dict[str, dict[str, Path]], cfg: FeaturesConfig, families: list[str]
) -> pl.DataFrame:
    """Todas las features para todo el periodo, con la comprobación de fugas superada."""
    step = timedelta(minutes=cfg.step_minutes.require("step_minutes"))
    age = timedelta(minutes=cfg.max_bar_age_minutes.require("max_bar_age_minutes"))
    mp = market.MarketParams(
        return_minutes=cfg.return_minutes.require("return_minutes"),
        max_gap=timedelta(minutes=cfg.max_gap_minutes.require("max_gap_minutes")),
        max_bar_age=age,
        vol_window=timedelta(minutes=cfg.vol_window_minutes.require("vol_window_minutes")),
    )
    bar_cols = [*market.REQUIRED]
    bars = pl.concat([pl.read_parquet(p, columns=bar_cols) for p in files["price"].values()])
    grid = pit.decision_grid(bars, step=step, max_bar_age=age)
    # Solo los periodos aprobados en G0 (ADR-019); las barras previas sirven de historia.
    in_period = pl.any_horizontal(
        pl.col(pit.DECISION).is_between(pr.start, pr.end, closed="left")
        for pr in cfg.periods.require("periods")
    )
    grid = grid.filter(in_period)
    if grid.height == 0:
        raise SchemaError("rejilla vacía en los periodos configurados")
    out = market.market_features(grid, bars, mp)
    del bars
    wpsr = pl.read_parquet(_one(files["wpsr"], "values.parquet"))
    cot = pl.read_parquet(_one(files["cot"], "cot.parquet"))
    fred = pl.read_parquet(_one(files["fred"], "vintages.parquet"))
    out = fundamentals.wpsr_features(out, wpsr)
    out = fundamentals.cot_features(out, cot)
    out = fundamentals.fred_features(out, fred)
    arts_paths = sorted(
        p for n, p in files["events"].items() if Path(n).name.startswith("articles_")
    )
    arts = pl.concat(
        [pl.read_parquet(p, columns=NEWS_COLUMNS).filter(pl.col("eligible")) for p in arts_paths]
    )
    np_ = news.NewsParams(
        families=families,
        windows=cfg.news_windows_minutes.require("news_windows_minutes"),
        slot_lag=timedelta(minutes=cfg.news_slot_lag_minutes.require("news_slot_lag_minutes")),
        slot_deadline=timedelta(
            minutes=cfg.news_slot_deadline_minutes.require("news_slot_deadline_minutes")
        ),
        min_slot_articles=cfg.news_min_slot_articles.require("news_min_slot_articles"),
    )
    out = news.news_features(out, arts, gdelt_slots(arts_paths), np_)
    out = calendar.calendar_features(out)
    if out.height != grid.height:
        raise SchemaError(f"features: {grid.height} instantes → {out.height} filas")
    pit.check_no_leakage(out)
    return out.with_columns(
        data_cutoff_time=pl.col(pit.DECISION), feature_version=pl.lit(cfg.version)
    )


def gdelt_slots(article_paths: list[Path]) -> pl.DataFrame:
    """Ficheros GDELT de 15 min presentes en la capa de eventos: nominal, disponibilidad y
    número de artículos (todas las relevancias). Todos los artículos de un fichero comparten
    ``first_available_at``; si no, ``SchemaError``."""
    per = (
        pl.scan_parquet(article_paths)
        .group_by(news.NOMINAL)
        .agg(
            n=pl.len(),
            available_at=pl.col("first_available_at").min(),
            _max=pl.col("first_available_at").max(),
        )
        .collect()
        .sort(news.NOMINAL)
    )
    if per.filter(pl.col("available_at") != pl.col("_max")).height:
        raise SchemaError("un fichero GDELT con varias horas de disponibilidad")
    return per.drop("_max")


def _one(files: dict[str, Path], name: str) -> Path:
    hits = [p for n, p in files.items() if Path(n).name == name]
    if len(hits) != 1:
        raise RawIntegrityError(f"se esperaba un único {name}, hay {len(hits)}")
    return hits[0]


def _writer(files: dict[str, dict[str, Path]], cfg: FeaturesConfig, families: list[str]) -> Writer:
    def write(staging: Path) -> dict[str, Any]:
        out = assemble(files, cfg, families)
        months = out.with_columns(_m=pl.col(pit.DECISION).dt.strftime("%Y%m"))
        n = 0
        for (m,), part in months.partition_by("_m", as_dict=True, maintain_order=True).items():
            part.drop("_m").write_parquet(
                staging / f"features_{m}.parquet", compression="zstd", statistics=True
            )
            n += part.height
        if n != out.height:
            raise SchemaError(f"filas escritas {n} ≠ calculadas {out.height}")
        nulls = out.select(pl.all().null_count()).row(0, named=True)
        return {
            "n_rows": out.height,
            "n_columns": len(out.columns),
            "first_decision": str(out[pit.DECISION].min()),
            "last_decision": str(out[pit.DECISION].max()),
            "event_families": families,
            "null_share": {k: round(v / out.height, 4) for k, v in sorted(nulls.items()) if v},
        }

    return write


def run_build(*, out_root: Path, manifest_dir: Path, cfg: FeaturesConfig, commit: str) -> Published:
    """Verifica las capas de entrada y publica (o verifica) la capa de features."""
    code = code_hashes(sys.modules[__name__], pit, market, fundamentals, news, calendar)
    versions = cfg.inputs.model_dump()
    inputs: dict[str, str] = {}
    files = {
        k: verified_inputs(manifest_dir, out_root, LAYERS[k], versions[k], inputs) for k in LAYERS
    }
    families = _event_families(out_root, cfg.inputs.events)
    spec = LayerSpec(out_root.parent, manifest_dir, SOURCE, cfg.version)  # data/features/<v>
    params = cfg.model_dump(mode="json", by_alias=True, exclude={"version"})
    return publish_or_verify_streaming(
        spec, inputs, params, _writer(files, cfg, families), commit=commit, code=code
    )

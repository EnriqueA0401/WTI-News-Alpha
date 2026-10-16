"""Capa de eventos: reglas → deduplicación online → agrupación online (FASE_03.md).

Invariantes:
- Entrada: la capa ``gdelt_news/<news_version>``, verificada por sha256 contra su
  manifiesto.
- Las anotaciones por artículo (relevancia, familia, mecanismo, duplicado,
  evento y métricas acumuladas) solo dependen de artículos anteriores
  (``dedup``/``cluster`` tienen tests de truncamiento).
- ``events.parquet`` resume cada evento con totales **retrospectivos**
  (``*_final``). No son point-in-time: las features deben usar las columnas
  acumuladas por artículo (``event_seq``, ``event_sources_so_far``).
- Escritura en streaming: un fichero de artículos por mes y uno de eventos.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import polars as pl

from wti_news_alpha.config import EventRulesConfig, EventsConfig
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.events import cluster, dedup, rules
from wti_news_alpha.normalization.store import (
    LayerSpec,
    Published,
    Writer,
    code_hashes,
    publish_or_verify_streaming,
    sha256_file,
)

SOURCE = "events"
NEWS_SOURCE = "gdelt_news"
_RULE_INPUT = ["title", "url", "themes", "organizations"]
ANNOTATIONS = (
    "article_id",
    "relevance",
    "relevance_rule",
    "family",
    "family_rule",
    "mechanism",
    "mechanism_rule",
    "country",
    "dup_kind",
    "dup_of",
    "eligible",
    "event_id",
    "event_seq",
    "event_sources_so_far",
    "is_event_first",
    "mechanism_differs",
)


def article_id() -> pl.Expr:
    """Id estable de artículo: ``YYYYmmddHHMMSS-<fila>`` del fichero GKG."""
    return pl.concat_str(
        [pl.col("source_file_ts").dt.strftime("%Y%m%d%H%M%S"), pl.col("row_in_file").cast(pl.Utf8)],
        separator="-",
    )


def compact_month(df: pl.DataFrame, rcfg: EventRulesConfig) -> pl.DataFrame:
    """Un mes normalizado → filas compactas con reglas y claves de deduplicación."""
    out = rules.apply_rules(df, rcfg).with_columns(
        article_id=article_id(),
        country=cluster.primary_country(pl.col("locations")),
    )
    out = dedup.dedup_keys(out.drop("themes", "organizations", "locations"))
    return out.with_columns(source_key=pl.col("_src_key"))


def annotate(compact: pl.DataFrame, cfg: EventsConfig) -> pl.DataFrame:
    """Deduplica, decide la elegibilidad y agrupa en eventos todo el periodo."""
    window = timedelta(hours=cfg.title_dedup_window_hours.require("title_dedup_window_hours"))
    gap = timedelta(hours=cfg.event_gap_hours.require("event_gap_hours"))
    eligible_rel = cfg.eligible_relevance.require("eligible_relevance")
    d = dedup.mark_duplicates(compact, window).with_columns(
        eligible=pl.col("relevance").is_in(eligible_rel)
        & pl.col("dup_kind").is_null()
        & pl.col("exclude_reason").is_null()
    )
    d = cluster.inherit_events(cluster.assign_events(d, gap))
    return d.select(ANNOTATIONS)


def events_table(ann: pl.DataFrame, fa: pl.DataFrame) -> pl.DataFrame:
    """Resumen retrospectivo por evento (``*_final``: no point-in-time)."""
    first = ann.filter(pl.col("is_event_first").fill_null(False)).join(
        fa, on="article_id", how="left", validate="1:1"
    )
    members = ann.filter(pl.col("event_id").is_not_null()).join(
        fa, on="article_id", how="left", validate="1:1"
    )
    agg = members.group_by("event_id").agg(
        n_articles_final=pl.len(),
        n_unique_articles_final=pl.col("dup_of").is_null().sum(),
        n_sources_final=pl.col("event_sources_so_far").max(),
        last_article_at_final=pl.col("first_available_at").max(),
    )
    return (
        first.select(
            "event_id",
            "family",
            "mechanism",
            "country",
            pl.col("first_available_at").alias("event_first_available_at"),
        )
        .join(agg, on="event_id", how="left", validate="1:1")
        .sort("event_first_available_at", "event_id")
    )


def _news_inputs(manifest_dir: Path, out_root: Path, version: str) -> dict[str, str]:
    mpath = manifest_dir / "normalized" / NEWS_SOURCE / f"{version}.json"
    if not mpath.is_file():
        raise RawIntegrityError(f"falta el manifiesto de noticias {mpath}")
    man = json.loads(mpath.read_text(encoding="utf-8"))
    base = out_root / NEWS_SOURCE / version
    inputs: dict[str, str] = {}
    for name, sha in sorted(man["outputs"].items()):
        path = base / name
        if not path.is_file() or sha256_file(path) != sha:
            raise RawIntegrityError(f"integridad rota en {path}")
        inputs[f"{NEWS_SOURCE}/{version}/{name}"] = sha
    return inputs


def _writer(news_dir: Path, months: list[str], cfg: EventsConfig, rcfg: EventRulesConfig) -> Writer:
    def write(staging: Path) -> dict[str, Any]:
        cols = [
            "source_file_ts",
            "row_in_file",
            "first_available_at",
            "exclude_reason",
            "source_common_name",
            *_RULE_INPUT,
            "locations",
        ]
        compact = pl.concat(
            [compact_month(pl.read_parquet(news_dir / m, columns=cols), rcfg) for m in months]
        )
        ann = annotate(compact, cfg)
        fa = compact.select("article_id", "first_available_at")
        del compact
        events = events_table(ann, fa)
        events.write_parquet(staging / "events.parquet", compression="zstd", statistics=True)
        keep = [
            "source_file_ts",
            "row_in_file",
            "first_available_at",
            "exclude_reason",
            "source_common_name",
            "url",
            "title",
            "tone",
        ]
        n_written = 0
        for m in months:
            month = pl.read_parquet(news_dir / m, columns=keep).with_columns(
                article_id=article_id()
            )
            out = month.join(ann, on="article_id", how="inner", validate="1:1")
            if out.height != month.height:
                raise SchemaError(f"{m}: anotaciones incompletas")
            out = out.sort("first_available_at", "source_file_ts", "row_in_file")
            out.write_parquet(staging / f"articles_{m}", compression="zstd", statistics=True)
            n_written += out.height
        if n_written != ann.height:
            raise SchemaError(f"artículos escritos {n_written} ≠ anotados {ann.height}")
        count = ann.group_by("relevance").len().sort("relevance")
        return {
            "n_articles": ann.height,
            "n_events": events.height,
            "relevance_counts": dict(count.iter_rows()),
            "dup_counts": dict(
                ann.group_by("dup_kind").len().fill_null("none").sort("dup_kind").iter_rows()
            ),
            "n_eligible": int(ann["eligible"].sum()),
            "family_counts_eligible": dict(
                ann.filter("eligible").group_by("family").len().sort("family").iter_rows()
            ),
        }

    return write


def run_build(
    *,
    out_root: Path,
    manifest_dir: Path,
    cfg: EventsConfig,
    rcfg: EventRulesConfig,
    commit: str,
) -> Published:
    """Verifica la capa de noticias y publica (o verifica) la capa de eventos."""
    code = code_hashes(sys.modules[__name__], dedup, cluster, rules)
    rules.check_regexes(rcfg)
    inputs = _news_inputs(manifest_dir, out_root, cfg.news_version)
    months = sorted(Path(k).name for k in inputs)
    spec = LayerSpec(out_root, manifest_dir, SOURCE, cfg.version)
    params = {
        "events": cfg.model_dump(mode="json", by_alias=True, exclude={"version"}),
        "rules": rcfg.model_dump(mode="json"),
    }
    news_dir = out_root / NEWS_SOURCE / cfg.news_version
    return publish_or_verify_streaming(
        spec, inputs, params, _writer(news_dir, months, cfg, rcfg), commit=commit, code=code
    )

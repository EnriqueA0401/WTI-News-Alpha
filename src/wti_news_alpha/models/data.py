"""Cargador único de datos de modelado (revisión F6): ``feat-v1`` + ``label-v1``, solo periodo
de investigación, pasado por la guardia del sellado.

- Las features permitidas son exactamente las columnas de la capa de features (menos
  ``decision_time``, ``data_cutoff_time`` y ``feature_version``); las de etiqueta nunca.
- ``SealedGuard.assert_research_only`` falla si alguna fila está en el sellado (ADR-036).
"""

from dataclasses import dataclass
from pathlib import Path

import polars as pl

from wti_news_alpha.evaluation import sealed_guard as sg
from wti_news_alpha.features import pit

NON_FEATURES = frozenset({pit.DECISION, "data_cutoff_time", "feature_version"})
LABEL_COLUMNS = ("ret_15m", "ret_60m", "label_end_15m", "label_end_60m")


@dataclass(frozen=True)
class ResearchFrame:
    df: pl.DataFrame
    allowed: frozenset[str]


def load_research_frame(
    *, features_dir: Path, labels_dir: Path, guard: sg.SealedGuard
) -> ResearchFrame:
    feats = pl.scan_parquet(features_dir / "*.parquet")
    allowed = frozenset(feats.collect_schema().names()) - NON_FEATURES
    feats = feats.filter(pl.col(pit.DECISION) < guard.window.start)
    labs = pl.scan_parquet(labels_dir / "*.parquet").select(pit.DECISION, *LABEL_COLUMNS)
    df = feats.join(labs, on=pit.DECISION, how="inner").collect().sort(pit.DECISION)
    guard.assert_research_only(df)
    return ResearchFrame(df, allowed)

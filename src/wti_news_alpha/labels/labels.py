"""Etiquetas por ``decision_time`` (FASE_06.md; ADR-037/040; Documento §8).

Invariantes:
- Entrada: cierre bid de la primera barra completa ≥ t + latencia; salida: última barra ≤
  entrada + h (tiempo de mercado). Nula, sin rellenar, si cruza un hueco > ``max_gap`` o la
  pausa (misma lógica probada que ``eda.event_study.event_returns``).
- ``label_end_<h>``: final del intervalo de la etiqueta (cota superior de la salida), para
  la purga del splitter. Si no hay entrada: ``t + latencia + max_gap + h``.
- **Sellado (ADR-036):** ningún ``decision_time`` puede estar en el sellado (``SealedTestError``)
  y toda etiqueta cuyo intervalo termina en él se anula. Las del sellado se calcularán en F10,
  detrás de la guardia.
- 3 clases: ``z = r / sigma`` (sigma = volatilidad previa escalada, PIT); el umbral tau se ajusta **solo
  con el train** de cada fold (``fit_threshold``) y se aplica después (``three_class``).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import polars as pl

from wti_news_alpha.eda import event_study as es
from wti_news_alpha.errors import SchemaError, SealedTestError
from wti_news_alpha.features import pit

DECISION = pit.DECISION


@dataclass(frozen=True)
class LabelParams:
    latency: timedelta
    max_gap: timedelta
    horizons_min: list[int]
    sessions: list[int]
    sealed_start: datetime


def build_labels(grid: pl.DataFrame, bars: pl.DataFrame, p: LabelParams) -> pl.DataFrame:
    """Retornos por horizonte e intervalos de etiqueta; mismas filas que ``grid``, ordenadas."""
    pit.require_grid(grid)
    n_sealed = grid.filter(pl.col(DECISION) >= p.sealed_start).height
    if n_sealed:
        raise SealedTestError(f"{n_sealed} decision_time en el periodo sellado (ADR-036)")
    ev = grid.sort(DECISION).select(
        pl.col(DECISION).dt.strftime("%Y%m%d%H%M%S").alias("event_id"), pl.col(DECISION).alias("t0")
    )
    if ev["event_id"].is_duplicated().any():
        raise SchemaError("decision_time con resolución inferior al segundo")
    sp = es.StudyParams(p.latency, p.max_gap, p.horizons_min, p.sessions, min(p.horizons_min))
    r = es.event_returns(ev, bars, sp)
    start = pl.coalesce(pl.col("entry_at"), pl.col("t0") + p.latency + p.max_gap)
    ends = [(start + timedelta(minutes=h)).alias(f"label_end_{h}m") for h in p.horizons_min]
    ends.append(es.session_close(start, 0).alias("label_end_session_end"))
    ends += [es.session_close(start, k).alias(f"label_end_{k}s") for k in p.sessions]
    r = r.with_columns(ends)
    names = [f"{h}m" for h in p.horizons_min] + ["session_end"] + [f"{k}s" for k in p.sessions]
    r = r.with_columns(
        pl.when(pl.col(f"label_end_{n}") < p.sealed_start)
        .then(pl.col(f"ret_{n}"))
        .alias(f"ret_{n}")
        for n in names
    )
    cols = (
        ["entry_at", "entry_close"]
        + [f"ret_{n}" for n in names]
        + [f"label_end_{n}" for n in names]
    )
    out = r.select(pl.col("t0").alias(DECISION), *cols).sort(DECISION)
    if out.height != grid.height:
        raise SchemaError(f"etiquetas: {grid.height} instantes → {out.height} filas")
    return out


def fit_threshold(z_train: pl.Series, *, neutral_share: float) -> float:
    """τ tal que una fracción ``neutral_share`` de |z| del **train** queda en neutral."""
    a = z_train.drop_nulls().abs()
    if a.len() == 0:
        raise SchemaError("umbral de 3 clases: train sin valores")
    tau = a.quantile(neutral_share, interpolation="nearest")
    if tau is None or tau <= 0:
        raise SchemaError(f"umbral de 3 clases no válido: {tau}")
    return float(tau)


def three_class(z: pl.Series, tau: float) -> pl.Series:
    """1 alcista si z > τ, -1 bajista si z < -τ, 0 neutral; nulo si z es nulo."""
    return (
        pl.select(pl.when(z > tau).then(1).when(z < -tau).then(-1).when(z.is_not_null()).then(0))
        .to_series()
        .cast(pl.Int8)
        .rename(z.name)
    )

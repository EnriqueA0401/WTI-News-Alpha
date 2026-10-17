"""Features de noticias desde la capa de eventos (FASE_04.md; Documento §7; ADR-032/034/035).

Entradas:
- artículos de ``events-v3``: ``first_available_at`` (UTC), ``family``, ``eligible``
  (directo, no duplicado, de una familia con eventos y con país), ``is_event_first``,
  ``title_source``;
- ficheros GDELT de 15 min (``slots``): ``source_file_ts`` (nominal), ``available_at`` y
  ``n`` (artículos del fichero en la capa).

Invariantes:
- Solo cuentan los artículos ``eligible``: los duplicados no son noticias nuevas.
- Conteos en ``(t - w, t]`` como diferencia de acumulados ``C(t) - C(t - w)``, con
  ``asof`` backward: solo pasado.
- **Hueco de la fuente ≠ sin noticias** (``reports/f1/gdelt.md`` §2). Un fichero es
  *bueno* si tiene ≥ ``min_slot_articles`` artículos y llega a tiempo (``available_at ≤
  nominal + slot_deadline``). Cualquier otro instante nominal (falta, degradado o tardío) es
  un **hueco**. En t, una ventana es válida solo si el fichero más reciente que necesita
  (nominal = t - ``slot_lag`` redondeado a 15 min) ya está disponible y no hay huecos en los
  nominales que cubre; si no, la feature es **nula**. Lo mismo para la edad del último evento
  (nula si hubo un hueco después de él). El estado de cada fichero anterior al necesario se
  conoce en t (su plazo ya venció), así que la validez no usa información futura.
- El mecanismo no se usa (ADR-032). La época (título real o slug) entra como la
  proporción de artículos con título real en la ventana más larga (ADR-033/034).
"""

from dataclasses import dataclass
from datetime import timedelta

import polars as pl

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.features import pit
from wti_news_alpha.timeutils import require_utc_column

DECISION = pit.DECISION
FA = "first_available_at"
NOMINAL = "source_file_ts"
SLOT = timedelta(minutes=15)
REQUIRED = {FA, "family", "eligible", "is_event_first", "title_source"}


@dataclass(frozen=True)
class NewsParams:
    """Parámetros de ``config/features.yaml`` para las features de noticias."""

    families: list[str]
    windows: list[int]
    slot_lag: timedelta  # nominal → disponible (gdelt-news: +15 min)
    slot_deadline: timedelta  # un fichero que llega más tarde cuenta como hueco
    min_slot_articles: int  # menos artículos: fichero degradado = hueco


def _cumulative(arts: pl.DataFrame) -> pl.DataFrame:
    """Acumulados por instante de disponibilidad (único): artículos, eventos nuevos, con título."""
    per = (
        arts.group_by(FA)
        .agg(
            n=pl.len(),
            new=pl.col("is_event_first").sum(),
            titled=(pl.col("title_source") == "title").sum(),
        )
        .sort(FA)
    )
    return per.select(
        pl.col(FA).alias(pit.AVAILABLE),
        pl.col("n").cum_sum().alias("cn"),
        pl.col("new").cum_sum().alias("cnew"),
        pl.col("titled").cum_sum().alias("ctitled"),
    )


def _cum_at(times: pl.DataFrame, cum: pl.DataFrame, key: str) -> pl.DataFrame:
    """Acumulados en cada instante de ``times``. Antes del primer artículo el acumulado es
    0 por definición (no es un relleno: la cobertura de la fuente se controla aparte)."""
    j = pit.asof_latest(times, cum, prefix=key, values=["cn", "cnew", "ctitled"])
    return j.with_columns(
        pl.when(pl.col(f"{key}_available_at").is_not_null()).then(pl.col(c)).otherwise(0).alias(c)
        for c in (f"{key}_cn", f"{key}_cnew", f"{key}_ctitled")
    )


def good_slots(slots: pl.DataFrame, p: NewsParams) -> pl.DataFrame:
    """Ficheros buenos por nominal, con ``hole_last``: último nominal con hueco antes de él
    (el anterior al primer fichero cuenta como hueco: no hay cobertura antes)."""
    missing = {NOMINAL, pit.AVAILABLE, "n"} - set(slots.columns)
    if missing:
        raise SchemaError(f"faltan columnas de ficheros GDELT: {sorted(missing)}")
    require_utc_column(slots, NOMINAL)
    require_utc_column(slots, pit.AVAILABLE)
    if slots[NOMINAL].is_duplicated().any():
        raise SchemaError("ficheros GDELT con nominal duplicado")
    ok = (pl.col("n") >= p.min_slot_articles) & (
        pl.col(pit.AVAILABLE) <= pl.col(NOMINAL) + p.slot_deadline
    )
    g = slots.filter(ok).sort(NOMINAL)
    if g.height == 0:
        raise SchemaError("ningún fichero GDELT bueno")
    ts = pl.col(NOMINAL)
    brk = ((ts - ts.shift(1)) > SLOT).fill_null(True)
    first_hole = g[NOMINAL].min() - SLOT  # type: ignore[operator]
    return g.select(
        ts.alias("g_ts"),
        pl.col(pit.AVAILABLE).alias("g_avail"),
        pl.when(brk).then(ts - SLOT).otherwise(pl.lit(first_hole)).cum_max().alias("g_hole_last"),
    )


def _slot_state(grid: pl.DataFrame, good: pl.DataFrame, p: NewsParams) -> pl.DataFrame:
    """Por ``decision_time``: si el fichero necesario está disponible (``ok``) y el último hueco."""
    needed = (pl.col(DECISION) - p.slot_lag).dt.truncate("15m")
    g = grid.select(DECISION).with_columns(_need=needed).sort("_need")
    j = g.join_asof(good, left_on="_need", right_on="g_ts", strategy="backward")
    ok = (pl.col("g_ts") == pl.col("_need")) & (pl.col("g_avail") <= pl.col(DECISION))
    return j.sort(DECISION).select(
        DECISION,
        ok.fill_null(False).alias("_ok"),
        "g_hole_last",
        pl.when(ok).then(pl.col("g_avail")).alias("news_slot_available_at"),
    )


def news_features(
    grid: pl.DataFrame, articles: pl.DataFrame, slots: pl.DataFrame, p: NewsParams
) -> pl.DataFrame:
    """Añade ``news_*`` a la rejilla (mismas filas, ordenada por ``decision_time``)."""
    if not p.windows:
        raise SchemaError("windows vacío")
    missing = REQUIRED - set(articles.columns)
    if missing:
        raise SchemaError(f"faltan columnas de artículos: {sorted(missing)}")
    require_utc_column(articles, FA)
    pit.require_grid(grid)
    state = _slot_state(grid, good_slots(slots, p), p)
    el = articles.filter(pl.col("eligible"))
    out = grid.sort(DECISION).with_columns(
        news_source_ok=state["_ok"],
        news_slot_available_at=state["news_slot_available_at"],
        _hole=state["g_hole_last"],
    )
    ok = pl.col("news_source_ok")
    hole = pl.col("_hole")
    subsets = {"all": el, **{f: el.filter(pl.col("family") == f) for f in p.families}}
    for key, sub in subsets.items():
        cum = _cumulative(sub)
        now = _cum_at(out.select(DECISION), cum, "_now")
        if key == "all":
            out = out.with_columns(
                pl.when(ok).then(now["_now_available_at"]).alias("news_available_at")
            )
        for w in p.windows:
            lag = timedelta(minutes=w)
            back = _cum_at(out.select(pl.col(DECISION) - lag), cum, "_b")
            valid = ok & (hole <= pl.col(DECISION) - lag - p.slot_lag)
            n = now["_now_cn"] - back["_b_cn"]
            new = now["_now_cnew"] - back["_b_cnew"]
            out = out.with_columns(
                pl.when(valid).then(n).alias(f"news_n_{key}_{w}m"),
                pl.when(valid).then(new).alias(f"news_new_{key}_{w}m"),
            )
            if key == "all" and w == max(p.windows):
                titled = now["_now_ctitled"] - back["_b_ctitled"]
                share = pl.when(valid & (n > 0)).then(titled / n)
                out = out.with_columns(share.alias(f"news_title_share_{w}m"))
    for f in p.families:
        starts = (
            subsets[f]
            .filter(pl.col("is_event_first"))
            .select(pl.col(FA).alias(pit.AVAILABLE))
            .unique()
        )
        j = pit.asof_latest(out.select(DECISION), starts, prefix="_ev", values=[])
        ev = j["_ev_available_at"]
        age = (pl.col(DECISION) - ev).dt.total_microseconds() / 60e6
        # Sin hueco desde el fichero del último evento (si no, pudo perderse uno más reciente).
        valid = ok & (hole <= ev - p.slot_lag - SLOT)
        out = out.with_columns(pl.when(valid).then(age).alias(f"news_event_age_min_{f}"))
    return out.drop("_hole")

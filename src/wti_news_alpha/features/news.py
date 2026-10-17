"""Features de noticias desde la capa de eventos (FASE_04.md; Documento §7; ADR-032/034).

Entrada: artículos de ``events-v3`` con ``first_available_at`` (UTC), ``family``,
``eligible`` (directo, no duplicado, de una familia con eventos y con país),
``is_event_first`` y ``title_source``.

Invariantes:
- Solo cuentan los artículos ``eligible``: los duplicados no son noticias nuevas.
- Conteos en ventanas ``(t - w, t]`` como diferencia de acumulados ``C(t) - C(t - w)``,
  con ``C`` = artículos con ``first_available_at ≤ x`` (``asof`` backward): solo pasado.
- Antes de ``coverage_start`` no hay datos: una ventana que empieza antes de la cobertura
  es **nula**, no cero. Después, "ningún artículo" sí es un cero real.
- El mecanismo no se usa (ADR-032). La época (título real o slug) entra como la
  proporción de artículos con título real en la ventana más larga (ADR-033/034).
"""

from datetime import datetime, timedelta

import polars as pl

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.features import pit
from wti_news_alpha.timeutils import require_utc_column

DECISION = pit.DECISION
FA = "first_available_at"
REQUIRED = {FA, "family", "eligible", "is_event_first", "title_source"}


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
    0 por definición (no es un relleno de datos ausentes: la cobertura se controla aparte)."""
    j = pit.asof_latest(times, cum, prefix=key, values=["cn", "cnew", "ctitled"])
    return j.with_columns(
        pl.when(pl.col(f"{key}_available_at").is_not_null()).then(pl.col(c)).otherwise(0).alias(c)
        for c in (f"{key}_cn", f"{key}_cnew", f"{key}_ctitled")
    )


def news_features(
    grid: pl.DataFrame,
    articles: pl.DataFrame,
    *,
    families: list[str],
    windows: list[int],
    coverage_start: datetime,
) -> pl.DataFrame:
    """Añade ``news_*`` a la rejilla (mismas filas, ordenada por ``decision_time``)."""
    if not windows:
        raise SchemaError("windows vacío")
    missing = REQUIRED - set(articles.columns)
    if missing:
        raise SchemaError(f"faltan columnas de artículos: {sorted(missing)}")
    require_utc_column(articles, FA)
    el = articles.filter(pl.col("eligible"))
    out = grid.sort(DECISION)
    covered = pl.col(DECISION) >= coverage_start
    subsets = {"all": el, **{f: el.filter(pl.col("family") == f) for f in families}}
    for key, sub in subsets.items():
        cum = _cumulative(sub)
        now = _cum_at(out.select(DECISION), cum, "_now")
        if key == "all":
            out = out.with_columns(news_available_at=now["_now_available_at"])
        for w in windows:
            lag = timedelta(minutes=w)
            back = _cum_at(out.select(pl.col(DECISION) - lag), cum, "_b")
            ok = (pl.col(DECISION) - lag) >= coverage_start
            n = now["_now_cn"] - back["_b_cn"]
            new = now["_now_cnew"] - back["_b_cnew"]
            out = out.with_columns(
                pl.when(ok).then(n).alias(f"news_n_{key}_{w}m"),
                pl.when(ok).then(new).alias(f"news_new_{key}_{w}m"),
            )
            if key == "all" and w == max(windows):
                titled = now["_now_ctitled"] - back["_b_ctitled"]
                share = pl.when(ok & (n > 0)).then(titled / n)
                out = out.with_columns(share.alias(f"news_title_share_{w}m"))
    for f in families:
        starts = (
            subsets[f]
            .filter(pl.col("is_event_first"))
            .select(pl.col(FA).alias(pit.AVAILABLE))
            .unique()
        )
        j = pit.asof_latest(out.select(DECISION), starts, prefix="_ev", values=[])
        age = (pl.col(DECISION) - j["_ev_available_at"]).dt.total_microseconds() / 60e6
        out = out.with_columns(pl.when(covered).then(age).alias(f"news_event_age_min_{f}"))
    return out.with_columns(
        pl.when(covered).then(pl.col("news_available_at")).alias("news_available_at")
    )

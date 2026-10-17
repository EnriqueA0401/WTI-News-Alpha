"""Agrupación online de artículos en eventos (Documento §6.1 paso 5; FASE_03.md).

Línea base transparente (fase 1 del §6.3): un evento es una racha de artículos
elegibles con la misma clave (familia, país principal) separados por huecos ≤
``gap``. La validez se mide en la muestra auditada de G2.

Invariantes:
- Online: la pertenencia y las métricas acumuladas de un artículo dependen solo
  de artículos anteriores en el orden de disponibilidad (test de truncamiento).
- ``event_id`` = ``article_id`` del primer artículo del evento.
- Los duplicados heredan el evento de su original (no cuentan como artículos
  nuevos ni como fuentes nuevas: no son shocks separados).
- Un artículo cuyo mecanismo (no ambiguo) difiere del primero (no ambiguo) del
  evento se marca (``mechanism_differs``): actualización que cambia el hecho.
- Un evento dura como máximo ``max_span`` desde su primer artículo.
"""

from datetime import timedelta

import polars as pl

from wti_news_alpha.errors import SchemaError

ORDER = ("first_available_at", "source_file_ts", "row_in_file")
NO_COUNTRY = "XX"
AMBIGUOUS = "ambiguous"


def primary_country(locations: pl.Expr) -> pl.Expr:
    """País (FIPS de GDELT) más mencionado en las localizaciones V1, o ``XX``.

    Las localizaciones V1 no están ordenadas por relevancia (revisión de F3): se
    toma la moda; los empates se resuelven por orden alfabético (determinista).
    """
    ccs = locations.str.split(";").list.eval(
        pl.element().str.split("#").list.get(2, null_on_oob=True)
    )
    cc = ccs.list.eval(
        pl.element().filter(pl.element().str.len_chars() > 0).mode().sort().first()
    ).list.first()
    return pl.when(cc.str.len_chars() > 0).then(cc).otherwise(pl.lit(NO_COUNTRY))


def assign_events(df: pl.DataFrame, gap: timedelta, max_span: timedelta) -> pl.DataFrame:
    """Añade ``event_id``, ``event_seq``, ``event_sources_so_far``, ``is_event_first`` y
    ``mechanism_differs`` a las filas con ``eligible``; el resto queda nulo.

    Entrada: ``article_id``, ``ORDER``, ``eligible``, ``family``, ``mechanism``,
    ``country``, ``source_key``.
    """
    need = {"article_id", *ORDER, "eligible", "family", "mechanism", "country", "source_key"}
    missing = need - set(df.columns)
    if missing:
        raise SchemaError(f"faltan columnas para agrupar: {sorted(missing)}")
    d = df.sort(*ORDER)
    el = d.filter(pl.col("eligible"))
    key = ["family", "country"]
    fa = pl.col("first_available_at")
    el = el.with_columns(_gap=fa - fa.shift(1).over(key))
    el = el.with_columns(
        _sess=(pl.col("_gap") > gap).fill_null(True).cast(pl.UInt32).cum_sum().over(key)
    )
    # Duración máxima: un evento se parte en tramos de ``max_span`` desde su inicio
    # (las rachas de claves muy activas no se cierran nunca por hueco).
    start = fa.min().over([*key, "_sess"])
    el = el.with_columns(_slot=((fa - start) / max_span).floor().cast(pl.UInt32))
    ev = [*key, "_sess", "_slot"]
    el = el.with_columns(
        event_id=pl.col("article_id").first().over(ev),
        event_seq=pl.int_range(1, pl.len() + 1, dtype=pl.UInt32).over(ev),
        _first_mech=pl.col("mechanism").first().over(ev),
        _new_src=(pl.col("source_key").cum_count().over([*ev, "source_key"]) == 1),
    )
    el = el.with_columns(
        event_sources_so_far=pl.col("_new_src").cast(pl.UInt32).cum_sum().over(ev),
        is_event_first=pl.col("event_seq") == 1,
        mechanism_differs=(pl.col("mechanism") != pl.col("_first_mech"))
        & (pl.col("mechanism") != AMBIGUOUS)
        & (pl.col("_first_mech") != AMBIGUOUS),
    ).select(
        "article_id",
        "event_id",
        "event_seq",
        "event_sources_so_far",
        "is_event_first",
        "mechanism_differs",
    )
    return d.join(el, on="article_id", how="left", validate="1:1")  # left 1:1: mismas filas


def inherit_events(df: pl.DataFrame) -> pl.DataFrame:
    """Los duplicados heredan ``event_id`` de su original raíz (``dup_of`` ya resuelto)."""
    orig = df.select(pl.col("article_id").alias("dup_of"), pl.col("event_id").alias("_orig_ev"))
    out = df.join(orig, on="dup_of", how="left", validate="m:1")  # left m:1: mismas filas
    return out.with_columns(event_id=pl.coalesce("event_id", "_orig_ev")).drop("_orig_ev")

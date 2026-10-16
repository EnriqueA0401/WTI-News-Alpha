"""Deduplicación online de artículos (Documento §6.1 paso 2; FASE_03.md).

Invariantes:
- Un artículo solo puede ser duplicado de otro **anterior** en el orden de
  disponibilidad (``first_available_at``, ``source_file_ts``, ``row_in_file``).
  El original de cada grupo es el primero en ese orden, así que el resultado
  en t no depende de artículos posteriores (test de truncamiento).
- Tipos, por prioridad:
  1. ``url``: misma URL canónica;
  2. ``same_source_title``: mismo medio y mismo título normalizado;
  3. ``syndicated_title``: mismo título normalizado en otro medio (re-publicación).
- Las claves son hashes de 64 bits de las cadenas canónicas, por memoria
  (12 M filas). La probabilidad de colisión es despreciable (< 1e-5).
"""

from datetime import timedelta

import polars as pl

from wti_news_alpha.errors import SchemaError

ORDER = ("first_available_at", "source_file_ts", "row_in_file")
_TRACKING = r"(utm_[a-z_]+|fbclid|gclid|ocid|cmpid|mc_cid|mc_eid|ref|src)=[^&#]*"
MIN_TITLE_CHARS = 20
# Sufijo de medio al final: " - Medio", " | Medio" o con raya (U+2013, U+2014), escapados para Rust.
_SITE_SUFFIX = r"^(.{20,}?)\s+[-|\u2013\u2014]\s+[^-|\u2013\u2014]{1,40}$"


def canonical_url(url: pl.Expr) -> pl.Expr:
    """URL canónica: sin esquema, ``www.``/``m.``/``amp.``, puerto, fragmento ni tracking."""
    u = url.str.strip_chars().str.replace(r"^[A-Za-z]+://", "")
    host = u.str.extract(r"^([^/?#]*)", 1).str.to_lowercase()
    host = host.str.replace(r":(80|443)$", "").str.replace(r"^(www\d?|m|amp|mobile)\.", "")
    rest = u.str.extract(r"^[^/?#]*(.*)$", 1).fill_null("")
    rest = rest.str.replace(r"#.*$", "")
    rest = rest.str.replace_all(_TRACKING, "")
    rest = rest.str.replace_all(r"\?&+", "?").str.replace_all(r"&{2,}", "&")
    rest = rest.str.replace(r"[?&]+$", "")
    rest = rest.str.replace(r"/amp/?$", "").str.replace(r"\.amp$", "")
    rest = rest.str.replace(r"/+$", "")
    return pl.concat_str([host, rest])


def normalized_title(title: pl.Expr) -> pl.Expr:
    """Título en minúsculas, sin sufijo de medio (`` - Medio``/`` | Medio``) ni puntuación.

    Nulo si queda con menos de ``MIN_TITLE_CHARS`` caracteres (títulos genéricos).
    """
    t = title.str.strip_chars().str.replace(_SITE_SUFFIX, "$1")
    t = t.str.to_lowercase().str.replace_all(r"[^\p{L}\p{N}]+", " ").str.strip_chars()
    return pl.when(t.str.len_chars() >= MIN_TITLE_CHARS).then(t)


def mark_duplicates(df: pl.DataFrame, title_window: timedelta) -> pl.DataFrame:
    """Añade ``dup_kind`` y ``dup_of`` (nulos en los originales).

    Entrada: ``article_id``, ``first_available_at``, ``source_file_ts``,
    ``row_in_file``, ``source_common_name``, ``url``, ``title``.
    Por título solo se agrupan artículos encadenados con huecos ≤ ``title_window``
    (un titular genérico repetido meses después no es una re-publicación).
    """
    missing = {"article_id", *ORDER, "source_common_name", "url", "title"} - set(df.columns)
    if missing:
        raise SchemaError(f"faltan columnas para deduplicar: {sorted(missing)}")
    if df["article_id"].is_duplicated().any():
        raise SchemaError("article_id duplicado")
    canon = canonical_url(pl.col("url"))
    title = normalized_title(pl.col("title"))
    d = df.sort(*ORDER).with_columns(
        _url_ok=canon.is_not_null() & (canon != ""),
        _url_key=canon.hash(seed=0),
        _title_ok=title.is_not_null(),
        _title_key=title.hash(seed=0),
        _src_key=pl.col("source_common_name").fill_null("").str.to_lowercase().hash(seed=0),
    )
    fa = pl.col("first_available_at")

    def session(keys: list[str]) -> pl.Expr:
        gap = fa - fa.shift(1).over(keys)
        return (gap > title_window).fill_null(True).cast(pl.UInt32).cum_sum().over(keys)

    d = d.with_columns(_s_st=session(["_src_key", "_title_key"]), _s_t=session(["_title_key"]))
    d = d.with_columns(
        _f_url=pl.when(pl.col("_url_ok")).then(pl.col("article_id").first().over("_url_key")),
        _f_st=pl.when(pl.col("_title_ok")).then(
            pl.col("article_id").first().over("_src_key", "_title_key", "_s_st")
        ),
        _f_t=pl.when(pl.col("_title_ok")).then(
            pl.col("article_id").first().over("_title_key", "_s_t")
        ),
    )
    is_url = (pl.col("_f_url") != pl.col("article_id")).fill_null(False)
    is_st = (pl.col("_f_st") != pl.col("article_id")).fill_null(False)
    is_t = (pl.col("_f_t") != pl.col("article_id")).fill_null(False)
    return d.with_columns(
        dup_kind=pl.when(is_url)
        .then(pl.lit("url"))
        .when(is_st)
        .then(pl.lit("same_source_title"))
        .when(is_t)
        .then(pl.lit("syndicated_title"))
        .otherwise(pl.lit(None, dtype=pl.Utf8)),
        dup_of=pl.when(is_url)
        .then(pl.col("_f_url"))
        .when(is_st)
        .then(pl.col("_f_st"))
        .when(is_t)
        .then(pl.col("_f_t"))
        .otherwise(pl.lit(None, dtype=pl.Utf8)),
    ).drop(
        "_url_ok",
        "_url_key",
        "_title_ok",
        "_title_key",
        "_src_key",
        "_s_st",
        "_s_t",
        "_f_url",
        "_f_st",
        "_f_t",
    )

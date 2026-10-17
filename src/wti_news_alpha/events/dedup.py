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
_TRACKING = r"(^|[?&])(utm_[a-z_]+|fbclid|gclid|ocid|cmpid|mc_cid|mc_eid|ref|src)=[^&#]*"
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
    rest = rest.str.replace_all(_TRACKING, "$1")
    rest = rest.str.replace_all(r"\?&+", "?").str.replace_all(r"&{2,}", "&")
    rest = rest.str.replace(r"[?&]+$", "")
    rest = rest.str.replace(r"/amp/?$", "").str.replace(r"\.amp$", "")
    rest = rest.str.replace(r"/+$", "")
    return pl.concat_str([host, rest])


_ENTITIES = {
    "&amp;": "&",
    "&quot;": '"',
    "&#39;": "'",
    "&#039;": "'",
    "&apos;": "'",
    "&rsquo;": "'",
    "&lsquo;": "'",
    "&#8217;": "'",
    "&#x2019;": "'",
    "&ldquo;": '"',
    "&rdquo;": '"',
    "&#8211;": " - ",
    "&#x2013;": " - ",
    "&ndash;": " - ",
    "&#8212;": " - ",
    "&#x2014;": " - ",
    "&mdash;": " - ",
    "&nbsp;": " ",
    "&#160;": " ",
}
_SLUG_EXT = r"\.(html?|php|aspx?|shtml|cms|ece)$"


def clean_title(title: pl.Expr) -> pl.Expr:
    """Título legible: entidades HTML frecuentes decodificadas y sin sufijo de medio."""
    t = title
    for ent, ch in _ENTITIES.items():
        t = t.str.replace_all(ent, ch, literal=True)
    t = t.str.replace_all(r"\s+", " ").str.strip_chars()
    return t.str.replace(_SITE_SUFFIX, "$1")


def url_slug_title(url: pl.Expr) -> pl.Expr:
    """Titular derivado del slug de la URL (segmento más largo con ≥ 4 palabras), o nulo.

    Se usa solo cuando no hay ``PAGE_TITLE`` (GDELT 2018-01 → 2019-08).
    """
    path = url.str.replace(r"^[A-Za-z]+://[^/]*", "").str.replace(r"[?#].*$", "")
    segs = path.str.split("/").list.eval(
        pl.element()
        .str.replace(_SLUG_EXT, "")
        .str.replace(r"[-_]\d+$", "")
        .filter(pl.element().str.count_matches(r"[a-zA-Z]+[-_]") >= 3)
    )
    best = segs.list.eval(pl.element().sort_by(pl.element().str.len_chars()).last()).list.first()
    return best.str.replace_all(r"[-_]+", " ").str.strip_chars()


def normalized_title(title: pl.Expr) -> pl.Expr:
    """Título en minúsculas, sin sufijo de medio (`` - Medio``/`` | Medio``) ni puntuación.

    Nulo si queda con menos de ``MIN_TITLE_CHARS`` caracteres (títulos genéricos).
    """
    t = clean_title(title)
    t = t.str.to_lowercase().str.replace_all(r"[^\p{L}\p{N}]+", " ").str.strip_chars()
    return pl.when(t.str.len_chars() >= MIN_TITLE_CHARS).then(t)


KEY_COLUMNS = ("_url_ok", "_url_key", "_title_ok", "_title_key", "_src_key")


def dedup_keys(df: pl.DataFrame) -> pl.DataFrame:
    """Sustituye ``url``, ``title`` y ``source_common_name`` por claves hash compactas.

    Permite calcular las claves mes a mes y deduplicar después todo el periodo
    sin cargar las cadenas en memoria.
    """
    missing = {"url", "title", "source_common_name"} - set(df.columns)
    if missing:
        raise SchemaError(f"faltan columnas para deduplicar: {sorted(missing)}")
    canon = canonical_url(pl.col("url"))
    title = normalized_title(pl.col("title"))
    return df.with_columns(
        _url_ok=(canon.is_not_null() & (canon != "")).fill_null(False),
        _url_key=canon.hash(seed=0),
        _title_ok=title.is_not_null(),
        _title_key=title.hash(seed=0),
        _src_key=pl.coalesce(
            pl.col("source_common_name").str.to_lowercase(),
            canon.str.extract(r"^([^/?#]*)", 1),
        )
        .fill_null("")
        .hash(seed=0),
    ).drop("url", "title", "source_common_name")


def mark_duplicates(df: pl.DataFrame, title_window: timedelta) -> pl.DataFrame:
    """Añade ``dup_kind`` y ``dup_of`` (nulos en los originales).

    Entrada: ``article_id``, ``first_available_at``, ``source_file_ts``,
    ``row_in_file`` y, o bien ``source_common_name``, ``url``, ``title``, o bien
    las claves de ``dedup_keys``.
    Por título solo se agrupan artículos encadenados con huecos ≤ ``title_window``
    (un titular genérico repetido meses después no es una re-publicación).
    """
    if not set(KEY_COLUMNS) <= set(df.columns):
        df = dedup_keys(df)
    missing = {"article_id", *ORDER} - set(df.columns)
    if missing:
        raise SchemaError(f"faltan columnas para deduplicar: {sorted(missing)}")
    if df["article_id"].is_duplicated().any():
        raise SchemaError("article_id duplicado")
    d = df.sort(*ORDER)
    fa = pl.col("first_available_at")

    def session(keys: list[str]) -> pl.Expr:
        gap = fa - fa.shift(1).over(keys)
        return (gap > title_window).fill_null(True).cast(pl.UInt32).cum_sum().over(keys)

    d = d.with_columns(_s_st=session(["_src_key", "_title_key"]), _s_t=session(["_title_key"]))
    d = d.with_columns(
        # Misma URL con titular distinto = actualización que puede cambiar el hecho: no se
        # colapsa (CLAUDE.md §7). Con título, la clave de URL incluye el título.
        _f_url=pl.when(pl.col("_url_ok") & pl.col("_title_ok"))
        .then(pl.col("article_id").first().over("_url_key", "_title_key"))
        .when(pl.col("_url_ok"))
        .then(pl.col("article_id").first().over("_url_key", "_title_ok")),
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
    out = d.with_columns(
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
    ).drop(*KEY_COLUMNS, "_s_st", "_s_t", "_f_url", "_f_st", "_f_t")
    return resolve_roots(out)


def resolve_roots(df: pl.DataFrame, max_depth: int = 10) -> pl.DataFrame:
    """``dup_of`` apunta al original raíz (un duplicado de un duplicado → el primero)."""
    out = df
    for _ in range(max_depth):
        parent = out.select(pl.col("article_id").alias("dup_of"), pl.col("dup_of").alias("_up"))
        nxt = out.join(parent, on="dup_of", how="left", validate="m:1")
        if nxt["_up"].null_count() == nxt.height:
            return nxt.drop("_up")
        out = nxt.with_columns(dup_of=pl.coalesce("_up", "dup_of")).drop("_up")
    raise SchemaError(f"cadenas de duplicados de más de {max_depth} niveles")

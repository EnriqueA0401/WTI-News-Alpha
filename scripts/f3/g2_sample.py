"""F3: muestra estratificada para la auditoría G2 (docs/labeling_guide.md, ADR-028).

Estratos (semilla fija):
- relevancia ``direct`` según las reglas: hasta ``N_PER_FAMILY`` por familia, la mitad
  con título real y la mitad con titular de slug cuando existan (los dos regímenes);
- ``indirect``: ``N_INDIRECT``;
- ``not_relevant`` difíciles (el título contiene oil, crude, gas, fuel, energy o brent):
  ``N_HARD_NEG``.
Más un ~10 % repetido a ciegas para medir la consistencia (§16.3 del Documento).
Solo artículos no duplicados, sin ``exclude_reason`` y con texto de titular.

Salida (fuera de git: los titulares son contenido de terceros, CLAUDE.md §3.7):
``data/labels/g2_sample_<version>.parquet``. Imprime solo conteos.
Uso: uv run python -I scripts/f3/g2_sample.py
"""

import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

EVENTS = REPO_ROOT / "data" / "normalized" / "events" / "events-v2"
OUT = REPO_ROOT / "data" / "labels" / "g2_sample_events-v2.parquet"
SEED = 20261017
N_PER_FAMILY = 14
N_INDIRECT = 45
N_HARD_NEG = 35
REPEAT_FRACTION = 0.10
HARD_NEG = r"(?i)\b(oil|crude|gas|fuel|energy|brent|barrels?)\b"


def main() -> None:
    cols = [
        "article_id",
        "first_available_at",
        "source_common_name",
        "url",
        "rule_title",
        "title_source",
        "relevance",
        "family",
        "mechanism",
        "dup_kind",
        "exclude_reason",
    ]
    df = (
        pl.scan_parquet(EVENTS / "articles_*.parquet")
        .select(cols)
        .filter(
            pl.col("dup_kind").is_null()
            & pl.col("exclude_reason").is_null()
            & pl.col("rule_title").is_not_null()
        )
        .collect()
    )
    parts = []
    direct = df.filter(pl.col("relevance") == "direct")
    for fam in sorted(direct["family"].unique().to_list()):
        g = direct.filter(pl.col("family") == fam)
        for src in ("title", "url_slug"):
            s = g.filter(pl.col("title_source") == src)
            n = min(s.height, N_PER_FAMILY // 2)
            parts.append(
                s.sample(n, seed=SEED, shuffle=True).with_columns(
                    stratum=pl.lit(f"direct/{fam}/{src}")
                )
            )
    ind = df.filter(pl.col("relevance") == "indirect")
    parts.append(
        ind.sample(min(ind.height, N_INDIRECT), seed=SEED).with_columns(stratum=pl.lit("indirect"))
    )
    neg = df.filter(
        (pl.col("relevance") == "not_relevant") & pl.col("rule_title").str.contains(HARD_NEG)
    )
    parts.append(
        neg.sample(min(neg.height, N_HARD_NEG), seed=SEED).with_columns(
            stratum=pl.lit("hard_negative")
        )
    )
    sample = pl.concat(parts).sample(fraction=1.0, seed=SEED, shuffle=True)
    reps = sample.sample(fraction=REPEAT_FRACTION, seed=SEED + 1).with_columns(
        stratum=pl.lit("repeat"), repeat_of=pl.col("article_id")
    )
    sample = sample.with_columns(repeat_of=pl.lit(None, dtype=pl.Utf8))
    full = pl.concat([sample, reps]).with_row_index("n", offset=1)
    # Las repeticiones van al final, mezcladas entre sí, para que no estén junto al original.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    full.write_parquet(OUT)
    print(full.height, "items;", reps.height, "repeticiones")
    print(full.group_by("stratum").len().sort("stratum"))


if __name__ == "__main__":
    main()

"""F3: segunda muestra de G2 (ADR-032): solo las 5 familias que generan eventos.

- Población: artículos ``eligible`` (directos, no duplicados, sin exclusión, con país) de
  ``opec_policy``, ``eia_inventories``, ``sanctions_geopolitics``, ``weather`` y
  ``conflict_attack``, con texto de titular.
- ``N_PER_HALF`` por familia y origen del titular (``title`` / ``url_slug``), semilla fija.
- Sin artículos de la primera muestra. Sin repeticiones: con un único etiquetador que ya
  conoce los titulares, la repetición no mide consistencia (ADR-031).
Las reglas son las mismas (``rules-v2``), así que la clasificación por artículo de
``events-v2`` es la de ``events-v3``.

Salida (fuera de git: titulares de terceros, CLAUDE.md §3.7):
``data/labels/g2_sample2_events-v3.parquet``. Imprime solo conteos.
Uso: uv run python -I scripts/f3/g2_sample2.py
"""

import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

EVENTS = REPO_ROOT / "data" / "normalized" / "events" / "events-v2"
FIRST = REPO_ROOT / "reports" / "f3" / "g2_labels.csv"
OUT = REPO_ROOT / "data" / "labels" / "g2_sample2_events-v3.parquet"
SEED = 20261018
N_PER_HALF = 15
FAMILIES = ("conflict_attack", "eia_inventories", "opec_policy", "sanctions_geopolitics", "weather")


def main() -> None:
    seen = pl.read_csv(FIRST)["article_id"]
    df = (
        pl.scan_parquet(EVENTS / "articles_*.parquet")
        .select(
            "article_id",
            "first_available_at",
            "source_common_name",
            "url",
            "rule_title",
            "title_source",
            "relevance",
            "family",
            "mechanism",
            "eligible",
        )
        .filter(
            pl.col("eligible")
            & pl.col("family").is_in(FAMILIES)
            & pl.col("rule_title").is_not_null()
            & ~pl.col("article_id").is_in(seen.implode())
        )
        .collect()
    )
    parts = []
    for fam in FAMILIES:
        for src in ("title", "url_slug"):
            s = df.filter((pl.col("family") == fam) & (pl.col("title_source") == src))
            if s.height < N_PER_HALF:
                raise ValueError(f"{fam}/{src}: solo {s.height} artículos")
            parts.append(
                s.sample(N_PER_HALF, seed=SEED, shuffle=True).with_columns(
                    stratum=pl.lit(f"direct/{fam}/{src}")
                )
            )
    sample = pl.concat(parts).sample(fraction=1.0, seed=SEED, shuffle=True)
    sample = sample.with_row_index("n", offset=1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sample.write_parquet(OUT)
    pop = df.group_by("family", "title_source").len().sort("family", "title_source")
    print(sample.height, "items")
    print(pop)


if __name__ == "__main__":
    main()

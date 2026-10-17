"""F3: precisión de las reglas frente a la muestra auditada de G2 (ADR-028, ADR-031).

Entradas:
- ``reports/f3/g2_labels.csv``: etiquetas finales y predicciones de las reglas por artículo
  (sin titulares: contenido de terceros, CLAUDE.md §3.7);
- la capa ``events-v2``, solo para contar el tamaño de cada estrato (pesos).

Métricas (solo originales; las repeticiones no cuentan):
- por estrato ``direct/<familia>/<origen del titular>``: relevancia directa, familia correcta y
  mecanismo correcto, con intervalo de Wilson al 95 %;
- precisión global de ``direct`` ponderada por el tamaño de cada estrato, con intervalo por
  bootstrap estratificado (semilla fija);
- matriz de confusión de relevancia en los estratos ``indirect`` y ``hard_negative``.

Salida: ``reports/f3/g2_metrics.json``. Imprime solo un resumen.
Uso: uv run python -I scripts/f3/g2_metrics.py
"""

import json
import math
import random
from typing import Any

import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

LABELS = REPO_ROOT / "reports" / "f3" / "g2_labels.csv"
EVENTS = REPO_ROOT / "data" / "normalized" / "events" / "events-v2"
OUT = REPO_ROOT / "reports" / "f3" / "g2_metrics.json"
SEED = 20261017
N_BOOT = 10_000
Z = 1.959964
NO_EVENT_FAMILIES = ("other", "market_report", "opinion")


def wilson(k: int, n: int) -> tuple[float, float]:
    """Intervalo de Wilson al 95 % para k éxitos en n ensayos."""
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    den = 1 + Z**2 / n
    mid = (p + Z**2 / (2 * n)) / den
    half = Z * math.sqrt(p * (1 - p) / n + Z**2 / (4 * n**2)) / den
    return (round(mid - half, 4), round(mid + half, 4))


def rate(flags: pl.Series) -> dict[str, Any]:
    k, n = int(flags.sum()), flags.len()
    return {"k": k, "n": n, "p": round(k / n, 4) if n else None, "ci95": wilson(k, n)}


def population() -> pl.DataFrame:
    """Artículos por estrato en la población muestreada (mismo filtro que g2_sample.py)."""
    return (
        pl.scan_parquet(EVENTS / "articles_*.parquet")
        .filter(
            pl.col("dup_kind").is_null()
            & pl.col("exclude_reason").is_null()
            & pl.col("rule_title").is_not_null()
            & (pl.col("relevance") == "direct")
        )
        .group_by("family", "title_source")
        .agg(N=pl.len())
        .collect()
        .with_columns(stratum=pl.format("direct/{}/{}", "family", "title_source"))
        .select("stratum", "N")
    )


def main() -> None:
    lab = pl.read_csv(LABELS).filter(pl.col("repeat_of").is_null())
    lab = lab.with_columns(
        ok_rel=pl.col("gold_relevance") == "direct",
        ok_fam=(pl.col("gold_relevance") == "direct")
        & (pl.col("gold_family") == pl.col("rule_family")),
        ok_event=(pl.col("gold_relevance") == "direct")
        & ~pl.col("gold_family").is_in(NO_EVENT_FAMILIES),
        ok_mech=pl.col("gold_mechanism") == pl.col("rule_mechanism"),
    )
    direct = lab.filter(pl.col("stratum").str.starts_with("direct/"))
    pop = population()
    strata = direct.group_by("stratum").agg(
        n=pl.len(), k_rel=pl.col("ok_rel").sum(), k_fam=pl.col("ok_fam").sum()
    )
    strata = strata.join(pop, on="stratum", how="left", validate="1:1")
    if strata["N"].null_count():
        raise ValueError("estrato de la muestra sin población")
    total = int(strata["N"].sum())
    w = {s: n / total for s, n in strata.select("stratum", "N").iter_rows()}
    rng = random.Random(SEED)  # noqa: S311 (bootstrap, no criptografía)
    groups = {s: direct.filter(pl.col("stratum") == s) for s in sorted(w)}

    def weighted(col: str, event_families_only: bool = False) -> dict[str, object]:
        keep = [
            s for s in groups if not (event_families_only and s.split("/")[1] in NO_EVENT_FAMILIES)
        ]
        ws = {s: w[s] / sum(w[k] for k in keep) for s in keep}
        vals = {s: [int(x) for x in groups[s][col]] for s in keep}
        point = sum(ws[s] * sum(v) / len(v) for s, v in vals.items())
        boot = sorted(
            sum(ws[s] * sum(rng.choices(v, k=len(v))) / len(v) for s, v in vals.items())
            for _ in range(N_BOOT)
        )
        lo, hi = boot[int(0.025 * N_BOOT)], boot[int(0.975 * N_BOOT) - 1]
        return {"p": round(point, 4), "ci95_bootstrap": [round(lo, 4), round(hi, 4)]}

    by_family: dict[str, dict[str, Any]] = {}
    for fam in sorted(direct["rule_family"].unique()):
        g = direct.filter(pl.col("rule_family") == fam)
        mech = g.filter(pl.col("rule_mechanism") != "ambiguous")
        by_family[fam] = {
            "relevance_direct": rate(g["ok_rel"]),
            "family_correct": rate(g["ok_fam"]),
            "mechanism_correct_when_rule_not_ambiguous": rate(mech["ok_mech"]),
            "by_title_source": {
                src: {
                    "relevance_direct": rate(h["ok_rel"]),
                    "family_correct": rate(h["ok_fam"]),
                }
                for src in ("title", "url_slug")
                if (h := g.filter(pl.col("title_source") == src)).height
            },
        }
    by_source = {
        src: {
            "relevance_direct": rate(h["ok_rel"]),
            "family_correct": rate(h["ok_fam"]),
        }
        for src in ("title", "url_slug")
        if (h := direct.filter(pl.col("title_source") == src)).height
    }
    mech_all = direct.filter(pl.col("rule_mechanism") != "ambiguous")
    confusion = {
        s: dict(
            lab.filter(pl.col("stratum") == s)
            .group_by("gold_relevance")
            .len()
            .sort("gold_relevance")
            .iter_rows()
        )
        for s in ("indirect", "hard_negative")
    }
    result = {
        "labels": "propuestas de Claude aceptadas sin revisión ítem a ítem (ADR-031)",
        "n_originals": lab.height,
        "n_direct_sample": direct.height,
        "weighted_direct": {
            "relevance_direct": weighted("ok_rel"),
            "family_correct": weighted("ok_fam"),
            "event_eligible_correct": weighted("ok_event"),
        },
        # Solo familias que generan eventos (las que entran en M4).
        "weighted_direct_event_families": {
            "relevance_direct": weighted("ok_rel", event_families_only=True),
            "family_correct": weighted("ok_fam", event_families_only=True),
        },
        "by_title_source": by_source,
        "by_family": by_family,
        "mechanism_correct_when_rule_not_ambiguous": rate(mech_all["ok_mech"]),
        "rule_ambiguous_mechanism_share": rate(direct["rule_mechanism"] == "ambiguous"),
        "gold_relevance_in_other_strata": confusion,
        "population_by_stratum": dict(strata.select("stratum", "N").sort("stratum").iter_rows()),
        "seed": SEED,
        "n_bootstrap": N_BOOT,
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result["weighted_direct"], indent=1))
    print(json.dumps(result["weighted_direct_event_families"], indent=1))
    print(json.dumps(by_source, indent=1))
    print("mecanismo:", result["mechanism_correct_when_rule_not_ambiguous"])
    print("otros estratos:", confusion)
    for fam, v in by_family.items():
        print(
            fam,
            v["relevance_direct"]["p"],
            v["family_correct"]["p"],
            v["mechanism_correct_when_rule_not_ambiguous"]["p"],
        )


if __name__ == "__main__":
    main()

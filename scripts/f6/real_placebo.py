"""F6 (G4): placebo con datos reales (CLAUDE.md §8.6) y línea base M3.

- Datos: ``feat-v1`` + ``label-v1``, solo periodo de investigación (la guardia
  ``SealedGuard.assert_research_only`` lo comprueba).
- **M4 placebo:** las columnas ``news_*`` de cada día se sustituyen por las de un día
  **anterior** con el mismo día de la semana (1-8 semanas antes, elegido con semilla fija y
  dentro del mismo tramo continuo de datos), a la misma hora. Conserva la estructura y la
  disponibilidad de las noticias (todo lo usado es anterior a t) pero rompe su relación con el
  precio: M4 placebo no debería superar a M3. **No se ejecuta M4 con las noticias reales.**
- Horizontes principales (ADR-040): 15 y 60 min. Folds trimestrales, purga + 1 día.

Salida: ``reports/f6/placebo.json`` y filas en ``runs/registry.csv``.
Uso: uv run python -I scripts/f6/real_placebo.py
"""

import csv
import json
import random
from datetime import UTC, datetime, timedelta

import polars as pl

from wti_news_alpha.config import (
    CONFIG_DIR,
    load_features_config,
    load_labels_config,
    load_project_config,
)
from wti_news_alpha.evaluation import sealed_guard as sg
from wti_news_alpha.labels import splits
from wti_news_alpha.models import compare, pipeline
from wti_news_alpha.runlog import REPO_ROOT, current_commit

OUT = REPO_ROOT / "reports" / "f6"
SEED = 20261017
FIRST_TEST = datetime(2019, 1, 1, tzinfo=UTC)
M3 = pipeline.ModelSpec("M3", ("mkt", "wpsr", "cot", "fred", "cal"))
M4P = pipeline.ModelSpec("M4_placebo", ("mkt", "wpsr", "cot", "fred", "cal", "news"))


def load() -> tuple[pl.DataFrame, sg.SealedGuard, datetime]:
    proj = load_project_config(CONFIG_DIR)
    win = proj.splits.sealed_test_window.require("sealed_test_window")
    guard = sg.SealedGuard(
        sg.Window(win.start, win.end),
        repo=REPO_ROOT,
        prereg="preregistration/H1.yaml",
        log=REPO_ROOT / "runs" / "sealed_access.jsonl",
    )
    fv = load_features_config(CONFIG_DIR).version
    lv = load_labels_config(CONFIG_DIR).version
    feats = pl.scan_parquet(REPO_ROOT / "data" / "features" / fv / "*.parquet").filter(
        pl.col("decision_time") < win.start
    )
    labs = pl.scan_parquet(REPO_ROOT / "data" / "labels" / lv / "*.parquet").select(
        "decision_time", "ret_15m", "ret_60m", "label_end_15m", "label_end_60m"
    )
    df = feats.join(labs, on="decision_time", how="inner").collect().sort("decision_time")
    guard.assert_research_only(df)
    return df, guard, win.start


def placebo_news(df: pl.DataFrame) -> pl.DataFrame:
    """Sustituye ``news_*`` de cada día por las de un día anterior con el mismo día de la semana."""
    news = [c for c in df.columns if c.startswith("news_")]
    days = df.select(pl.col("decision_time").dt.date().unique().sort())["decision_time"].to_list()
    have = set(days)
    rng = random.Random(SEED)  # noqa: S311 (placebo, no criptografía)
    mapping = []
    for d in days:
        options = [d - timedelta(weeks=k) for k in range(1, 9) if d - timedelta(weeks=k) in have]
        mapping.append((d, rng.choice(options) if options else None))
    m = pl.DataFrame(mapping, schema={"_d": pl.Date, "_src": pl.Date}, orient="row")
    src = df.select(
        pl.col("decision_time").dt.date().alias("_src"),
        pl.col("decision_time").dt.time().alias("_tod"),
        *news,
    )
    base = df.drop(news).with_columns(
        _d=pl.col("decision_time").dt.date(), _tod=pl.col("decision_time").dt.time()
    )
    out = base.join(m, on="_d", how="left").join(src, on=["_src", "_tod"], how="left")
    return out.drop("_d", "_tod", "_src").select(df.columns)


def registry_row(spec: str, h: int, commit: str, preds: pl.DataFrame) -> dict[str, str]:
    by_fold = preds.group_by("fold").agg(pl.col("loss").mean()).sort("fold")
    return {
        "run_id": f"f6-{spec}-h{h}",
        "timestamp_utc": datetime.now(tz=UTC).isoformat(),
        "commit": commit,
        "hypothesis_id": "H1-placebo",
        "phase": "F6",
        "model_id": spec,
        "data_version": "dwx-xti-m1-v3/events-v3",
        "feature_version": "feat-v1",
        "label_version": "label-v1",
        "split_id": f"wf-quarter-from-2019Q1-embargo1d-h{h}",
        "hyperparams_json": json.dumps(M3.params, sort_keys=True),
        "seed": str(M3.params["random_state"]),
        "metrics_by_fold_json": json.dumps({str(f): round(v, 6) for f, v in by_fold.iter_rows()}),
        "status": "ok",
        "notes": "placebo de noticias (días anteriores)" if "placebo" in spec else "línea base",
    }


def main() -> None:
    commit = current_commit()
    df, _, sealed_start = load()
    pl_df = placebo_news(df)
    results = {"commit": commit, "seed": SEED, "n_rows": df.height}
    rows = []
    for h in (15, 60):
        folds = splits.walk_forward(
            pipeline.purge_frame(df, h),
            first_test=FIRST_TEST,
            end=sealed_start,
            step="quarter",
            embargo=timedelta(days=1),
        )
        p3 = pipeline.run_walk_forward(df, M3, folds, horizon_min=h)
        p4 = pipeline.run_walk_forward(pl_df, M4P, folds, horizon_min=h)
        j = p3.join(p4, on="decision_time", suffix="_p", validate="1:1")
        blocks = j["decision_time"].dt.strftime("%Y-%m-%d").to_list()
        c = compare.compare_losses(
            j["loss"].to_list(), j["loss_p"].to_list(), blocks, n_boot=1000, seed=SEED
        )
        results[f"h{h}"] = {
            "n_folds": len(folds),
            "n_test_rows": j.height,
            "loss_M3": round(float(j["loss"].mean()), 5),  # type: ignore[arg-type]
            "loss_M4_placebo": round(float(j["loss_p"].mean()), 5),  # type: ignore[arg-type]
            "uniform_loss_ln3": 1.09861,
            "mean_diff_M3_minus_placebo": c.mean_diff,
            "ci95": [c.lo, c.hi],
            "p_dm": c.p_dm,
            "p_bootstrap": c.p_bootstrap,
            "n_days": c.n_blocks,
        }
        rows += [registry_row("M3", h, commit, p3), registry_row("M4_placebo", h, commit, p4)]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "placebo.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    reg = REPO_ROOT / "runs" / "registry.csv"
    with reg.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writerows(rows)
    print(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()

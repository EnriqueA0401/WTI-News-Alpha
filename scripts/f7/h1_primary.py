"""F7: prueba principal de H1 (pre-registro congelado, ADR-044) y escalera de ablación M0-M4.

- Datos: ``feat-v1`` + ``label-v1``, solo periodo de investigación (guardia del sellado).
- Se niega a correr si el árbol tiene cambios sin commitear o si el pre-registro no está
  commiteado, sin cambios y aprobado. Horizontes, semilla y criterios: los de ``H1.yaml``.
- Modelos (FASE_07.md): M0 climatología, M1 logística (mkt), M2 XGB (mkt, cal), M3 XGB
  (+ wpsr, cot, fred), M4 XGB (+ news). La prueba principal es M3 frente a M4; el resto es
  descriptivo.
- Predicciones OOS en ``data/runs/f7/`` (fuera de git; sha256 del CSV en el informe).

Salida: ``reports/f7/h1_primary.json`` y filas en ``runs/registry.csv`` (también si falla).
Uso: uv run python -I scripts/f7/h1_primary.py
"""

import csv
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl
import yaml

from wti_news_alpha.config import (
    CONFIG_DIR,
    load_features_config,
    load_labels_config,
    load_project_config,
)
from wti_news_alpha.evaluation import sealed_guard as sg
from wti_news_alpha.labels import splits
from wti_news_alpha.models import compare, data, pipeline
from wti_news_alpha.runlog import REPO_ROOT, current_commit

OUT = REPO_ROOT / "reports" / "f7"
PRED_DIR = REPO_ROOT / "data" / "runs" / "f7"
REGISTRY = REPO_ROOT / "runs" / "registry.csv"
PREREG = "preregistration/H1.yaml"
SEED = 20261017  # bootstrap (H1.yaml: "1.000, semilla 20261017")
N_BOOT = 1000
ALPHA = 0.05
FOLD_SHARE = 0.6
FIRST_TEST = datetime(2019, 1, 1, tzinfo=UTC)
BASE = ("mkt", "cal")
FUND = ("mkt", "cal", "wpsr", "cot", "fred")
SPECS = (
    pipeline.ModelSpec("M1", ("mkt",), learner="logit"),
    pipeline.ModelSpec("M2", BASE),
    pipeline.ModelSpec("M3", FUND),
    pipeline.ModelSpec("M4", (*FUND, "news")),
)
LADDER = (("M0", "M1"), ("M0", "M2"), ("M2", "M3"), ("M3", "M4"))


def load() -> tuple[data.ResearchFrame, datetime, sg.SealedGuard]:
    proj = load_project_config(CONFIG_DIR)
    win = proj.splits.sealed_test_window.require("sealed_test_window")
    guard = sg.SealedGuard(
        sg.Window(win.start, win.end),
        repo=REPO_ROOT,
        prereg=PREREG,
        log=REPO_ROOT / "runs" / "sealed_access.jsonl",
    )
    fv = load_features_config(CONFIG_DIR).version
    lv = load_labels_config(CONFIG_DIR).version
    rf = data.load_research_frame(
        features_dir=REPO_ROOT / "data" / "features" / fv,
        labels_dir=REPO_ROOT / "data" / "labels" / lv,
        guard=guard,
    )
    return rf, win.start, guard


def registry_row(  # noqa: PLR0913 - una fila del registro, todo explícito
    run_id: str,
    *,
    model: str,
    h: int,
    commit: str,
    params: dict[str, Any],
    by_fold: str,
    status: str,
    notes: str,
) -> dict[str, str]:
    return {
        "run_id": run_id,
        "timestamp_utc": datetime.now(tz=UTC).isoformat(),
        "commit": commit,
        "hypothesis_id": "H1",
        "phase": "F7",
        "model_id": model,
        "data_version": "dwx-xti-m1-v3/events-v3",
        "feature_version": "feat-v1",
        "label_version": "label-v1",
        "split_id": f"wf-quarter-from-2019Q1-embargo1d-h{h}",
        "hyperparams_json": json.dumps(params, sort_keys=True),
        "seed": str(params.get("random_state", "")),
        "metrics_by_fold_json": by_fold,
        "status": status,
        "notes": notes,
    }


def fold_losses(preds: pl.DataFrame) -> str:
    by = preds.group_by("fold").agg(pl.col("loss").mean()).sort("fold")
    return json.dumps({str(f): round(v, 6) for f, v in by.iter_rows()})


def pred_sha(preds: pl.DataFrame) -> str:
    return hashlib.sha256(preds.sort("decision_time").write_csv().encode()).hexdigest()


def run_horizon(  # noqa: PLR0913 - entradas de la corrida explícitas
    df: pl.DataFrame,
    *,
    allowed: frozenset[str],
    sealed_start: datetime,
    h: int,
    commit: str,
    rows: list[dict[str, str]],
) -> dict[str, Any]:
    folds = splits.walk_forward(
        pipeline.purge_frame(df, h),
        first_test=FIRST_TEST,
        end=sealed_start,
        step="quarter",
        embargo=timedelta(days=1),
        sealed_start=sealed_start,
        horizon=f"{h}m",
    )
    preds = {"M0": pipeline.climatology_loss(df, folds, horizon_min=h)}
    skipped: dict[str, list[int]] = {}
    imputed: dict[str, dict[int, tuple[int, int]]] = {}
    for spec in SPECS:
        res = pipeline.run_walk_forward(
            df, spec, folds, horizon_min=h, allowed=allowed, sealed_start=sealed_start
        )
        preds[spec.name] = res.predictions
        skipped[spec.name] = res.skipped
        if res.imputed:
            imputed[spec.name] = res.imputed
    keys = preds["M0"].select("decision_time", "fold").sort("decision_time")
    for name, p in preds.items():
        if not p.select("decision_time", "fold").sort("decision_time").equals(keys):
            raise RuntimeError(f"h{h}: {name} no tiene las mismas filas OOS que M0")
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    shas = {}
    for name, p in preds.items():
        p.write_parquet(PRED_DIR / f"{name}_h{h}.parquet")
        shas[name] = pred_sha(p)
        params = {} if name == "M0" else next(s.params for s in SPECS if s.name == name)
        rows.append(
            registry_row(
                f"f7-{name}-h{h}",
                model=name,
                h=h,
                commit=commit,
                params=params,
                by_fold=fold_losses(p),
                status="ok",
                notes=f"prueba principal H1 (M3 vs M4); OOS sha256 {shas[name][:16]}",
            )
        )
    j = keys
    for name, p in preds.items():
        j = j.join(
            p.select("decision_time", pl.col("loss").alias(name)),
            on="decision_time",
            how="left",
            validate="1:1",
        )
    j = j.with_columns(day=pl.col("decision_time").dt.strftime("%Y-%m-%d"))
    days = j["day"].to_list()
    ladder = {}
    for a, b in LADDER:
        c = compare.compare_losses(j[a].to_list(), j[b].to_list(), days, n_boot=N_BOOT, seed=SEED)
        ladder[f"{a}_vs_{b}"] = {
            "mean_diff_a_minus_b": c.mean_diff,
            "ci95": [c.lo, c.hi],
            "p_dm": c.p_dm,
            "p_bootstrap": c.p_bootstrap,
            "dm_stat": c.dm_stat,
            "n_days": c.n_blocks,
            "n_obs": c.n_obs,
        }
    daily = (
        j.with_columns(d=pl.col("M3") - pl.col("M4"))
        .group_by("day")
        .agg(pl.col("d").mean(), pl.col("fold").first(), pl.col("fold").n_unique().alias("nf"))
        .sort("day")
    )
    if daily["nf"].max() != 1:
        raise RuntimeError(f"h{h}: un día aparece en más de un fold de test")
    dl, groups = daily["d"].to_list(), [str(f) for f in daily["fold"].to_list()]
    by_fold = compare.group_means(dl, groups)
    loo = compare.leave_one_group_out(dl, groups)
    return {
        "n_folds": len(folds),
        "skipped_folds": skipped,
        "imputed_cells_M1": {str(k): v for k, v in imputed.get("M1", {}).items()},
        "n_test_rows": j.height,
        "n_days": len(dl),
        "loss": {name: round(float(j[name].mean()), 6) for name in preds},  # type: ignore[arg-type]
        "loss_by_fold": {name: json.loads(fold_losses(p)) for name, p in preds.items()},
        "oos_sha256": shas,
        "ladder": ladder,
        "primary": {
            **ladder["M3_vs_M4"],
            "d_by_fold": by_fold,
            "share_folds_d_positive": sum(v > 0 for v in by_fold.values()) / len(by_fold),
            "leave_one_fold_out_mean_d": loo,
        },
    }


def verdict(res: dict[int, dict[str, Any]], holm_p: dict[str, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for h, r in res.items():
        p = r["primary"]
        crit = {
            "1_holm_p_dm_lt_0.05": holm_p[f"h{h}"] < ALPHA,
            "2_mean_d_pos_and_ci_excludes_0": p["mean_diff_a_minus_b"] > 0 and p["ci95"][0] > 0,
            "3_d_pos_in_60pct_folds": p["share_folds_d_positive"] >= FOLD_SHARE,
            "4_leave_one_fold_out_all_pos": all(
                v > 0 for v in p["leave_one_fold_out_mean_d"].values()
            ),
        }
        out[f"h{h}"] = {"holm_p_dm": holm_p[f"h{h}"], "criteria": crit, "pass": all(crit.values())}
    out["H1_supported"] = any(v["pass"] for v in out.values() if isinstance(v, dict))
    return out


def main() -> None:
    commit = current_commit()
    if commit.endswith("-dirty"):
        sys.exit("árbol con cambios sin commitear: la corrida no sería atribuible")
    rf, sealed_start, guard = load()
    prereg_sha = guard.require_approved_prereg()
    prereg = yaml.safe_load((REPO_ROOT / PREREG).read_text(encoding="utf-8"))
    horizons = [int(h) for h in prereg["primary"]["horizons_min"]]
    rows: list[dict[str, str]] = []
    try:
        res = {
            h: run_horizon(
                rf.df, allowed=rf.allowed, sealed_start=sealed_start, h=h, commit=commit, rows=rows
            )
            for h in horizons
        }
    except Exception as e:
        rows.append(
            registry_row(
                "f7-failed",
                model="-",
                h=0,
                commit=commit,
                params={},
                by_fold="{}",
                status="failed",
                notes=repr(e)[:300],
            )
        )
        raise
    finally:
        with REGISTRY.open("a", newline="", encoding="utf-8") as fh:
            if rows:
                csv.DictWriter(fh, fieldnames=list(rows[0])).writerows(rows)
    holm_p = compare.holm({f"h{h}": res[h]["primary"]["p_dm"] for h in horizons})
    report = {
        "commit": commit,
        "prereg": PREREG,
        "prereg_sha256": prereg_sha,
        "bootstrap": {"n_boot": N_BOOT, "seed": SEED},
        "n_rows": rf.df.height,
        "horizons": {f"h{h}": res[h] for h in horizons},
        "verdict": verdict(res, holm_p),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "h1_primary.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report["verdict"], indent=1))
    for h in horizons:
        print(h, res[h]["loss"], {k: round(v["p_dm"], 4) for k, v in res[h]["ladder"].items()})


if __name__ == "__main__":
    main()

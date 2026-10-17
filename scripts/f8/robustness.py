"""F8: robustez de la prueba H1 y análisis secundarios pre-registrados (``H1.yaml``, secondary).

- Usa las predicciones OOS **oficiales** de F7 (``data/runs/f7/``); su sha256 debe coincidir con
  ``reports/f7/h1_primary.json``.
- Subgrupos (exploratorios, BH conjunta sobre los dos horizontes): evento nuevo por familia en
  la última hora, con/sin noticias, fuente de noticias caída, tercil de volatilidad previa,
  tramo de sesión (ET), año, época de titulares (slug hasta 2019-08 / título), y lado según el
  resultado ``y`` (descriptivo: condiciona en el resultado).
- Brier (calibración) M3 frente a M4; frecuencias medias predichas frente a observadas.
- Magnitud ``|ret_h|`` (error cuadrático) M3 frente a M4, mismos folds; BH aparte (2 pruebas).
- SHAP de M4 en el último fold (asociación, no causa): parte de |SHAP| por grupo.

Salida: ``reports/f8/robustness.json`` y filas de magnitud en ``runs/registry.csv``.
Uso: uv run python -I scripts/f8/robustness.py
"""

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import polars as pl
import xgboost as xgb

from wti_news_alpha.config import (
    CONFIG_DIR,
    load_features_config,
    load_labels_config,
    load_project_config,
)
from wti_news_alpha.eda.event_study import bh_qvalues
from wti_news_alpha.evaluation import sealed_guard as sg
from wti_news_alpha.labels import labels, splits
from wti_news_alpha.models import compare, data, magnitude, pipeline, robustness
from wti_news_alpha.runlog import REPO_ROOT, current_commit

OUT = REPO_ROOT / "reports" / "f8"
PRED_DIR = REPO_ROOT / "data" / "runs" / "f7"
F7_REPORT = REPO_ROOT / "reports" / "f7" / "h1_primary.json"
REGISTRY = REPO_ROOT / "runs" / "registry.csv"
SEED = 20261017
N_BOOT = 1000
FIRST_TEST = datetime(2019, 1, 1, tzinfo=UTC)
FAMILIES = ("conflict_attack", "eia_inventories", "opec_policy", "sanctions_geopolitics", "weather")
FUND = ("mkt", "cal", "wpsr", "cot", "fred")
M3 = pipeline.ModelSpec("M3", FUND)
M4 = pipeline.ModelSpec("M4", (*FUND, "news"))
SLUG_ERA_END = datetime(2019, 9, 1, tzinfo=UTC)  # ADR-032: sin títulos de 2018-01 a 2019-08


def load() -> tuple[data.ResearchFrame, datetime, sg.SealedGuard]:
    proj = load_project_config(CONFIG_DIR)
    win = proj.splits.sealed_test_window.require("sealed_test_window")
    guard = sg.SealedGuard(
        sg.Window(win.start, win.end),
        repo=REPO_ROOT,
        prereg="preregistration/H1.yaml",
        log=REPO_ROOT / "runs" / "sealed_access.jsonl",
    )
    rf = data.load_research_frame(
        features_dir=REPO_ROOT / "data" / "features" / load_features_config(CONFIG_DIR).version,
        labels_dir=REPO_ROOT / "data" / "labels" / load_labels_config(CONFIG_DIR).version,
        guard=guard,
    )
    return rf, win.start, guard


def official_preds(h: int, f7: dict[str, Any]) -> dict[str, pl.DataFrame]:
    out = {}
    for name in ("M3", "M4"):
        p = pl.read_parquet(PRED_DIR / f"{name}_h{h}.parquet")
        sha = hashlib.sha256(p.sort("decision_time").write_csv().encode()).hexdigest()
        if sha != f7["horizons"][f"h{h}"]["oos_sha256"][name]:
            raise RuntimeError(f"{name} h{h}: predicciones distintas de las oficiales de F7")
        out[name] = p
    return out


def subgroups(rv_cuts: tuple[float, float]) -> dict[str, pl.Expr]:
    hour = pl.col("cal_hour_et")
    lo, hi = rv_cuts
    g: dict[str, pl.Expr] = {
        f"evento_nuevo_60m:{f}": pl.col(f"news_new_{f}_60m") > 0 for f in FAMILIES
    }
    g |= {
        "con_noticias_nuevas_60m": pl.col("news_new_all_60m") > 0,
        "sin_noticias_60m": pl.col("news_n_all_60m") == 0,
        "fuente_noticias_caida": ~pl.col("news_source_ok"),
        "vol_baja": pl.col("mkt_rv_60m") <= lo,
        "vol_media": (pl.col("mkt_rv_60m") > lo) & (pl.col("mkt_rv_60m") <= hi),
        "vol_alta": pl.col("mkt_rv_60m") > hi,
        "sesion_asia_18_02": (hour >= 18) | (hour < 2),
        "sesion_europa_02_08": (hour >= 2) & (hour < 8),
        "sesion_eeuu_08_12": (hour >= 8) & (hour < 12),
        "sesion_eeuu_12_17": (hour >= 12) & (hour < 17),
        "epoca_slug_hasta_2019_08": pl.col("decision_time") < SLUG_ERA_END,
        "epoca_titulo": pl.col("decision_time") >= SLUG_ERA_END,
        "resultado_sube_(descriptivo)": pl.col("y") == 1,
        "resultado_baja_(descriptivo)": pl.col("y") == -1,
        "resultado_neutral_(descriptivo)": pl.col("y") == 0,
    }
    for y in (2019, 2020, 2024, 2025):
        g[f"año_{y}"] = pl.col("decision_time").dt.year() == y
    return g


def folds_for(df: pl.DataFrame, h: int, sealed_start: datetime) -> list[splits.Fold]:
    return splits.walk_forward(
        pipeline.purge_frame(df, h),
        first_test=FIRST_TEST,
        end=sealed_start,
        step="quarter",
        embargo=timedelta(days=1),
        sealed_start=sealed_start,
        horizon=f"{h}m",
    )


def shap_last_fold(
    df: pl.DataFrame, folds: list[splits.Fold], h: int, allowed: frozenset[str]
) -> dict[str, Any]:
    """Reajusta M4 en el último fold (igual que ``run_walk_forward``) y resume SHAP en su test."""
    f = folds[-1]
    cols = pipeline.feature_columns(df, M4, allowed)
    d = pipeline.add_z(df, h)
    tr = d.filter(f.train & pl.col("_z").is_not_null())
    te = d.filter(f.test & pl.col("_z").is_not_null())
    tau = labels.fit_threshold(tr["_z"], neutral_share=1 / 3)
    ytr = labels.three_class(tr["_z"], tau).to_numpy() + 1
    model = xgb.XGBClassifier(objective="multi:softprob", num_class=3, **M4.params)
    model.fit(tr.select(cols).to_numpy(), ytr)
    contribs = model.get_booster().predict(
        xgb.DMatrix(te.select(cols).to_numpy()), pred_contribs=True
    )
    per_col = np.abs(contribs[..., :-1]).reshape(-1, len(cols)).mean(axis=0)
    top_news = sorted(
        ((c, float(v)) for c, v in zip(cols, per_col, strict=True) if c.startswith("news_")),
        key=lambda t: -t[1],
    )[:5]
    return {
        "fold": f.fold_id,
        "n_test_rows": te.height,
        "share_by_group": robustness.shap_shares(contribs, cols),
        "top_news_columns_mean_abs": top_news,
    }


def main() -> None:
    commit = current_commit()
    if commit.endswith("-dirty"):
        raise SystemExit("árbol con cambios sin commitear: la corrida no sería atribuible")
    rf, sealed_start, guard = load()
    prereg_sha = guard.require_approved_prereg()
    f7 = json.loads(F7_REPORT.read_text(encoding="utf-8"))
    feats = rf.df.select(
        "decision_time",
        "mkt_rv_60m",
        "cal_hour_et",
        "news_source_ok",
        "news_n_all_60m",
        "news_new_all_60m",
        *[f"news_new_{f}_60m" for f in FAMILIES],
    )
    q = rf.df["mkt_rv_60m"].quantile(1 / 3), rf.df["mkt_rv_60m"].quantile(2 / 3)
    rv_cuts = (float(q[0]), float(q[1]))  # type: ignore[arg-type]
    groups = subgroups(rv_cuts)
    report: dict[str, Any] = {
        "commit": commit,
        "prereg_sha256": prereg_sha,
        "f7_commit": f7["commit"],
        "rv_tercile_cuts": rv_cuts,
        "bootstrap": {"kind": "bloques móviles de días (ADR-043)", "n_boot": N_BOOT, "seed": SEED},
    }
    rows: list[dict[str, str]] = []
    mag_p: dict[str, float] = {}
    for h in (15, 60):
        p = official_preds(h, f7)
        j = (
            p["M3"]
            .select(
                "decision_time",
                "fold",
                "y",
                pl.col("loss").alias("M3"),
                pl.concat_list("p_down", "p_neutral", "p_up").alias("p3"),
            )
            .join(
                p["M4"].select(
                    "decision_time",
                    pl.col("loss").alias("M4"),
                    pl.concat_list("p_down", "p_neutral", "p_up").alias("p4"),
                ),
                on="decision_time",
                how="inner",
                validate="1:1",
            )
            .join(feats, on="decision_time", how="left", validate="1:1")
        )
        if j.height != p["M3"].height:
            raise RuntimeError(f"h{h}: M3 y M4 no tienen las mismas filas")
        sub = robustness.subgroup_tests(j, groups, "M3", "M4", n_boot=N_BOOT, seed=SEED)
        y = j["y"].to_numpy()
        p3, p4 = np.vstack(j["p3"].to_list()), np.vstack(j["p4"].to_list())
        b3, b4 = robustness.brier_rows(p3, y), robustness.brier_rows(p4, y)
        days = j["decision_time"].dt.strftime("%Y-%m-%d").to_list()
        cb = compare.compare_losses(b3.tolist(), b4.tolist(), days, n_boot=N_BOOT, seed=SEED)
        calib = {
            name: {
                "mean_pred": dict(
                    zip(("down", "neutral", "up"), pr.mean(axis=0).round(4).tolist(), strict=True)
                ),
                "observed": {
                    k: round(float((y == v).mean()), 4)
                    for k, v in (("down", -1), ("neutral", 0), ("up", 1))
                },
            }
            for name, pr in (("M3", p3), ("M4", p4))
        }
        folds = folds_for(rf.df, h, sealed_start)
        kw = {"horizon_min": h, "allowed": rf.allowed, "sealed_start": sealed_start}
        m3 = magnitude.run_magnitude(rf.df, M3, folds, **kw)  # type: ignore[arg-type]
        m4 = magnitude.run_magnitude(rf.df, M4, folds, **kw)  # type: ignore[arg-type]
        jm = m3.select("decision_time", pl.col("loss").alias("a")).join(
            m4.select("decision_time", pl.col("loss").alias("b")),
            on="decision_time",
            validate="1:1",
        )
        cm = compare.compare_losses(
            jm["a"].to_list(),
            jm["b"].to_list(),
            jm["decision_time"].dt.strftime("%Y-%m-%d").to_list(),
            n_boot=N_BOOT,
            seed=SEED,
        )
        mag_p[f"h{h}"] = cm.p_dm
        for name, m in (("M3", m3), ("M4", m4)):
            by = m.group_by("fold").agg(pl.col("loss").mean()).sort("fold")
            rows.append(
                {
                    "run_id": f"f8-mag-{name}-h{h}-{datetime.now(tz=UTC):%Y%m%dT%H%M%SZ}",
                    "timestamp_utc": datetime.now(tz=UTC).isoformat(),
                    "commit": commit,
                    "hypothesis_id": "H1-secundaria-magnitud",
                    "phase": "F8",
                    "model_id": f"{name}-mag",
                    "data_version": "dwx-xti-m1-v3/events-v3",
                    "feature_version": "feat-v1",
                    "label_version": "label-v1",
                    "split_id": f"wf-quarter-from-2019Q1-embargo1d-h{h}",
                    "hyperparams_json": json.dumps(M3.params, sort_keys=True),
                    "seed": "7",
                    "metrics_by_fold_json": json.dumps(
                        {str(f): round(v, 4) for f, v in by.iter_rows()}
                    ),
                    "status": "ok",
                    "notes": "|ret_h| en pb, error cuadrático",
                }
            )
        report[f"h{h}"] = {
            "subgroups": sub,
            "brier": {
                "M3": float(b3.mean()),
                "M4": float(b4.mean()),
                "mean_diff_M3_minus_M4": cb.mean_diff,
                "ci95": [cb.lo, cb.hi],
                "p_dm": cb.p_dm,
            },
            "calibration": calib,
            "magnitude": {
                "mse_bps2_M3": float(jm["a"].mean()),  # type: ignore[arg-type]
                "mse_bps2_M4": float(jm["b"].mean()),  # type: ignore[arg-type]
                "mean_diff_M3_minus_M4": cm.mean_diff,
                "ci95": [cm.lo, cm.hi],
                "p_dm": cm.p_dm,
                "n_days": cm.n_blocks,
            },
            "shap_M4_last_fold": shap_last_fold(rf.df, folds, h, rf.allowed),
        }
    tested = [r for h in (15, 60) for r in report[f"h{h}"]["subgroups"] if r["status"] == "ok"]
    for r, qv in zip(tested, bh_qvalues([r["p_dm"] for r in tested]), strict=True):
        r["q_bh_joint"] = qv
    report["n_subgroup_tests"] = len(tested)
    report["magnitude_bh"] = dict(zip(mag_p, bh_qvalues(list(mag_p.values())), strict=True))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "robustness.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    with REGISTRY.open("a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=list(rows[0])).writerows(rows)
    for h in (15, 60):
        r = report[f"h{h}"]
        print(
            h,
            "brier",
            r["brier"],
            "mag",
            r["magnitude"],
            "shap",
            r["shap_M4_last_fold"]["share_by_group"],
        )
        for s in r["subgroups"]:
            if s["status"] == "ok":
                print(
                    f"  {s['subgroup']:38s} d={s['mean_diff_a_minus_b']:+.5f} "
                    f"p={s['p_dm']:.3f} q={s['q_bh_joint']:.3f} días={s['n_days']}"
                )
            else:
                print(f"  {s['subgroup']:38s} {s['status']}")


if __name__ == "__main__":
    main()

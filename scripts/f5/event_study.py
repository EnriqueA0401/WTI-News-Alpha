"""F5: estudio de eventos en el periodo de investigación (FASE_05.md, ADR-036/037).

- Eventos de ``events-v3`` (5 familias) con t0 = ``event_first_available_at``.
- Solo el periodo de investigación: t0 < inicio del sellado - 7 días (la ventana más larga
  son 3 sesiones) y barras con ``available_at`` < inicio del sellado. ``check_research_only``
  falla si algo toca el sellado.
- Referencia: los instantes de la rejilla de ``feat-v1`` (mismas medidas), emparejados por
  día de la semana y hora ET y año, sin evento de la misma familia en ±4 h.
- Placebo: ``N_PLACEBO`` sorteos de un instante emparejado por evento → mediana de |retorno|.
- Intervalos: bootstrap por bloques de un día (UTC), semilla fija.

Salidas (sin titulares): ``reports/f5/*.csv`` y ``reports/f5/summary.json``. Imprime un resumen.
Uso: uv run python -I scripts/f5/event_study.py
"""

import json
import random
import statistics
from datetime import timedelta

import polars as pl

from wti_news_alpha.config import CONFIG_DIR, load_features_config, load_project_config
from wti_news_alpha.eda import event_study as es
from wti_news_alpha.features import market
from wti_news_alpha.runlog import REPO_ROOT, current_commit

OUT = REPO_ROOT / "reports" / "f5"
NORM = REPO_ROOT / "data" / "normalized"
SEED = 20261017
N_PLACEBO = 500
N_BOOT = 1000
EXCLUSION = timedelta(hours=4)
HORIZONS = [5, 15, 30, 60]
SESSIONS = [1, 3]
MEASURES = [f"ret_{h}m" for h in HORIZONS] + ["ret_session_end"] + [f"ret_{k}s" for k in SESSIONS]


def load() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, dict[str, object]]:
    proj = load_project_config(CONFIG_DIR)
    fcfg = load_features_config(CONFIG_DIR)
    sealed = proj.splits.sealed_test_window.require("sealed_test_window").start
    cutoff = sealed - timedelta(days=7)
    latency = timedelta(seconds=proj.costs.latency_seconds.require("latency_seconds"))
    periods = fcfg.periods.require("periods")
    in_research = pl.any_horizontal(
        pl.col("t0").is_between(p.start, min(p.end, cutoff), closed="left") for p in periods
    )
    ev = (
        pl.read_parquet(NORM / "events" / fcfg.inputs.events / "events.parquet")
        .rename({"event_first_available_at": "t0"})
        .filter(in_research)
        .select("event_id", "t0", "family", "country", "n_sources_final")
    )
    price = NORM / "darwinex_xti_m1" / fcfg.inputs.price
    bars = pl.concat(
        [pl.read_parquet(p, columns=list(market.REQUIRED)) for p in sorted(price.glob("*.parquet"))]
    ).filter(pl.col("available_at") < sealed)
    grid = (
        pl.scan_parquet(REPO_ROOT / "data" / "features" / fcfg.version / "*.parquet")
        .select(pl.col("decision_time").alias("t0"), "mkt_rv_60m")
        .filter(pl.col("t0") < cutoff)
        .collect()
        .with_columns(event_id=pl.col("t0").dt.strftime("g%Y%m%d%H%M"))
    )
    meta = {"sealed_start": sealed, "cutoff": str(cutoff), "latency": latency}
    return ev, bars, grid, meta


def keyed(df: pl.DataFrame) -> pl.DataFrame:
    local = pl.col("t0").dt.convert_time_zone("America/New_York")
    return df.with_columns(
        k_wd=local.dt.weekday(), k_hour=local.dt.hour(), k_year=pl.col("t0").dt.year()
    )


def free_of(grid: pl.DataFrame, ev_times: pl.DataFrame) -> pl.Series:
    """Instantes de la rejilla sin evento (de esa familia) en ±``EXCLUSION``."""
    e = ev_times.select(pl.col("t0").alias("_e")).sort("_e")
    g = grid.select("t0").sort("t0")
    back = g.join_asof(e, left_on="t0", right_on="_e", strategy="backward")["_e"]
    fwd = g.join_asof(e, left_on="t0", right_on="_e", strategy="forward")["_e"]
    near_b = (g["t0"] - back) <= EXCLUSION
    near_f = (fwd - g["t0"]) <= EXCLUSION
    return ~(near_b.fill_null(False) | near_f.fill_null(False))


def bh_qvalues(pvals: list[float]) -> list[float]:
    """Benjamini-Hochberg: q-valores en el orden de entrada (CLAUDE.md §11.4)."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    q = [0.0] * m
    running = 1.0
    for rank, i in reversed(list(enumerate(order, start=1))):
        running = min(running, pvals[i] * m / rank)
        q[i] = running
    return q


def vol_regime(col: str, q1: float, q2: float) -> pl.Expr:
    c = pl.col(col)
    return (
        pl.when(c <= q1)
        .then(pl.lit("low"))
        .when(c <= q2)
        .then(pl.lit("mid"))
        .when(c.is_not_null())
        .then(pl.lit("high"))
    )


def main() -> None:
    commit = current_commit()
    ev, bars, grid, meta = load()
    p = es.StudyParams(meta["latency"], timedelta(minutes=5), HORIZONS, SESSIONS, 60)  # type: ignore[arg-type]
    er = es.event_returns(ev.select("event_id", "t0"), bars, p)
    er = er.join(ev.drop("t0"), on="event_id", how="left", validate="1:1")
    check = er.with_columns(window_end=pl.col("t0") + timedelta(days=7))
    es.check_research_only(check, sealed_start=meta["sealed_start"])  # type: ignore[arg-type]
    pb = es.StudyParams(p.latency, p.max_gap, HORIZONS, SESSIONS, 5)
    base = es.event_returns(grid.select("event_id", "t0"), bars, pb)
    base = keyed(base.join(grid.select("event_id", "mkt_rv_60m"), on="event_id", how="left"))
    er = keyed(er).with_columns(day=pl.col("t0").dt.date().cast(pl.Utf8))
    # Régimen de volatilidad previo: terciles de la RV de 60 min de la rejilla de 2018-2020.
    rv = grid.filter(pl.col("t0").dt.year() <= 2020)["mkt_rv_60m"].drop_nulls()
    q1, q2 = float(rv.quantile(1 / 3) or 0.0), float(rv.quantile(2 / 3) or 0.0)
    if not 0 < q1 < q2:
        raise ValueError(f"terciles de volatilidad no válidos: {q1}, {q2}")
    er = er.join_asof(
        grid.select(pl.col("t0").alias("_g"), pl.col("mkt_rv_60m").alias("rv_pre")).sort("_g"),
        left_on="t0",
        right_on="_g",
        strategy="backward",
    ).with_columns(
        vol_regime=vol_regime("rv_pre", q1, q2),
        epoch=pl.when(pl.col("t0") < pl.datetime(2019, 9, 1, time_zone="UTC"))
        .then(pl.lit("slug"))
        .otherwise(pl.lit("title")),
    )
    base = base.with_columns(vol_regime=vol_regime("mkt_rv_60m", q1, q2))
    variants = {
        "hour": ["k_wd", "k_hour", "k_year"],
        "hour_vol": ["k_wd", "k_hour", "k_year", "vol_regime"],
    }
    rows = []
    for variant, keys in variants.items():
        rows += placebo_rows(er, base, grid, keys, variant)
    summary = pl.DataFrame(rows)
    summary = pl.concat(
        [
            g.with_columns(
                q_bh=pl.Series([round(x, 4) for x in bh_qvalues(g["p_placebo"].to_list())])
            )
            for _, g in summary.group_by("variant", maintain_order=True)
        ]
    )
    OUT.mkdir(parents=True, exist_ok=True)
    summary.write_csv(OUT / "event_study_summary.csv")
    write_segments(er)
    write_tables(er)
    info = {
        "sealed_start": str(meta["sealed_start"]),
        "cutoff": meta["cutoff"],
        "latency_s": meta["latency"].total_seconds(),  # type: ignore[attr-defined]
        "commit": commit,
        "n_events_research": er.height,
        "n_events_with_entry": int(er["entry_at"].is_not_null().sum()),
        "n_baseline_instants": base.height,
        "vol_terciles_2018_2020": [q1, q2],
        "seed": SEED,
        "n_placebo": N_PLACEBO,
        "n_boot": N_BOOT,
        "exclusion_hours": EXCLUSION.total_seconds() / 3600,
        "n_tests_per_variant": summary.height // len(variants),
    }
    (OUT / "summary.json").write_text(json.dumps(info, indent=2, default=str) + "\n")
    print(json.dumps(info, indent=1, default=str))
    with pl.Config(tbl_rows=80, tbl_cols=12, tbl_width_chars=220):
        print(
            summary.select(
                "variant",
                "family",
                "measure",
                "n_events",
                "matched_share",
                "med_abs_bp",
                "placebo_med_bp",
                "placebo_hi_bp",
                "ratio_vs_placebo",
                "p_placebo",
                "q_bh",
            )
        )


def placebo_rows(
    er: pl.DataFrame, base: pl.DataFrame, grid: pl.DataFrame, keys: list[str], variant: str
) -> list[dict[str, object]]:
    """Por familia y medida: mediana de |retorno| frente a placebos emparejados por ``keys``."""
    rng = random.Random(SEED)  # noqa: S311 (placebo, no criptografía)
    rows: list[dict[str, object]] = []
    for fam in sorted(er["family"].unique()):
        fe = er.filter(pl.col("family") == fam)
        pool = base.filter(free_of(grid, fe))
        for m in MEASURES:
            e = fe.filter(pl.col(m).is_not_null())
            if e.height < 10:
                continue
            absr = e[m].abs().to_list()
            ci = es.block_bootstrap_ci(absr, e["day"].to_list(), n_boot=N_BOOT, seed=SEED)
            sg = es.block_bootstrap_ci(e[m].to_list(), e["day"].to_list(), n_boot=N_BOOT, seed=SEED)
            pools = {
                k: v[m].abs().to_list()
                for k, v in pool.filter(pl.col(m).is_not_null())
                .partition_by(keys, as_dict=True)
                .items()
            }
            need = e.group_by(keys).len()
            plan: list[tuple[list[float], int]] = []
            for r in need.iter_rows():
                vals = pools.get(tuple(r[: len(keys)]))
                if vals:
                    plan.append((vals, r[len(keys)]))
            meds = []
            for _ in range(N_PLACEBO):
                draw = [x for vals, n in plan for x in rng.choices(vals, k=n)]
                meds.append(statistics.median(draw))
            meds.sort()
            p_val = (1 + sum(x >= ci.point for x in meds)) / (N_PLACEBO + 1)
            rows.append(
                {
                    "variant": variant,
                    "family": fam,
                    "measure": m,
                    "n_events": ci.n,
                    "n_days": ci.n_blocks,
                    "matched_share": round(sum(n for _, n in plan) / e.height, 3),
                    "med_abs_bp": round(1e4 * ci.point, 2),
                    "med_abs_lo_bp": round(1e4 * ci.lo, 2),
                    "med_abs_hi_bp": round(1e4 * ci.hi, 2),
                    "placebo_med_bp": round(1e4 * meds[N_PLACEBO // 2], 2),
                    "placebo_lo_bp": round(1e4 * meds[int(0.025 * N_PLACEBO)], 2),
                    "placebo_hi_bp": round(1e4 * meds[int(0.975 * N_PLACEBO) - 1], 2),
                    "ratio_vs_placebo": round(ci.point / meds[N_PLACEBO // 2], 3),
                    "p_placebo": round(p_val, 4),
                    "med_signed_bp": round(1e4 * sg.point, 2),
                    "med_signed_lo_bp": round(1e4 * sg.lo, 2),
                    "med_signed_hi_bp": round(1e4 * sg.hi, 2),
                }
            )
    return rows


def write_segments(er: pl.DataFrame) -> None:
    seg_rows = []
    for seg in ("epoch", "vol_regime", "k_year"):
        for (fam, val), g in er.filter(pl.col("ret_15m").is_not_null()).group_by("family", seg):
            if g.height < 10 or val is None:
                continue
            ci = es.block_bootstrap_ci(
                g["ret_15m"].abs().to_list(), g["day"].to_list(), n_boot=N_BOOT, seed=SEED
            )
            seg_rows.append(
                {
                    "segment": seg,
                    "value": str(val),
                    "family": fam,
                    "n_events": ci.n,
                    "n_days": ci.n_blocks,
                    "med_abs_ret_15m_bp": round(1e4 * ci.point, 2),
                    "lo_bp": round(1e4 * ci.lo, 2),
                    "hi_bp": round(1e4 * ci.hi, 2),
                }
            )
    pl.DataFrame(seg_rows).sort("segment", "family", "value").write_csv(OUT / "segments.csv")


def write_tables(er: pl.DataFrame) -> None:
    cov = er.group_by("k_year", "family").agg(
        n_events=pl.len(),
        with_entry=pl.col("entry_at").is_not_null().sum(),
        with_ret_15m=pl.col("ret_15m").is_not_null().sum(),
    )
    cov.sort("k_year", "family").write_csv(OUT / "coverage.csv")
    extremes = (
        er.filter(pl.col("ret_15m").is_not_null())
        .sort(pl.col("ret_15m").abs(), descending=True)
        .group_by("family", maintain_order=True)
        .head(3)
        .select("family", "event_id", "t0", "country", "n_sources_final", "ret_15m", "ret_60m")
    )
    extremes.write_csv(OUT / "extremes.csv")


if __name__ == "__main__":
    main()

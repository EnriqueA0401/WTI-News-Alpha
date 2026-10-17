"""F5: estudio de eventos en el periodo de investigación (FASE_05.md, ADR-036/037/038).

- Eventos de ``events-v3`` (5 familias) con t0 = ``event_first_available_at``.
- Solo el periodo de investigación: t0 < inicio del sellado - 7 días (la ventana más larga
  son 3 sesiones) y barras con ``available_at`` < inicio del sellado. ``check_research_only``
  falla si algo toca el sellado.
- **Comparación (ADR-038):** cada evento se sitúa en el percentil de su |retorno| entre los
  instantes comparables de la rejilla de ``feat-v1``. Comparable = mismo día de la semana, hora
  y **minuto** ET (los eventos llegan a los cuartos de hora) y año, sin evento de la misma
  familia en ±4 h; en la variante ``time_vol``, además el mismo tercil de volatilidad previa.
  Bajo la hipótesis nula la media de los percentiles es 0,5.
- **Incertidumbre:** bootstrap por bloques de un día (horizontes intradía y fin de sesión) o
  de una semana ISO (1 y 3 sesiones, cuyas ventanas se solapan entre días). Calibrado con
  eventos falsos que conservan la agrupación por días (≤ 5 % de falsos positivos a p ≤ 0,05).
- Determinista: sin sorteos de placebo, bloques y celdas ordenados, semilla fija.

Salidas (sin titulares): ``reports/f5/*.csv`` y ``reports/f5/summary.json``. Imprime un resumen.
Uso: uv run python -I scripts/f5/event_study.py
"""

import hashlib
import json
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
N_BOOT = 1000
EXCLUSION = timedelta(hours=4)
RV_TOLERANCE = timedelta(minutes=10)
HORIZONS = [5, 15, 30, 60]
SESSIONS = [1, 3]
MEASURES = [f"ret_{h}m" for h in HORIZONS] + ["ret_session_end"] + [f"ret_{k}s" for k in SESSIONS]
WEEK_BLOCKS = {f"ret_{k}s" for k in SESSIONS}
VARIANTS = {
    "time": ["k_wd", "k_hour", "k_min", "k_year"],
    "time_vol": ["k_wd", "k_hour", "k_min", "k_year", "vol_regime"],
}


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
        .sort("t0", "event_id")
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
        .sort("t0")
        .with_columns(event_id=pl.col("t0").dt.strftime("g%Y%m%d%H%M"))
    )
    meta = {"sealed_start": sealed, "cutoff": str(cutoff), "latency": latency}
    return ev, bars, grid, meta


def keyed(df: pl.DataFrame) -> pl.DataFrame:
    local = pl.col("t0").dt.convert_time_zone("America/New_York")
    return df.with_columns(
        k_wd=local.dt.weekday(),
        k_hour=local.dt.hour(),
        k_min=local.dt.minute(),
        k_year=pl.col("t0").dt.year(),
        day=pl.col("t0").dt.strftime("%Y-%m-%d"),
        week=pl.col("t0").dt.strftime("%G-%V"),
    )


def event_free(grid: pl.DataFrame, ev_times: pl.DataFrame) -> pl.DataFrame:
    """Instantes ``t0`` de la rejilla sin evento (de esa familia) en ±``EXCLUSION``."""
    e = ev_times.select(pl.col("t0").alias("_e")).sort("_e")
    g = grid.select("t0").sort("t0")
    b = g.join_asof(e, left_on="t0", right_on="_e", strategy="backward").rename({"_e": "_b"})
    f = b.join_asof(e, left_on="t0", right_on="_e", strategy="forward").rename({"_e": "_f"})
    near = ((pl.col("t0") - pl.col("_b")) <= EXCLUSION) | (
        (pl.col("_f") - pl.col("t0")) <= EXCLUSION
    )
    return f.filter(~near.fill_null(False)).select("t0")


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


def percentile_rows(
    er: pl.DataFrame, base: pl.DataFrame, grid: pl.DataFrame, keys: list[str], variant: str
) -> list[dict[str, object]]:
    """Por familia y medida: media del percentil de |retorno| de cada evento en su celda."""
    rows: list[dict[str, object]] = []
    for fam in sorted(er["family"].unique()):
        fe = er.filter(pl.col("family") == fam)
        pool = base.join(event_free(grid, fe), on="t0", how="semi")
        for m in MEASURES:
            pp = pool.filter(pl.col(m).is_not_null()).sort("t0")
            cells = {
                k: sorted(v[m].abs().to_list())
                for k, v in pp.partition_by(keys, as_dict=True, maintain_order=True).items()
            }
            n_all = fe.filter(pl.col(m).is_not_null()).height
            e = fe.filter(
                pl.col(m).is_not_null() & pl.all_horizontal(pl.col(k).is_not_null() for k in keys)
            )
            u: list[float] = []
            blocks: list[str] = []
            ev_abs: list[float] = []
            cell_med: list[float] = []
            for r in e.sort("t0", "event_id").iter_rows(named=True):
                cell = cells.get(tuple(r[k] for k in keys))
                if not cell:
                    continue
                x = abs(r[m])
                u.append(es.cell_percentile(x, cell))
                blocks.append(r["week"] if m in WEEK_BLOCKS else r["day"])
                ev_abs.append(x)
                cell_med.append(statistics.median(cell))
            if len(u) < 30:
                continue
            t = es.percentile_test(u, blocks, n_boot=N_BOOT, seed=SEED)
            rows.append(
                {
                    "variant": variant,
                    "family": fam,
                    "measure": m,
                    "n_events": t.n,
                    "n_blocks": t.n_blocks,
                    "block": "week" if m in WEEK_BLOCKS else "day",
                    "matched_share": round(t.n / n_all, 3),
                    "mean_pct": round(t.mean, 4),
                    "mean_pct_lo": round(t.lo, 4),
                    "mean_pct_hi": round(t.hi, 4),
                    "p_greater": round(t.p_greater, 4),
                    "med_abs_bp": round(1e4 * statistics.median(ev_abs), 2),
                    "med_cell_med_bp": round(1e4 * statistics.median(cell_med), 2),
                }
            )
    return rows


def main() -> None:
    commit = current_commit()
    ev, bars, grid, meta = load()
    latency: timedelta = meta["latency"]  # type: ignore[assignment]
    p = es.StudyParams(latency, timedelta(minutes=5), HORIZONS, SESSIONS, 60)
    er = es.event_returns(ev.select("event_id", "t0"), bars, p)
    er = er.join(ev.drop("t0"), on="event_id", how="left", validate="1:1")
    check = er.with_columns(window_end=pl.col("t0") + timedelta(days=7))
    es.check_research_only(check, sealed_start=meta["sealed_start"])  # type: ignore[arg-type]
    pb = es.StudyParams(p.latency, p.max_gap, HORIZONS, SESSIONS, 5)
    base = es.event_returns(grid.select("event_id", "t0"), bars, pb)
    base = keyed(base.join(grid.select("event_id", "mkt_rv_60m"), on="event_id", how="left"))
    # Régimen de volatilidad previo: terciles de la RV de 60 min de la rejilla de 2018-2020.
    rv = grid.filter(pl.col("t0").dt.year() <= 2020)["mkt_rv_60m"].drop_nulls()
    q1, q2 = float(rv.quantile(1 / 3) or 0.0), float(rv.quantile(2 / 3) or 0.0)
    if not 0 < q1 < q2:
        raise ValueError(f"terciles de volatilidad no válidos: {q1}, {q2}")
    pre = grid.select(pl.col("t0").alias("_g"), pl.col("mkt_rv_60m").alias("rv_pre")).sort("_g")
    er = (
        keyed(er)
        .sort("t0")
        .join_asof(pre, left_on="t0", right_on="_g", strategy="backward", tolerance=RV_TOLERANCE)
        .with_columns(
            vol_regime=vol_regime("rv_pre", q1, q2),
            epoch=pl.when(pl.col("t0") < pl.datetime(2019, 9, 1, time_zone="UTC"))
            .then(pl.lit("slug"))
            .otherwise(pl.lit("title")),
        )
    )
    base = base.with_columns(vol_regime=vol_regime("mkt_rv_60m", q1, q2))
    rows: list[dict[str, object]] = []
    for variant, keys in VARIANTS.items():
        rows += percentile_rows(er, base, grid, keys, variant)
    summary = pl.DataFrame(rows)
    summary = pl.concat(
        [
            g.with_columns(
                q_bh=pl.Series([round(x, 4) for x in es.bh_qvalues(g["p_greater"].to_list())])
            )
            for _, g in summary.group_by("variant", maintain_order=True)
        ]
    )
    OUT.mkdir(parents=True, exist_ok=True)
    summary.write_csv(OUT / "event_study_summary.csv")
    write_segments(er)
    write_tables(er)
    digest = hashlib.sha256((OUT / "event_study_summary.csv").read_bytes()).hexdigest()
    info = {
        "sealed_start": str(meta["sealed_start"]),
        "cutoff": meta["cutoff"],
        "latency_s": latency.total_seconds(),
        "commit": commit,
        "n_events_research": er.height,
        "n_events_with_entry": int(er["entry_at"].is_not_null().sum()),
        "n_baseline_instants": base.height,
        "vol_terciles_2018_2020": [q1, q2],
        "seed": SEED,
        "n_boot": N_BOOT,
        "exclusion_hours": EXCLUSION.total_seconds() / 3600,
        "n_tests_per_variant": summary.height // len(VARIANTS),
        "summary_sha256": digest,
    }
    (OUT / "summary.json").write_text(json.dumps(info, indent=2, default=str) + "\n")
    print(json.dumps(info, indent=1, default=str))
    cols = [
        "variant",
        "family",
        "measure",
        "n_events",
        "matched_share",
        "mean_pct",
        "mean_pct_lo",
        "mean_pct_hi",
        "p_greater",
        "q_bh",
        "med_abs_bp",
        "med_cell_med_bp",
    ]
    with pl.Config(tbl_rows=80, tbl_cols=14, tbl_width_chars=230):
        print(summary.select(cols))


def write_segments(er: pl.DataFrame) -> None:
    seg_rows = []
    for seg in ("epoch", "vol_regime", "k_year"):
        groups = er.filter(pl.col("ret_15m").is_not_null()).group_by("family", seg)
        for (fam, val), g in groups:
            if g.height < 10 or val is None:
                continue
            g = g.sort("t0", "event_id")
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
        .sort(pl.col("ret_15m").abs(), "event_id", descending=[True, False])
        .group_by("family", maintain_order=True)
        .head(3)
        .select("family", "event_id", "t0", "country", "n_sources_final", "ret_15m", "ret_60m")
        .sort("family", "event_id")
    )
    extremes.write_csv(OUT / "extremes.csv")


if __name__ == "__main__":
    main()

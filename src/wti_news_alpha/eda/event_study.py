"""Estudio de eventos: lo que pasa con el precio tras cada evento (FASE_05.md; Documento §7.1).

Entradas: eventos (``event_id``, ``t0`` = primera disponibilidad accionable, UTC) y barras de
1 min (``available_at`` = fin de barra, precios bid).

Invariantes:
- **Entrada:** cierre de la primera barra completa con ``available_at ≥ t0 + latencia``
  (ADR-037). Si tarda más de ``max_gap`` (mercado cerrado o hueco), no hay entrada: nulo.
- **Retorno a k min** (tiempo de mercado): última barra con ``available_at ≤ entrada + k``,
  con ≤ ``max_gap`` de antigüedad y sin ningún corte (hueco > ``max_gap``) entre entrada y
  salida. Si no, nulo (sin rellenar).
- **Fin de sesión:** última barra antes del cierre de las 17:00 ET (ADR-037), sin cortes.
  **k sesiones:** cierre k sesiones después (días hábiles; cruza pausas por definición, así
  que solo se exige que la barra del cierre exista).
- **Trayectoria** (``path_minutes``): máximo high y mínimo low en ``(entrada, entrada + p]``
  respecto a la entrada (log), en las dos direcciones; nula si el retorno a ``p`` lo es.
- Un evento solo usa barras hasta el final de su ventana (test de propiedad).
"""

import bisect
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta

import polars as pl

from wti_news_alpha.errors import SchemaError, SealedTestError
from wti_news_alpha.features import market, pit
from wti_news_alpha.timeutils import require_utc_column

AT = pit.AVAILABLE
NY = "America/New_York"
CLOSE_ET = time(17, 0)


@dataclass(frozen=True)
class StudyParams:
    latency: timedelta
    max_gap: timedelta
    horizons_min: list[int]
    sessions: list[int]
    path_minutes: int


def session_close(entry: pl.Expr, k: int) -> pl.Expr:
    """Cierre (17:00 ET, en UTC) de la sesión de ``entry`` más ``k`` sesiones hábiles.

    Una entrada a partir de las 17:00 ET pertenece a la sesión del día hábil siguiente.
    No usa calendario de festivos: un cierre festivo deja la barra ausente (y el valor nulo).
    """
    local = entry.dt.convert_time_zone(NY)
    # Int32: ``dt.hour()`` es Int8 y ``hora * 60`` se desbordaría.
    minutes = local.dt.hour().cast(pl.Int32) * 60 + local.dt.minute().cast(pl.Int32)
    after = minutes >= CLOSE_ET.hour * 60 + CLOSE_ET.minute
    day = local.dt.date() + pl.duration(days=pl.when(after).then(1).otherwise(0))
    day = day.dt.add_business_days(0, roll="forward").dt.add_business_days(k)
    return day.dt.combine(CLOSE_ET).dt.replace_time_zone(NY).dt.convert_time_zone("UTC")


def _exit(
    ev: pl.DataFrame, b: pl.DataFrame, target: pl.Expr, max_gap: timedelta, name: str
) -> pl.DataFrame:
    """Última barra ≤ ``target`` por evento: cierre, ``available_at`` y último corte."""
    t = ev.select("event_id", target.alias("_target")).filter(pl.col("_target").is_not_null())
    t = t.sort("_target")
    j = t.join_asof(
        b.select(pl.col(AT).alias("_x_at"), pl.col("bid_close").alias("_x_close"), "_break_at"),
        left_on="_target",
        right_on="_x_at",
        strategy="backward",
    )
    fresh = (pl.col("_target") - pl.col("_x_at")) <= max_gap
    return j.select(
        "event_id",
        pl.when(fresh).then(pl.col("_x_close")).alias(f"{name}_close"),
        pl.when(fresh).then(pl.col("_x_at")).alias(f"{name}_at"),
        pl.when(fresh).then(pl.col("_break_at")).alias(f"{name}_break"),
    )


def event_returns(events: pl.DataFrame, bars: pl.DataFrame, p: StudyParams) -> pl.DataFrame:
    """Una fila por evento con entrada, retornos, fin de sesión, k sesiones y trayectoria."""
    for c in ("event_id", "t0"):
        if c not in events.columns:
            raise SchemaError(f"faltan columnas de eventos: {c}")
    require_utc_column(events, "t0")
    if events["event_id"].is_duplicated().any():
        raise SchemaError("event_id duplicado")
    b = market.bar_features(bars, max_gap=p.max_gap, vol_window=p.max_gap)
    want = events.sort("t0").with_columns(_want=pl.col("t0") + p.latency)
    j = want.join_asof(
        b.select(pl.col(AT).alias("_e_at"), pl.col("bid_close").alias("_e_close")),
        left_on="_want",
        right_on="_e_at",
        strategy="forward",
    )
    ok = (pl.col("_e_at") - pl.col("_want")) <= p.max_gap
    out = j.select(
        "event_id",
        "t0",
        pl.when(ok).then(pl.col("_e_at")).alias("entry_at"),
        pl.when(ok).then(pl.col("_e_close")).alias("entry_close"),
    )
    entry = pl.col("entry_at")
    log_ret = (pl.col("_x_close") / pl.col("entry_close")).log()

    def add(target: pl.Expr, name: str, *, no_break: bool) -> None:
        nonlocal out
        x = _exit(out, b, target, p.max_gap, "_x")
        out = out.join(x, on="event_id", how="left", validate="1:1")
        valid = pl.col("_x_at") > entry
        if no_break:
            valid = valid & (pl.col("_x_break") <= entry)
        out = out.with_columns(pl.when(valid).then(log_ret).alias(name)).drop(
            "_x_close", "_x_at", "_x_break"
        )

    for k in sorted({*p.horizons_min, p.path_minutes}):
        add(entry + timedelta(minutes=k), f"ret_{k}m", no_break=True)
    out = out.with_columns(session_close_at=session_close(entry, 0))
    add(pl.col("session_close_at"), "ret_session_end", no_break=True)
    for k in p.sessions:
        add(session_close(entry, k), f"ret_{k}s", no_break=False)
    out = out.join(_path(out, b, p), on="event_id", how="left", validate="1:1")
    keep_extra = f"ret_{p.path_minutes}m" in out.columns and p.path_minutes not in p.horizons_min
    return out.drop(f"ret_{p.path_minutes}m") if keep_extra else out


def _path(out: pl.DataFrame, b: pl.DataFrame, p: StudyParams) -> pl.DataFrame:
    """MFE/MAE en (entrada, entrada + p] con high/low de cada minuto (asof por minuto)."""
    k = p.path_minutes
    valid = out.filter(pl.col(f"ret_{k}m").is_not_null()).select(
        "event_id", "entry_at", "entry_close"
    )
    steps = valid.join(pl.DataFrame({"_k": list(range(1, k + 1))}), how="cross").with_columns(
        _t=pl.col("entry_at") + pl.duration(minutes=pl.col("_k"))
    )
    hl = b.select(pl.col(AT).alias("_b_at"), "bid_high", "bid_low")
    j = steps.sort("_t").join_asof(hl, left_on="_t", right_on="_b_at", strategy="backward")
    j = j.filter(pl.col("_b_at") > pl.col("entry_at"))
    agg = j.group_by("event_id").agg(
        (pl.col("bid_high").max() / pl.col("entry_close").first()).log().alias(f"mfe_up_{k}m"),
        (pl.col("bid_low").min() / pl.col("entry_close").first()).log().alias(f"mae_dn_{k}m"),
    )
    return out.select("event_id").join(agg, on="event_id", how="left", validate="1:1")


@dataclass(frozen=True)
class Interval:
    point: float
    lo: float
    hi: float
    n: int
    n_blocks: int


def block_bootstrap_ci(
    values: list[float], blocks: list[str], *, n_boot: int, seed: int, level: float = 0.95
) -> Interval:
    """Mediana con intervalo por bootstrap de bloques (p. ej. días): se remuestrean bloques
    enteros, así los eventos del mismo bloque no cuentan como independientes."""
    if not values or len(values) != len(blocks):
        raise SchemaError("bootstrap: valores vacíos o desalineados con los bloques")
    groups: dict[str, list[float]] = {}
    for v, g in zip(values, blocks, strict=True):
        groups.setdefault(g, []).append(v)
    keys = sorted(groups)
    rng = random.Random(seed)  # noqa: S311 (bootstrap, no criptografía)

    def median(xs: list[float]) -> float:
        s = sorted(xs)
        m = len(s) // 2
        return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2

    stats = []
    for _ in range(n_boot):
        sample = [v for key in rng.choices(keys, k=len(keys)) for v in groups[key]]
        stats.append(median(sample))
    stats.sort()
    a = (1 - level) / 2
    lo, hi = stats[int(a * n_boot)], stats[min(n_boot - 1, int((1 - a) * n_boot))]
    return Interval(median(values), lo, hi, len(values), len(keys))


def check_research_only(events: pl.DataFrame, *, sealed_start: datetime) -> None:
    """``SealedTestError`` si algún evento o su ventana (``window_end``) entra en el sellado."""
    for c in ("t0", "window_end"):
        require_utc_column(events, c)
    bad = events.filter(
        (pl.col("t0") >= sealed_start) | (pl.col("window_end") >= sealed_start)
    ).height
    if bad:
        raise SealedTestError(f"{bad} eventos tocan el periodo sellado (ADR-036)")


def cell_percentile(value: float, pool_sorted: list[float]) -> float:
    """Percentil (rango medio, empates a la mitad) de ``value`` en ``pool_sorted`` (ascendente).

    Bajo la hipótesis nula (el evento se comporta como sus momentos comparables) es
    ~U(0, 1), con media 0,5, sea cual sea la escala de la celda.
    """
    if not pool_sorted:
        raise SchemaError("percentil: celda de comparación vacía")
    lo = bisect.bisect_left(pool_sorted, value)
    hi = bisect.bisect_right(pool_sorted, value)
    return (lo + hi) / 2 / len(pool_sorted)


@dataclass(frozen=True)
class PercentileTest:
    mean: float
    lo: float
    hi: float
    p_greater: float
    n: int
    n_blocks: int


def percentile_test(
    u: list[float], blocks: list[str], *, n_boot: int, seed: int, level: float = 0.95
) -> PercentileTest:
    """Media de los percentiles frente a 0,5, con bootstrap por bloques (revisión F5: los
    eventos del mismo día o semana no son independientes; el sorteo instante a instante
    subestimaba la varianza).

    ``p_greater``: bootstrap centrado, ``(1 + #{m* - m ≥ m - 0,5}) / (n_boot + 1)``.
    Determinista: bloques ordenados y semilla fija.
    """
    if not u or len(u) != len(blocks):
        raise SchemaError("percentiles vacíos o desalineados con los bloques")
    groups: dict[str, list[float]] = {}
    for x, g in zip(u, blocks, strict=True):
        groups.setdefault(g, []).append(x)
    keys = sorted(groups)
    sums = {k: (sum(groups[k]), len(groups[k])) for k in keys}
    rng = random.Random(seed)  # noqa: S311 (bootstrap, no criptografía)
    mean = sum(u) / len(u)
    boots = []
    for _ in range(n_boot):
        tot = cnt = 0.0
        for k in rng.choices(keys, k=len(keys)):
            s, c = sums[k]
            tot += s
            cnt += c
        boots.append(tot / cnt)
    boots.sort()
    a = (1 - level) / 2
    lo, hi = boots[int(a * n_boot)], boots[min(n_boot - 1, int((1 - a) * n_boot))]
    excess = mean - 0.5
    p = (1 + sum(1 for b in boots if b - mean >= excess)) / (n_boot + 1)
    return PercentileTest(mean, lo, hi, p, len(u), len(keys))


def bh_qvalues(pvals: list[float]) -> list[float]:
    """Benjamini-Hochberg: q-valores en el orden de entrada (CLAUDE.md §11.4)."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: (pvals[i], i))
    q = [0.0] * m
    running = 1.0
    for rank, i in reversed(list(enumerate(order, start=1))):
        running = min(running, pvals[i] * m / rank)
        q[i] = running
    return q

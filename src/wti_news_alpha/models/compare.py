"""Comparación de pérdidas entre modelos sobre las mismas observaciones OOS (CLAUDE.md §11.3).

- ``d_i = pérdida_A - pérdida_B`` (positivo: B mejor). Se agrega por día (serie diaria) y se usan:
  1. **bootstrap de bloques móviles** sobre la serie diaria (bloques de ``ceil(n^(1/3))`` días
     consecutivos; la autocorrelación entre días se conserva) → IC y p unilateral (centrado);
  2. **Diebold-Mariano** con varianza HAC de Newey-West (truncamiento
     ``floor(4 (n/100)^(2/9))``), corrección de Harvey-Leybourne-Newbold y p unilateral con
     t de Student de n-1 grados de libertad.
- Mínimo ``MIN_DAYS`` días: con menos, el tamaño de las pruebas no es fiable (revisión F6).
- Determinista: días ordenados y semilla fija.
"""

import math
import random
from dataclasses import dataclass

from scipy import stats

from wti_news_alpha.errors import SchemaError

MIN_DAYS = 30


@dataclass(frozen=True)
class Comparison:
    mean_diff: float
    lo: float
    hi: float
    p_bootstrap: float
    dm_stat: float
    p_dm: float
    n_obs: int
    n_blocks: int


def block_means(diffs: list[float], blocks: list[str]) -> list[float]:
    """Media de ``diffs`` por bloque, en orden cronológico de los bloques (orden de clave)."""
    if not diffs or len(diffs) != len(blocks):
        raise SchemaError("diferencias vacías o desalineadas con los bloques")
    acc: dict[str, list[float]] = {}
    for d, b in zip(diffs, blocks, strict=True):
        acc.setdefault(b, []).append(d)
    return [sum(acc[k]) / len(acc[k]) for k in sorted(acc)]


def newey_west_var(x: list[float]) -> float:
    """Varianza de largo plazo (Newey-West, kernel de Bartlett) de una serie."""
    n = len(x)
    if n < 2:
        raise SchemaError("Newey-West necesita al menos 2 observaciones")
    mu = sum(x) / n
    e = [v - mu for v in x]
    lag = math.floor(4 * (n / 100) ** (2 / 9))
    var = sum(v * v for v in e) / n
    for k in range(1, min(lag, n - 1) + 1):
        cov = sum(e[t] * e[t - k] for t in range(k, n)) / n
        var += 2 * (1 - k / (lag + 1)) * cov
    return var


def diebold_mariano(daily: list[float]) -> tuple[float, float]:
    """Estadístico DM con corrección HLN y p unilateral (H1: media > 0), t de Student n-1."""
    n = len(daily)
    var = newey_west_var(daily)
    if var <= 0:
        raise SchemaError("varianza HAC no positiva")
    lag = math.floor(4 * (n / 100) ** (2 / 9))
    h = lag + 1
    hln = math.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n) if n + 1 - 2 * h > 0 else 1.0
    stat = hln * (sum(daily) / n) / math.sqrt(var / n)
    return stat, float(stats.t.sf(stat, df=n - 1))


def moving_block_means(daily: list[float], *, n_boot: int, seed: int) -> list[float]:
    """Medias de remuestras por bloques móviles de ``ceil(n^(1/3))`` días consecutivos."""
    n = len(daily)
    size = max(1, math.ceil(n ** (1 / 3)))
    starts = list(range(n - size + 1))
    rng = random.Random(seed)  # noqa: S311 (bootstrap, no criptografía)
    out = []
    for _ in range(n_boot):
        sample: list[float] = []
        while len(sample) < n:
            s0 = rng.choice(starts)
            sample.extend(daily[s0 : s0 + size])
        out.append(sum(sample[:n]) / n)
    return sorted(out)


def compare_losses(
    loss_a: list[float], loss_b: list[float], blocks: list[str], *, n_boot: int, seed: int
) -> Comparison:
    """¿B pierde menos que A? Bootstrap de bloques móviles y Diebold-Mariano (unilaterales)."""
    if len(loss_a) != len(loss_b):
        raise SchemaError("pérdidas de A y B con distinto número de observaciones")
    diffs = [a - b for a, b in zip(loss_a, loss_b, strict=True)]
    daily = block_means(diffs, blocks)
    if len(daily) < MIN_DAYS:
        raise SchemaError(f"comparación con {len(daily)} días (< {MIN_DAYS}): tamaño no fiable")
    mean = sum(daily) / len(daily)
    boots = moving_block_means(daily, n_boot=n_boot, seed=seed)
    lo, hi = boots[int(0.025 * n_boot)], boots[min(n_boot - 1, int(0.975 * n_boot))]
    p_boot = (1 + sum(1 for b in boots if b - mean >= mean)) / (n_boot + 1)
    stat, p_dm = diebold_mariano(daily)
    return Comparison(mean, lo, hi, p_boot, stat, p_dm, len(diffs), len(daily))

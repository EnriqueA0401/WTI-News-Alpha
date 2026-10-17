"""Comparación de pérdidas entre modelos sobre las mismas observaciones OOS (CLAUDE.md §11.3).

- ``d_i = pérdida_A - pérdida_B`` (positivo: B mejor). Se agrega por bloque (día) y se usan:
  1. **bootstrap por bloques** de la media de los bloques → IC y p unilateral (centrado);
  2. **Diebold-Mariano** sobre la serie diaria con varianza HAC de Newey-West
     (truncamiento ``floor(4 (n/100)^(2/9))``) → p unilateral normal.
- Determinista: bloques ordenados y semilla fija.
"""

import math
import random
from dataclasses import dataclass

from wti_news_alpha.errors import SchemaError


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
    """Estadístico DM y p unilateral (H1: media > 0) con varianza HAC."""
    n = len(daily)
    var = newey_west_var(daily)
    if var <= 0:
        raise SchemaError("varianza HAC no positiva")
    stat = (sum(daily) / n) / math.sqrt(var / n)
    return stat, 0.5 * math.erfc(stat / math.sqrt(2))


def compare_losses(
    loss_a: list[float], loss_b: list[float], blocks: list[str], *, n_boot: int, seed: int
) -> Comparison:
    """¿B pierde menos que A? Bootstrap por bloques y Diebold-Mariano (unilaterales)."""
    if len(loss_a) != len(loss_b):
        raise SchemaError("pérdidas de A y B con distinto número de observaciones")
    diffs = [a - b for a, b in zip(loss_a, loss_b, strict=True)]
    daily = block_means(diffs, blocks)
    mean = sum(daily) / len(daily)
    rng = random.Random(seed)  # noqa: S311 (bootstrap, no criptografía)
    boots = sorted(sum(rng.choices(daily, k=len(daily))) / len(daily) for _ in range(n_boot))
    lo, hi = boots[int(0.025 * n_boot)], boots[min(n_boot - 1, int(0.975 * n_boot))]
    p_boot = (1 + sum(1 for b in boots if b - mean >= mean)) / (n_boot + 1)
    stat, p_dm = diebold_mariano(daily)
    return Comparison(mean, lo, hi, p_boot, stat, p_dm, len(diffs), len(daily))

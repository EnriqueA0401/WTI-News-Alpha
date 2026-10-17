"""Comparación de pérdidas (CLAUDE.md §11.3): casos a mano de Newey-West, DM y bootstrap."""

import random

import pytest

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.models import compare


def test_block_means_orders_blocks() -> None:
    assert compare.block_means([1.0, 3.0, 5.0], ["b", "a", "b"]) == [3.0, 3.0]
    with pytest.raises(SchemaError):
        compare.block_means([], [])


def test_newey_west_hand_case() -> None:
    x = [1.0, -1.0, 1.0, -1.0]  # n = 4 → lag = floor(4 * 0,04^(2/9)) = 1
    # var0 = 1; cov1 = (-1*1*3)/4 = -0,75; peso 1 - 1/2 = 0,5 → 1 + 2*0,5*(-0,75) = 0,25
    assert compare.newey_west_var(x) == pytest.approx(0.25)
    with pytest.raises(SchemaError):
        compare.newey_west_var([1.0])


def test_diebold_mariano_and_comparison() -> None:
    daily = [0.2, 0.1, 0.3, 0.2, 0.25, 0.15] * 5
    stat, p = compare.diebold_mariano(daily)
    assert stat > 3
    assert p < 0.01
    assert 0 < p < 0.5
    with pytest.raises(SchemaError):
        compare.diebold_mariano([1.0, 1.0, 1.0])
    a = [1.0] * 60
    b = [0.8] * 30 + [0.9] * 30
    blocks = [f"d{i // 2:02d}" for i in range(60)]
    res = compare.compare_losses(a, b, blocks, n_boot=200, seed=3)
    assert res.mean_diff == pytest.approx(0.15)
    assert res.n_blocks == 30
    with pytest.raises(SchemaError, match="días"):
        compare.compare_losses(a[:20], b[:20], blocks[:20], n_boot=50, seed=3)
    assert res.p_bootstrap < 0.05
    assert res == compare.compare_losses(a, b, blocks, n_boot=200, seed=3)
    with pytest.raises(SchemaError):
        compare.compare_losses([1.0], [1.0, 2.0], ["a"], n_boot=10, seed=1)


def test_size_under_autocorrelated_null() -> None:
    """Revisión F6: con días AR(0,5) y media 0, las dos pruebas rechazan ~5 % a p < 0,05."""
    rng = random.Random(0)  # noqa: S311
    rej_dm = rej_bs = 0
    reps = 120
    for r in range(reps):
        x, e = [], 0.0
        for _ in range(250):
            e = 0.5 * e + rng.gauss(0, 1)
            x.append(e)
        blocks = [f"d{i:03d}" for i in range(250)]
        res = compare.compare_losses(x, [0.0] * 250, blocks, n_boot=200, seed=r)
        rej_dm += res.p_dm < 0.05
        rej_bs += res.p_bootstrap < 0.05
    assert rej_dm / reps < 0.12
    assert rej_bs / reps < 0.12

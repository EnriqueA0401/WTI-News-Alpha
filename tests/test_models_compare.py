"""Comparación de pérdidas (CLAUDE.md §11.3): casos a mano de Newey-West, DM y bootstrap."""

import math

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
    assert p == pytest.approx(0.5 * math.erfc(stat / math.sqrt(2)))
    with pytest.raises(SchemaError):
        compare.diebold_mariano([1.0, 1.0, 1.0])
    a = [1.0] * 60
    b = [0.8] * 30 + [0.9] * 30
    blocks = [f"d{i // 2:02d}" for i in range(60)]
    res = compare.compare_losses(a, b, blocks, n_boot=200, seed=3)
    assert res.mean_diff == pytest.approx(0.15)
    assert res.n_blocks == 30
    assert res.p_bootstrap < 0.05
    assert res == compare.compare_losses(a, b, blocks, n_boot=200, seed=3)
    with pytest.raises(SchemaError):
        compare.compare_losses([1.0], [1.0, 2.0], ["a"], n_boot=10, seed=1)

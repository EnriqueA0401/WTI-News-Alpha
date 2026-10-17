"""Features de noticias desde ``events-v3`` (FASE_04.md): casos a mano y truncamiento."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.features import news, pit

T0 = datetime(2024, 3, 8, 15, 0, tzinfo=UTC)
M = timedelta(minutes=1)
FAMS = ["opec_policy", "weather"]


def _arts(rows: list[tuple[int, str, bool, bool, str]]) -> pl.DataFrame:
    """(minuto, familia, elegible, primero del evento, origen del titular)."""
    return pl.DataFrame(
        {
            "first_available_at": [T0 + r[0] * M for r in rows],
            "family": [r[1] for r in rows],
            "eligible": [r[2] for r in rows],
            "is_event_first": [r[3] for r in rows],
            "title_source": [r[4] for r in rows],
        },
        schema_overrides={"first_available_at": pl.Datetime("us", "UTC")},
    )


def _grid(minutes: list[int]) -> pl.DataFrame:
    return pl.DataFrame(
        {"decision_time": [T0 + m * M for m in minutes]},
        schema={"decision_time": pl.Datetime("us", "UTC")},
    )


def _run(arts: pl.DataFrame, grid: pl.DataFrame, start: datetime = T0) -> pl.DataFrame:
    return news.news_features(grid, arts, families=FAMS, windows=[15, 60], coverage_start=start)


def test_counts_hand_case() -> None:
    arts = _arts(
        [
            (0, "opec_policy", True, True, "title"),
            (0, "opec_policy", True, False, "title"),  # misma hora de fichero
            (10, "weather", True, True, "url_slug"),
            (20, "opec_policy", True, False, "title"),
            (20, "company", False, False, "title"),  # no elegible: no cuenta
        ]
    )
    out = _run(arts, _grid([0, 15, 20, 75]), start=T0 - timedelta(hours=2))
    assert out["news_n_all_15m"].to_list() == [2, 1, 2, 0]  # ventana (t-15, t]
    assert out["news_n_all_60m"].to_list() == [2, 3, 4, 1]
    assert out["news_n_opec_policy_15m"].to_list() == [2, 0, 1, 0]
    assert out["news_new_all_60m"].to_list() == [1, 2, 2, 0]
    assert out["news_new_weather_15m"].to_list() == [0, 1, 1, 0]
    assert out["news_title_share_60m"].to_list() == pytest.approx([1.0, 2 / 3, 3 / 4, 1.0])
    assert out["news_event_age_min_opec_policy"].to_list() == [0.0, 15.0, 20.0, 75.0]
    assert out["news_event_age_min_weather"].to_list() == [None, 5.0, 10.0, 65.0]
    assert out["news_available_at"].to_list() == [T0, T0 + 10 * M, T0 + 20 * M, T0 + 20 * M]
    pit.check_no_leakage(out)


def test_before_coverage_is_null_not_zero() -> None:
    arts = _arts([(30, "weather", True, True, "title")])
    out = _run(arts, _grid([5, 40, 80]), start=T0 + 20 * M)
    # En 5 no hay cobertura; en 40 la ventana de 60 min empieza antes de la cobertura.
    assert out["news_n_all_15m"].to_list() == [None, 1, 0]
    assert out["news_n_all_60m"].to_list() == [None, None, 1]
    assert out["news_title_share_60m"].to_list() == [None, None, 1.0]


def test_validation() -> None:
    arts = _arts([(0, "weather", True, True, "title")])
    with pytest.raises(SchemaError, match="faltan"):
        _run(arts.drop("eligible"), _grid([0]))
    with pytest.raises(SchemaError, match="windows"):
        news.news_features(_grid([0]), arts, families=FAMS, windows=[], coverage_start=T0)


@settings(max_examples=40, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(0, 300),
            st.sampled_from(["opec_policy", "weather", "company"]),
            st.booleans(),
            st.booleans(),
            st.sampled_from(["title", "url_slug"]),
        ),
        min_size=1,
        max_size=60,
    ),
    st.integers(0, 300),
)
def test_property_truncation(rows: list[tuple[int, str, bool, bool, str]], cut: int) -> None:
    arts = _arts(rows)
    grid = _grid(list(range(0, 305, 5)))
    t_cut = T0 + cut * M
    full = _run(arts, grid)
    trunc = _run(arts.filter(pl.col("first_available_at") <= t_cut), grid)
    keep = pl.col("decision_time") <= t_cut
    assert full.filter(keep).equals(trunc.filter(keep))
    pit.check_no_leakage(full)

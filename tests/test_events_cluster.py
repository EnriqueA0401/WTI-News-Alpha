"""Tests de la agrupación online en eventos (FASE_03.md paso 4). Datos SINTÉTICOS."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.events import cluster as cl

T0 = datetime(2024, 1, 1, tzinfo=UTC)
GAP = timedelta(hours=24)


def _df(rows: list[tuple[str, int, bool, str, str, str, str]]) -> pl.DataFrame:
    """(id, minutos, elegible, familia, mecanismo, país, medio)."""
    return pl.DataFrame(
        {
            "article_id": [r[0] for r in rows],
            "first_available_at": [T0 + timedelta(minutes=r[1]) for r in rows],
            "source_file_ts": [T0 + timedelta(minutes=r[1] - 15) for r in rows],
            "row_in_file": list(range(len(rows))),
            "eligible": [r[2] for r in rows],
            "family": [r[3] for r in rows],
            "mechanism": [r[4] for r in rows],
            "country": [r[5] for r in rows],
            "source_key": [r[6] for r in rows],
        },
        schema_overrides={
            "first_available_at": pl.Datetime("us", "UTC"),
            "source_file_ts": pl.Datetime("us", "UTC"),
        },
    )


def test_primary_country() -> None:
    locs = pl.Series(
        ["1#Saudi Arabia#SA#SA#25#45#SA;4#Riyadh#SA#SA10#24#46#-1", "", None], dtype=pl.Utf8
    )
    assert pl.select(cl.primary_country(pl.lit(locs))).to_series().to_list() == ["SA", "XX", "XX"]


def test_assign_events_hand_cases() -> None:
    df = _df(
        [
            ("a", 0, True, "opec_policy", "tightens_supply", "SA", "s1"),
            ("b", 60, True, "opec_policy", "tightens_supply", "SA", "s2"),
            ("c", 120, True, "opec_policy", "loosens_supply", "SA", "s1"),  # cambia el hecho
            ("d", 180, True, "opec_policy", "tightens_supply", "IR", "s3"),  # otro país
            ("e", 60 * 30, True, "opec_policy", "tightens_supply", "SA", "s4"),  # > 24 h: nuevo
            ("f", 200, False, "opec_policy", "tightens_supply", "SA", "s5"),  # no elegible
        ]
    )
    out = cl.assign_events(df, GAP).sort("article_id")
    assert out["event_id"].to_list() == ["a", "a", "a", "d", "e", None]
    assert out["event_seq"].to_list() == [1, 2, 3, 1, 1, None]
    assert out["event_sources_so_far"].to_list() == [1, 2, 2, 1, 1, None]
    assert out["mechanism_differs"].to_list() == [False, False, True, False, False, None]
    assert out["is_event_first"].to_list() == [True, False, False, True, True, None]


def test_inherit_events() -> None:
    df = pl.DataFrame(
        {"article_id": ["a", "b", "c"], "dup_of": [None, "a", "z"], "event_id": ["a", None, None]}
    )
    out = cl.inherit_events(df)
    assert out["event_id"].to_list() == ["a", "a", None]


def test_missing_columns() -> None:
    with pytest.raises(SchemaError, match="faltan"):
        cl.assign_events(pl.DataFrame({"article_id": ["a"]}), GAP)


@settings(max_examples=100, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(0, 6000),
            st.booleans(),
            st.integers(0, 2),
            st.integers(0, 2),
            st.integers(0, 3),
            st.integers(0, 1),
        ),
        min_size=1,
        max_size=40,
    ),
    st.integers(0, 6000),
)
def test_property_truncation(rows: list[tuple[int, bool, int, int, int, int]], cut: int) -> None:
    fams, ccs, mechs = ["opec_policy", "weather", "other"], ["SA", "US", "XX"], ["a", "b"]
    df = _df(
        [
            (f"id{i}", m, e, fams[f], mechs[k], ccs[c], f"s{s}")
            for i, (m, e, f, c, s, k) in enumerate(rows)
        ]
    )
    full = cl.assign_events(df, GAP)
    t_cut = T0 + timedelta(minutes=cut)
    trunc = cl.assign_events(df.filter(pl.col("first_available_at") <= t_cut), GAP)
    cols = [
        "article_id",
        "event_id",
        "event_seq",
        "event_sources_so_far",
        "is_event_first",
        "mechanism_differs",
    ]
    exp = full.filter(pl.col("first_available_at") <= t_cut).select(cols).sort("article_id")
    assert trunc.select(cols).sort("article_id").equals(exp)

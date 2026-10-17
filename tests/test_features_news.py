"""Features de noticias desde ``events-v3`` (FASE_04.md, ADR-035): casos a mano, huecos de la
fuente y truncamiento."""

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
P = news.NewsParams(
    families=FAMS,
    windows=[15, 60],
    slot_lag=timedelta(minutes=15),
    slot_deadline=timedelta(minutes=20),
    min_slot_articles=5,
)


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


def _slots(
    start: int,
    end: int,
    *,
    missing: frozenset[int] = frozenset(),
    late: frozenset[int] = frozenset(),
    weak: frozenset[int] = frozenset(),
) -> pl.DataFrame:
    """Ficheros nominales cada 15 min en [start, end] (minutos desde T0); disponibles a +15 min."""
    noms = [m for m in range(start, end + 1, 15) if m not in missing]
    return pl.DataFrame(
        {
            "source_file_ts": [T0 + m * M for m in noms],
            "available_at": [T0 + (m + (60 if m in late else 15)) * M for m in noms],
            "n": [1 if m in weak else 50 for m in noms],
        },
        schema_overrides={
            "source_file_ts": pl.Datetime("us", "UTC"),
            "available_at": pl.Datetime("us", "UTC"),
        },
    )


def _grid(minutes: list[int]) -> pl.DataFrame:
    return pl.DataFrame(
        {"decision_time": [T0 + m * M for m in minutes]},
        schema={"decision_time": pl.Datetime("us", "UTC")},
    )


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
    out = news.news_features(_grid([0, 15, 20, 75]), arts, _slots(-180, 120), P)
    assert out["news_source_ok"].to_list() == [True] * 4
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


@pytest.mark.parametrize("kind", ["missing", "late", "weak"])
def test_source_hole_is_null_not_zero(kind: str) -> None:
    """Hueco en el nominal 30 (falta, llega tarde o está degradado): nada de ceros falsos."""
    arts = _arts([(0, "opec_policy", True, True, "title")])
    slots = _slots(-180, 300, **{kind: frozenset({30})})  # type: ignore[arg-type]
    out = news.news_features(_grid([50, 65, 110]), arts, slots, P)
    # 50: el fichero necesario (nominal 30) es el hueco.
    assert out["news_source_ok"].to_list() == [False, True, True]
    assert out["news_n_all_15m"].to_list() == [None, 0, 0]
    assert out["news_n_all_60m"].to_list() == [None, None, 0]  # 65: la ventana cubre el hueco
    assert out["news_event_age_min_opec_policy"].to_list() == [None, None, None]
    assert out["news_available_at"].to_list()[0] is None
    pit.check_no_leakage(out)


def test_stale_source_and_before_coverage_are_null() -> None:
    arts = _arts([(35, "weather", True, True, "title")])
    out = news.news_features(_grid([5, 40, 90, 200]), arts, _slots(15, 120), P)
    assert out["news_source_ok"].to_list() == [False, True, True, False]  # 200: sin ficheros
    assert out["news_n_all_15m"].to_list() == [None, 1, 0, None]
    assert out["news_n_all_60m"].to_list() == [None, None, 1, None]
    assert out["news_title_share_60m"].to_list() == [None, None, 1.0, None]
    assert out["news_event_age_min_weather"].to_list() == [None, 5.0, 55.0, None]


def test_validation() -> None:
    arts = _arts([(0, "weather", True, True, "title")])
    slots = _slots(-30, 30)
    with pytest.raises(SchemaError, match="faltan columnas de artículos"):
        news.news_features(_grid([0]), arts.drop("eligible"), slots, P)
    with pytest.raises(SchemaError, match="faltan columnas de ficheros"):
        news.news_features(_grid([0]), arts, slots.drop("n"), P)
    p = news.NewsParams(FAMS, [], P.slot_lag, P.slot_deadline, 5)
    with pytest.raises(SchemaError, match="windows"):
        news.news_features(_grid([0]), arts, slots, p)
    with pytest.raises(SchemaError, match="ningún fichero"):
        news.news_features(_grid([0]), arts, _slots(-30, 30, weak=frozenset(range(-30, 31))), P)
    with pytest.raises(SchemaError, match="duplicado"):
        news.news_features(_grid([0]), arts, pl.concat([slots, slots]), P)


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
    st.frozensets(st.sampled_from(list(range(0, 300, 15)))),
    st.frozensets(st.sampled_from(list(range(0, 300, 15)))),
    st.integers(0, 300),
)
def test_property_truncation(
    rows: list[tuple[int, str, bool, bool, str]],
    missing: frozenset[int],
    late: frozenset[int],
    cut: int,
) -> None:
    arts = _arts(rows)
    slots = _slots(-60, 300, missing=missing, late=late)
    grid = _grid(list(range(0, 305, 5)))
    t_cut = T0 + cut * M
    full = news.news_features(grid, arts, slots, P)
    trunc_slots = slots.filter(pl.col("available_at") <= t_cut)
    if (
        trunc_slots.filter(
            pl.col("available_at") <= pl.col("source_file_ts") + P.slot_deadline
        ).height
        == 0
    ):
        return  # sin ningún fichero bueno aún: la función rechaza la entrada (probado aparte)
    trunc = news.news_features(
        grid, arts.filter(pl.col("first_available_at") <= t_cut), trunc_slots, P
    )
    keep = pl.col("decision_time") <= t_cut
    assert full.filter(keep).equals(trunc.filter(keep))
    pit.check_no_leakage(full)

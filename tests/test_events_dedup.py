"""Tests de deduplicación online (FASE_03.md paso 1). Datos SINTÉTICOS."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wti_news_alpha.errors import SchemaError
from wti_news_alpha.events import dedup as dd

T0 = datetime(2024, 1, 1, tzinfo=UTC)
WIN = timedelta(hours=48)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.Reuters.com/a/b/?utm_source=x&id=3#frag", "reuters.com/a/b/?id=3"),
        ("http://m.example.com/x/", "example.com/x"),
        ("https://calgaryherald.com:443/opinion/x", "calgaryherald.com/opinion/x"),
        ("https://amp.site.com/news/123/amp", "site.com/news/123"),
        ("https://site.com/p?fbclid=abc", "site.com/p"),
        ("https://site.com/p?a=1&gclid=z&b=2", "site.com/p?a=1&b=2"),
    ],
)
def test_canonical_url(url: str, expected: str) -> None:
    out = pl.select(dd.canonical_url(pl.lit(url))).item()
    assert out == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Hike in petrol, diesel prices on New Year's eve - Business & Finance",
            "hike in petrol diesel prices on new year s eve",
        ),
        ("OPEC+ agrees to cut output | Reuters", "opec agrees to cut output"),
        ("Short title", None),
        (None, None),
    ],
)
def test_normalized_title(title: str | None, expected: str | None) -> None:
    out = pl.select(dd.normalized_title(pl.lit(title, dtype=pl.Utf8))).item()
    assert out == expected


def _df(rows: list[tuple[str, int, str, str, str | None]]) -> pl.DataFrame:
    """(article_id, minutos desde T0, medio, url, título)."""
    return pl.DataFrame(
        {
            "article_id": [r[0] for r in rows],
            "first_available_at": [T0 + timedelta(minutes=r[1]) for r in rows],
            "source_file_ts": [T0 + timedelta(minutes=r[1] - 15) for r in rows],
            "row_in_file": list(range(len(rows))),
            "source_common_name": [r[2] for r in rows],
            "url": [r[3] for r in rows],
            "title": [r[4] for r in rows],
        },
        schema_overrides={
            "first_available_at": pl.Datetime("us", "UTC"),
            "source_file_ts": pl.Datetime("us", "UTC"),
            "title": pl.Utf8,
            "url": pl.Utf8,
        },
    )


TITLE = "Syria's U.S.-trained opposition fighters wait to learn of their role"


def test_mark_duplicates_hand_cases() -> None:
    df = _df(
        [
            ("a", 0, "ypradio.org", "https://www.ypradio.org/x", TITLE),
            ("b", 15, "wkms.org", "https://www.wkms.org/x", TITLE),  # re-publicación
            ("c", 30, "ypradio.org", "https://ypradio.org/x?utm_source=fb", TITLE),  # misma URL
            ("d", 45, "ypradio.org", "https://ypradio.org/other", TITLE),  # mismo medio y título
            ("e", 60 * 24 * 10, "kpbs.org", "https://kpbs.org/x", TITLE),  # 10 días después: nuevo
            ("f", 60, "x.com", "https://x.com/1", None),
            ("g", 75, "y.com", "https://y.com/1", None),
        ]
    )
    out = dd.mark_duplicates(df, WIN).sort("article_id")
    assert out["dup_kind"].to_list() == [
        None,
        "syndicated_title",
        "url",
        "same_source_title",
        None,
        None,
        None,
    ]
    assert out["dup_of"].to_list() == [None, "a", "a", "a", None, None, None]


def test_null_urls_are_not_duplicates() -> None:
    df = _df([("a", 0, "s", None, None), ("b", 15, "s", None, None)])  # type: ignore[list-item]
    out = dd.mark_duplicates(df, WIN)
    assert out["dup_kind"].to_list() == [None, None]


def test_missing_columns_and_duplicate_ids() -> None:
    with pytest.raises(SchemaError, match="faltan"):
        dd.mark_duplicates(pl.DataFrame({"article_id": ["a"]}), WIN)
    df = _df([("a", 0, "s", "https://s/1", None), ("a", 15, "s", "https://s/2", None)])
    with pytest.raises(SchemaError, match="duplicado"):
        dd.mark_duplicates(df, WIN)


@settings(max_examples=100, deadline=None)
@given(
    st.lists(
        st.tuples(st.integers(0, 6000), st.integers(0, 3), st.integers(0, 4), st.integers(0, 3)),
        min_size=1,
        max_size=40,
    ),
    st.integers(0, 6000),
)
def test_property_truncation(rows: list[tuple[int, int, int, int]], cut: int) -> None:
    # El marcado de cualquier artículo disponible en t no cambia si se quitan los posteriores.
    titles = [
        None,
        TITLE,
        "OPEC agrees a new production cut for next year",
        "Oil prices jump after the attack",
    ]
    df = _df(
        [
            (f"id{i}", m, f"src{s}.com", f"https://src{s}.com/p{u}", titles[t])
            for i, (m, s, u, t) in enumerate(rows)
        ]
    )
    full = dd.mark_duplicates(df, WIN)
    t_cut = T0 + timedelta(minutes=cut)
    trunc = dd.mark_duplicates(df.filter(pl.col("first_available_at") <= t_cut), WIN)
    cols = ["article_id", "dup_kind", "dup_of"]
    expected = full.filter(pl.col("first_available_at") <= t_cut).select(cols).sort("article_id")
    assert trunc.select(cols).sort("article_id").equals(expected)

"""Splitter walk-forward trimestral con purga y embargo (FASE_06.md, ADR-040)."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wti_news_alpha.errors import SchemaError, SealedTestError
from wti_news_alpha.labels import splits

UTC_US = pl.Datetime("us", "UTC")
D = timedelta(days=1)
SEALED = datetime(2025, 7, 1, tzinfo=UTC)
KW = {"sealed_start": SEALED, "horizon": "2h"}


def _frame(times: list[datetime], horizon: timedelta) -> pl.DataFrame:
    return pl.DataFrame(
        {"decision_time": times, "label_end": [t + horizon for t in times]},
        schema={"decision_time": UTC_US, "label_end": UTC_US},
    )


def test_quarters_purge_and_embargo_hand_case() -> None:
    times = [datetime(2019, 12, 30, 12, tzinfo=UTC), datetime(2019, 12, 31, 23, tzinfo=UTC)]
    times += [datetime(2019, 11, 1, tzinfo=UTC), datetime(2020, 1, 2, tzinfo=UTC)]
    times += [datetime(2020, 3, 31, 23, tzinfo=UTC), datetime(2020, 4, 1, tzinfo=UTC)]
    df = _frame(times, timedelta(hours=2))
    folds = splits.walk_forward(
        df,
        first_test=datetime(2020, 1, 1, tzinfo=UTC),
        end=datetime(2020, 7, 1, tzinfo=UTC),
        step="quarter",
        embargo=D,
        **KW,
    )
    assert [f.test_start for f in folds] == [
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2020, 4, 1, tzinfo=UTC),
    ]
    f0 = folds[0]
    tr = sorted(df.filter(f0.train)["decision_time"].to_list())
    te = sorted(df.filter(f0.test)["decision_time"].to_list())
    # Embargo de 1 día: el train acaba antes de 2019-12-31 00:00 (decisión y fin de etiqueta).
    assert tr == [datetime(2019, 11, 1, tzinfo=UTC), datetime(2019, 12, 30, 12, tzinfo=UTC)]
    assert datetime(2019, 12, 31, 23, tzinfo=UTC) not in tr
    assert te == [datetime(2020, 1, 2, tzinfo=UTC), datetime(2020, 3, 31, 23, tzinfo=UTC)]
    f1 = folds[1]
    assert df.filter(f1.test)["decision_time"].to_list() == [datetime(2020, 4, 1, tzinfo=UTC)]
    # Ventana creciente: el train del fold 2 contiene el test del fold 1 salvo lo purgado.
    assert datetime(2020, 1, 2, tzinfo=UTC) in df.filter(f1.train)["decision_time"].to_list()
    assert (
        datetime(2020, 3, 31, 23, tzinfo=UTC) not in df.filter(f1.train)["decision_time"].to_list()
    )


def test_validation() -> None:
    df = _frame([datetime(2020, 1, 1, tzinfo=UTC)], D)
    with pytest.raises(SchemaError, match="step"):
        splits.walk_forward(
            df,
            first_test=datetime(2020, 1, 1, tzinfo=UTC),
            end=datetime(2020, 4, 1, tzinfo=UTC),
            step="month",
            embargo=D,
            **KW,
        )  # type: ignore[arg-type]
    with pytest.raises(SchemaError, match="label_end"):
        splits.walk_forward(
            df.drop("label_end"),
            first_test=datetime(2020, 1, 1, tzinfo=UTC),
            end=datetime(2020, 4, 1, tzinfo=UTC),
            step="quarter",
            embargo=D,
            **KW,
        )
    bad = df.with_columns(label_end=pl.col("decision_time") - D)
    with pytest.raises(SchemaError, match="anterior"):
        splits.walk_forward(
            bad,
            first_test=datetime(2020, 1, 1, tzinfo=UTC),
            end=datetime(2020, 4, 1, tzinfo=UTC),
            step="quarter",
            embargo=D,
            **KW,
        )


@settings(max_examples=60, deadline=None)
@given(
    st.lists(st.integers(0, 3 * 365 * 24), min_size=5, max_size=200, unique=True),
    st.integers(1, 96),
    st.sampled_from(["quarter", "half"]),
)
def test_property_no_overlap(hours: list[int], horizon_h: int, step: str) -> None:
    """Ningún intervalo de etiqueta del train toca el test ni su embargo; train antes que test."""
    base = datetime(2018, 1, 1, tzinfo=UTC)
    df = _frame([base + timedelta(hours=h) for h in hours], timedelta(hours=horizon_h))
    folds = splits.walk_forward(
        df,
        first_test=datetime(2019, 1, 1, tzinfo=UTC),
        end=datetime(2021, 1, 1, tzinfo=UTC),
        step=step,
        embargo=D,
        **KW,  # type: ignore[arg-type]
    )
    for f in folds:
        tr, te = df.filter(f.train), df.filter(f.test)
        assert (te["decision_time"] >= f.test_start).all()
        assert (te["decision_time"] < f.test_end).all()
        if tr.height:
            assert tr["label_end"].max() < f.test_start - D  # type: ignore[operator]
            assert tr["decision_time"].max() < f.test_start - D  # type: ignore[operator]
        assert not (f.train & f.test).any()


def test_sealed_and_month_start_are_enforced() -> None:
    df = _frame([datetime(2020, 1, 1, tzinfo=UTC)], D)
    with pytest.raises(SealedTestError, match="sellado"):
        splits.walk_forward(
            df,
            first_test=datetime(2020, 1, 1, tzinfo=UTC),
            end=datetime(2026, 8, 1, tzinfo=UTC),
            step="quarter",
            embargo=D,
            **KW,
        )  # type: ignore[arg-type]
    late = _frame([datetime(2025, 7, 2, tzinfo=UTC)], D)
    with pytest.raises(SealedTestError, match="filas"):
        splits.walk_forward(
            late,
            first_test=datetime(2020, 1, 1, tzinfo=UTC),
            end=SEALED,
            step="quarter",
            embargo=D,
            **KW,
        )  # type: ignore[arg-type]
    with pytest.raises(SchemaError, match="primer instante"):
        splits.walk_forward(
            df,
            first_test=datetime(2020, 1, 31, tzinfo=UTC),
            end=SEALED,
            step="quarter",
            embargo=D,
            **KW,
        )  # type: ignore[arg-type]


def test_null_label_end_never_trains_and_hash_is_order_sensitive() -> None:
    times = [
        datetime(2019, 6, 1, tzinfo=UTC),
        datetime(2019, 6, 2, tzinfo=UTC),
        datetime(2020, 1, 5, tzinfo=UTC),
    ]
    df = _frame(times, D).with_columns(
        label_end=pl.when(pl.col("decision_time") == times[1])
        .then(None)
        .otherwise(pl.col("label_end"))
    )
    (f,) = splits.walk_forward(
        df,
        first_test=datetime(2020, 1, 1, tzinfo=UTC),
        end=datetime(2020, 4, 1, tzinfo=UTC),
        step="quarter",
        embargo=D,
        **KW,
    )  # type: ignore[arg-type]
    assert df.filter(f.train)["decision_time"].to_list() == [times[0]]
    assert splits.frame_hash(df) != splits.frame_hash(df.reverse())

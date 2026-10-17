"""Features de calendario (FASE_04.md): casos a mano con cambio de hora (DST)."""

from datetime import UTC, datetime

import polars as pl
import pytest

from wti_news_alpha.features import calendar


def _grid(times: list[datetime]) -> pl.DataFrame:
    return pl.DataFrame(
        {"decision_time": times}, schema={"decision_time": pl.Datetime("us", "UTC")}
    )


def test_hour_weekday_and_next_releases_across_dst() -> None:
    times = [
        datetime(2024, 3, 6, 15, 0, tzinfo=UTC),  # mié 10:00 EST → WPSR en 30 min
        datetime(2024, 3, 6, 15, 30, tzinfo=UTC),  # mié 10:30 EST: ya publicado → próximo miércoles
        datetime(2024, 3, 8, 20, 0, tzinfo=UTC),  # vie 15:00 EST → COT en 30 min
        datetime(2024, 3, 10, 22, 0, tzinfo=UTC),  # dom 18:00 EDT (tras el cambio de hora)
    ]
    out = calendar.calendar_features(_grid(times))
    assert out["cal_hour_et"].to_list() == pytest.approx([10.0, 10.5, 15.0, 18.0])
    assert out["cal_weekday_et"].to_list() == [3, 3, 5, 7]
    # Del mié 6-mar 10:30 EST al mié 13-mar 10:30 EDT hay 7 días menos 1 h.
    assert out["cal_min_to_wpsr_std"].to_list() == pytest.approx(
        [30.0, 7 * 1440 - 60, 4 * 1440 + 18.5 * 60, 2 * 1440 + 990]
    )
    assert out["cal_min_to_cot_std"].to_list()[2] == pytest.approx(30.0)
    # Dom 18:00 EDT → vie 15-mar 15:30 EDT: 4 días + 21,5 h.
    assert out["cal_min_to_cot_std"].to_list()[3] == pytest.approx(4 * 1440 + 21.5 * 60)

"""Features de calendario (FASE_04.md; Documento §7: "variables conocidas de antemano").

- ``cal_hour_et`` (hora decimal) y ``cal_weekday_et`` (1 = lunes … 7 = domingo) en
  America/New_York, con el cambio de hora resuelto por la zona.
- ``cal_min_to_wpsr_std`` / ``cal_min_to_cot_std``: minutos hasta la próxima publicación
  según el **calendario estándar** (WPSR: miércoles 10:30 ET, D8; COT: viernes 15:30 ET,
  ADR-022), estrictamente posterior a t. Es una regla fija, no la hora real del próximo
  release: no usa información futura. Los desplazamientos por festivo no se reflejan (riesgo
  documentado; la hora real ya se usa en ``wpsr_age_h`` y ``cot_age_h``).
"""

from datetime import time

import polars as pl

from wti_news_alpha.features import pit

DECISION = pit.DECISION
NY = "America/New_York"
WPSR_STD = (3, time(10, 30))  # miércoles
COT_STD = (5, time(15, 30))  # viernes


def _minutes_to_next(weekday: int, at: time) -> pl.Expr:
    t = pl.col(DECISION)
    local = t.dt.convert_time_zone(NY)
    ahead = (weekday - local.dt.weekday()) % 7

    def candidate(extra_days: int) -> pl.Expr:
        day = local.dt.date() + pl.duration(days=ahead + extra_days)
        wall = day.dt.combine(at)
        return wall.dt.replace_time_zone(NY).dt.convert_time_zone("UTC")

    nxt = pl.when(candidate(0) > t).then(candidate(0)).otherwise(candidate(7))
    return (nxt - t).dt.total_microseconds() / 60e6


def calendar_features(grid: pl.DataFrame) -> pl.DataFrame:
    """Añade ``cal_*`` a la rejilla (mismas filas, ordenada por ``decision_time``)."""
    pit.require_grid(grid)
    local = pl.col(DECISION).dt.convert_time_zone(NY)
    return grid.sort(DECISION).with_columns(
        cal_hour_et=local.dt.hour() + local.dt.minute() / 60,
        cal_weekday_et=local.dt.weekday(),
        cal_min_to_wpsr_std=_minutes_to_next(*WPSR_STD),
        cal_min_to_cot_std=_minutes_to_next(*COT_STD),
    )

"""Datos **SINTÉTICOS** para los controles del pipeline (CLAUDE.md §3.4, §8.5).

Nunca se mezclan con datos reales: llevan ``synthetic = True`` y solo se usan en tests y en
el informe de controles de G4. Estructura mínima de ``feat-v1`` + ``label-v1``:
- una rejilla cada 30 min, 14 h por día hábil;
- mercado y fundamentales como ruido;
- un conteo de noticias Poisson independiente de todo lo demás.

**Señal plantada** (control positivo): cuando hay noticias en la última hora, el retorno
futuro a 15 min tiene una deriva de ``signal`` desviaciones típicas hacia arriba.
Con ``signal = 0`` es el control negativo.
"""

from datetime import UTC, date, datetime, timedelta

import numpy as np
import polars as pl


def make_dataset(*, n_days: int, signal: float, seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    d0 = date(2019, 1, 1)
    days = pl.date_range(d0, d0 + timedelta(days=2 * n_days), eager=True)
    days = days.filter(days.dt.weekday() <= 5).head(n_days)
    times = [
        datetime(d.year, d.month, d.day, 8, tzinfo=UTC) + timedelta(minutes=30 * k)
        for d in days
        for k in range(28)
    ]
    n = len(times)
    rv = np.exp(rng.normal(np.log(0.003), 0.3, n))
    news = rng.poisson(0.4, n)
    sigma15 = rv * np.sqrt(15 / 60)
    drift = np.where(news > 0, signal, 0.0)
    ret = sigma15 * (drift + rng.standard_normal(n))
    t = pl.Series("decision_time", times, dtype=pl.Datetime("us", "UTC"))
    return pl.DataFrame(
        {
            "decision_time": t,
            "mkt_rv_60m": rv,
            "mkt_ret_5m": rng.standard_normal(n) * 0.001,
            "mkt_available_at": t,
            "wpsr_A_chg": rng.standard_normal(n),
            "wpsr_available_at": t - timedelta(hours=1),
            "cal_hour_et": [x.hour + x.minute / 60 for x in times],
            "news_n_all_60m": news.astype(np.int64),
            "news_available_at": t,
            "ret_15m": ret,
            "label_end_15m": t + timedelta(minutes=16),
            "synthetic": True,
        }
    )

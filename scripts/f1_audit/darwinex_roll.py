"""F1 bis: ¿qué vencimiento sigue el CFD XTIUSD de Darwinex y cuándo rola? (ADR-017)

Sin red. Con reports/f1/darwinex_vs_eia_daily.csv (mid 14:29 ET vs RCLC1/RCLC2):
w = (mid - C1) / (C2 - C1) en días con |C2 - C1| >= 0,20 USD; w~0 sigue C1, w~1 sigue C2.
Vencimiento de C1 por la regla NYMEX 200102.F (verificada): fin de negociación 3 días hábiles
antes del día 25 del mes anterior al de entrega (4 si el 25 no es hábil). APROXIMACIÓN de
diagnóstico: hábiles = lun-vie sin festivos (error posible de ±1 día). No se usa en etiquetas.
"""

from datetime import date, timedelta

import polars as pl

from wti_news_alpha.runlog import REPO_ROOT

IN = REPO_ROOT / "reports/f1/darwinex_vs_eia_daily.csv"
OUT = REPO_ROOT / "reports/f1"


def _bday_back(d: date, n: int) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def last_trade(delivery_year: int, delivery_month: int) -> date:
    """Último día de negociación aproximado del contrato con entrega en (año, mes)."""
    y, m = (delivery_year, delivery_month - 1) if delivery_month > 1 else (delivery_year - 1, 12)
    d25 = date(y, m, 25)
    return _bday_back(d25, 3 if d25.weekday() < 5 else 4)


def expiries(start: date, end: date) -> list[date]:
    out = []
    for y in range(start.year - 1, end.year + 2):
        for m in range(1, 13):
            lt = last_trade(y, m)
            if start - timedelta(days=40) <= lt <= end + timedelta(days=40):
                out.append(lt)
    return sorted(out)


def main() -> None:
    j = pl.read_csv(IN, try_parse_dates=True).sort("date")
    exps = expiries(j["date"].min(), j["date"].max())
    nxt = pl.DataFrame({"exp": exps}).sort("exp")
    j = j.join_asof(nxt, left_on="date", right_on="exp", strategy="forward")
    j = j.with_columns(
        (pl.col("RCLC2") - pl.col("RCLC1")).alias("spr"),
        ((pl.col("mid_1429") - pl.col("RCLC1")) / (pl.col("RCLC2") - pl.col("RCLC1"))).alias("w"),
        pl.business_day_count(pl.col("date"), pl.col("exp")).alias("bdays_to_c1_expiry"),
    )
    ok = j.filter(pl.col("spr").abs() >= 0.20)
    prof = (
        ok.group_by("bdays_to_c1_expiry")
        .agg(
            pl.len().alias("n"),
            pl.col("w").median().alias("w_median"),
            (pl.col("w") > 0.5).mean().alias("share_tracks_c2"),
        )
        .sort("bdays_to_c1_expiry", descending=True)
    )
    prof.write_csv(OUT / "darwinex_roll_profile.csv")
    print("días con |C2-C1| >= 0,20:", ok.height, "de", j.height)
    with pl.Config(tbl_rows=40):
        print(prof)


if __name__ == "__main__":
    main()

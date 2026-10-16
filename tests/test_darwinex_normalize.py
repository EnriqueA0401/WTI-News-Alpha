"""Tests de la capa normalizada Darwinex XTIUSD M1 (docs/plans/FASE_02_precio.md).

Datos SINTÉTICOS: barras inventadas para fijar casos calculados a mano.
"""

import hashlib
import json
import os
import stat
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wti_news_alpha.config import CONFIG_DIR, DarwinexConfig, load_darwinex_config
from wti_news_alpha.errors import CalendarError, RawIntegrityError, SchemaError
from wti_news_alpha.normalization import darwinex as dwx

HEADER = "Date,Time,Open,High,Low,Close,TickVolume,AvgSpread"


def _row(  # noqa: PLR0913, PLR0917 - una columna por argumento
    d: str,
    t: str,
    o: float = 50.0,
    h: float = 50.1,
    lo: float = 49.9,
    c: float = 50.05,
    tv: int = 10,
    sp: float = 0.04,
) -> str:
    return f"{d},{t},{o:.3f},{h:.3f},{lo:.3f},{c:.3f},{tv},{sp:.3f}"


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


@pytest.fixture
def cfg() -> DarwinexConfig:
    return load_darwinex_config(CONFIG_DIR)


def _relaxed(cfg: DarwinexConfig) -> DarwinexConfig:
    data = cfg.model_dump(by_alias=True)
    data["max_drop_fraction"]["value"] = 0.5
    return DarwinexConfig.model_validate(data)


def _norm(cfg: DarwinexConfig, *rows: str) -> tuple[pl.DataFrame, dwx.DropStats]:
    return dwx.normalize_bars(dwx.parse_csv(_csv(*rows)), cfg)


# ---------------------------------------------------------------- config


def test_repository_config_loads(cfg: DarwinexConfig) -> None:
    assert cfg.server_offset_vs_new_york_hours.require("off") == 7
    assert cfg.bar_timestamp_is_start.require("start") is True


# ---------------------------------------------------------------- parse


def test_parse_csv_types_and_source_line() -> None:
    raw = dwx.parse_csv(_csv(_row("2018.01.08", "01:00"), _row("2018.01.08", "01:01")))
    assert raw.columns == [*dwx.RAW_COLUMNS, "source_line"]
    assert raw["source_line"].to_list() == [2, 3]
    assert raw.schema["TickVolume"] == pl.Int64
    assert raw.schema["Open"] == pl.Float64


def test_parse_csv_rejects_wrong_header() -> None:
    with pytest.raises(SchemaError, match="cabecera"):
        dwx.parse_csv("Date,Time,Open\n2018.01.08,01:00,50\n")


def test_parse_csv_rejects_nulls() -> None:
    with pytest.raises(SchemaError, match="nulos"):
        dwx.parse_csv(_csv("2018.01.08,01:00,50.0,,49.9,50.0,10,0.04"))


def test_parse_csv_rejects_non_numeric() -> None:
    with pytest.raises(SchemaError, match="CSV inválido"):
        dwx.parse_csv(_csv("2018.01.08,01:00,abc,50.1,49.9,50.0,10,0.04"))


def test_parse_csv_rejects_empty() -> None:
    with pytest.raises(SchemaError, match="sin filas"):
        dwx.parse_csv(HEADER + "\n")


# ---------------------------------------------------------------- conversión


def test_winter_conversion_by_hand(cfg: DarwinexConfig) -> None:
    # Servidor lun 2018-01-08 01:00 → NY dom 18:00 EST (UTC-5) → 23:00 UTC.
    out, _ = _norm(cfg, _row("2018.01.08", "01:00"))
    row = out.row(0, named=True)
    assert row["bar_start_utc"] == datetime(2018, 1, 7, 23, 0, tzinfo=UTC)
    assert row["bar_end_utc"] == datetime(2018, 1, 7, 23, 1, tzinfo=UTC)
    assert row["available_at"] == row["bar_end_utc"]
    assert row["trade_date"] == date(2018, 1, 8)


def test_summer_conversion_by_hand(cfg: DarwinexConfig) -> None:
    # Servidor lun 2018-07-09 01:00 → NY dom 18:00 EDT (UTC-4) → 22:00 UTC.
    out, _ = _norm(cfg, _row("2018.07.09", "01:00"))
    assert out["bar_start_utc"][0] == datetime(2018, 7, 8, 22, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("srv_date", "expected_utc"),
    [
        # Domingo 2018-03-11: cambio a EDT a las 02:00 ET. Apertura 18:00 EDT = 22:00 UTC.
        ("2018.03.12", datetime(2018, 3, 11, 22, 0, tzinfo=UTC)),
        # Domingo 2018-11-04: vuelta a EST a las 02:00 ET. Apertura 18:00 EST = 23:00 UTC.
        ("2018.11.05", datetime(2018, 11, 4, 23, 0, tzinfo=UTC)),
    ],
)
def test_dst_sundays(cfg: DarwinexConfig, srv_date: str, expected_utc: datetime) -> None:
    out, _ = _norm(cfg, _row(srv_date, "01:00"))
    assert out["bar_start_utc"][0] == expected_utc


def test_dst_week_keeps_minute_spacing(cfg: DarwinexConfig) -> None:
    # Viernes 2018-03-09 16:59 EST y lunes 2018-03-12 09:00 EDT: el desfase cambia en medio.
    out, _ = _norm(cfg, _row("2018.03.09", "23:59"), _row("2018.03.12", "16:00"))
    assert out["bar_start_utc"].to_list() == [
        datetime(2018, 3, 9, 21, 59, tzinfo=UTC),
        datetime(2018, 3, 12, 13, 0, tzinfo=UTC),
    ]


def test_nonexistent_ny_time_raises(cfg: DarwinexConfig) -> None:
    # Servidor 2018-03-11 09:30 → NY 02:30 del domingo del salto: no existe.
    with pytest.raises(CalendarError, match="DST"):
        _norm(cfg, _row("2018.03.11", "09:30"))


def test_ambiguous_ny_time_raises(cfg: DarwinexConfig) -> None:
    # Servidor 2018-11-04 08:30 → NY 01:30 del domingo de vuelta: ambigua.
    with pytest.raises(CalendarError, match="DST"):
        _norm(cfg, _row("2018.11.04", "08:30"))


def test_invalid_datetime_text_raises(cfg: DarwinexConfig) -> None:
    with pytest.raises(SchemaError, match="fecha/hora"):
        _norm(cfg, _row("2018.02.30", "01:00"))  # formato válido, fecha imposible


def test_end_timestamp_convention_not_implemented(cfg: DarwinexConfig) -> None:
    data = cfg.model_dump(by_alias=True)
    data["bar_timestamp_is_start"]["value"] = False
    with pytest.raises(SchemaError, match="inicio"):
        _norm(DarwinexConfig.model_validate(data), _row("2018.01.08", "01:00"))


# ---------------------------------------------------------------- trade_date y sesión


@pytest.mark.parametrize(
    ("srv", "expected"),
    [
        (("2018.01.09", "00:59"), None),  # 17:59 ET: pausa
        (("2018.01.09", "01:00"), date(2018, 1, 9)),  # lun 18:00 ET → martes
        (("2018.01.08", "23:59"), date(2018, 1, 8)),  # lun 16:59 ET → lunes
        (("2018.01.08", "07:00"), date(2018, 1, 8)),  # lun 00:00 ET → lunes
    ],
)
def test_trade_date(cfg: DarwinexConfig, srv: tuple[str, str], expected: date | None) -> None:
    # Barra base válida (lun 2018-01-08 02:00 servidor) antes del caso, para no superar el umbral.
    out, stats = _norm(_relaxed(cfg), _row("2018.01.08", "02:00"), _row(*srv))
    target = out.filter(pl.col("source_line") == 3)
    if expected is None:
        assert target.height == 0
        assert stats.dropped == {"daily_break": 1}
    else:
        assert target["trade_date"].to_list() == [expected]


@pytest.mark.parametrize(
    ("srv", "reason"),
    [
        (("2018.01.09", "00:00"), "daily_break"),  # mar 17:00 ET
        (("2018.01.13", "00:00"), "outside_week"),  # vie 17:00 ET
        (("2018.01.13", "12:00"), "outside_week"),  # sáb 05:00 ET
        (("2018.01.15", "00:59"), "outside_week"),  # dom 17:59 ET
    ],
)
def test_bars_outside_session_dropped_and_counted(
    cfg: DarwinexConfig, srv: tuple[str, str], reason: str
) -> None:
    rows = [_row("2018.01.08", f"{h:02d}:00") for h in range(1, 23)]  # 22 barras válidas
    out, stats = _norm(_relaxed(cfg), *rows, _row(*srv))
    assert out.height == 22
    assert stats.rows_in == 23
    assert stats.dropped == {reason: 1}


def test_drop_threshold_exceeded_raises(cfg: DarwinexConfig) -> None:
    with pytest.raises(SchemaError, match="descartadas"):
        _norm(cfg, _row("2018.01.08", "02:00"), _row("2018.01.09", "00:30"))


# ---------------------------------------------------------------- validación dura


@pytest.mark.parametrize(
    ("row", "match"),
    [
        (_row("2018.01.08", "02:00", o=0.0, lo=0.0), "precio"),
        (_row("2018.01.08", "02:00", h=50.0, c=50.05), "High"),
        (_row("2018.01.08", "02:00", lo=50.01, o=50.0), "Low"),
        (_row("2018.01.08", "02:00", sp=-0.01), "spread"),
        (_row("2018.01.08", "02:00", tv=0), "TickVolume"),
    ],
)
def test_hard_validation(cfg: DarwinexConfig, row: str, match: str) -> None:
    with pytest.raises(SchemaError, match=match):
        _norm(cfg, row)


def test_duplicate_raises(cfg: DarwinexConfig) -> None:
    with pytest.raises(SchemaError, match="orden"):
        _norm(cfg, _row("2018.01.08", "02:00"), _row("2018.01.08", "02:00"))


def test_unsorted_raises(cfg: DarwinexConfig) -> None:
    with pytest.raises(SchemaError, match="orden"):
        _norm(cfg, _row("2018.01.08", "02:01"), _row("2018.01.08", "02:00"))


def test_no_filling_of_missing_minutes(cfg: DarwinexConfig) -> None:
    out, _ = _norm(cfg, _row("2018.01.08", "02:00"), _row("2018.01.08", "02:05"))
    assert out.height == 2


def test_output_schema_validated(cfg: DarwinexConfig) -> None:
    out, _ = _norm(cfg, _row("2018.01.08", "02:00"))
    assert out.columns == dwx.OUTPUT_SCHEMA.names()
    dwx.validate_output(out, cfg)
    with pytest.raises(SchemaError, match="esquema"):
        dwx.validate_output(out.with_columns(tick_volume=pl.lit(-1, dtype=pl.Int64)), cfg)
    with pytest.raises(SchemaError, match="available_at"):
        dwx.validate_output(out.with_columns(available_at=pl.col("bar_start_utc")), cfg)
    with pytest.raises(SchemaError, match="trade_date"):
        dwx.validate_output(out.with_columns(trade_date=pl.lit(date(2019, 1, 1))), cfg)
    # 2018-01-08 02:00 servidor = 2018-01-07 19:00 ET → trade_date 2018-01-08;
    # el día anterior es incoherente.
    with pytest.raises(SchemaError, match="trade_date"):
        dwx.validate_output(out.with_columns(trade_date=pl.lit(date(2018, 1, 7))), cfg)


# ---------------------------------------------------------------- ejecución completa


def _setup_raw(tmp_path: Path, cfg: DarwinexConfig, text: str) -> DarwinexConfig:
    raw = tmp_path / "raw" / cfg.input.rel_path
    raw.parent.mkdir(parents=True)
    raw.write_text(text, encoding="utf-8")
    data = cfg.model_dump(by_alias=True)
    data["input"]["sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return DarwinexConfig.model_validate(data)


def _run(tmp_path: Path, cfg: DarwinexConfig) -> dwx.NormalizeResult:
    return dwx.run_normalize(
        raw_root=tmp_path / "raw",
        out_root=tmp_path / "normalized",
        manifest_dir=tmp_path / "manifests",
        cfg=cfg,
        commit="abc123",
    )


TWO_YEARS = _csv(
    _row("2018.12.31", "02:00"),
    _row("2019.01.01", "01:00"),  # lun 31-dic 18:00 ET → trade_date 2019-01-01
    _row("2019.01.02", "03:00"),
)


def test_run_writes_readonly_outputs_and_manifest(tmp_path: Path, cfg: DarwinexConfig) -> None:
    c = _setup_raw(tmp_path, cfg, TWO_YEARS)
    res = _run(tmp_path, c)
    assert res.created is True
    base = tmp_path / "normalized" / "darwinex_xti_m1" / c.version
    files = sorted(p.name for p in base.iterdir())
    assert files == ["2018.parquet", "2019.parquet"]
    for p in base.iterdir():
        assert not os.stat(p).st_mode & stat.S_IWUSR
    assert pl.read_parquet(base / "2019.parquet").height == 2
    mpath = tmp_path / "manifests" / "normalized" / "darwinex_xti_m1" / f"{c.version}.json"
    man = json.loads(mpath.read_text())
    assert man["inputs"] == {c.input.rel_path: c.input.sha256}
    assert man["params"]["server_offset_vs_new_york_hours"]["value"] == 7
    assert man["rows_in"] == 3
    assert man["rows_out"] == 3
    assert man["dropped"] == {}
    assert man["code_commit"] == "abc123"
    assert set(man["outputs"]) == {"2018.parquet", "2019.parquet"}
    assert man["bar_start_utc_min"] == "2018-12-31 00:00:00+00:00"


def test_second_run_verifies_and_does_not_rewrite(tmp_path: Path, cfg: DarwinexConfig) -> None:
    c = _setup_raw(tmp_path, cfg, TWO_YEARS)
    first = _run(tmp_path, c)
    second = _run(tmp_path, c)
    assert second.created is False
    assert second.outputs == first.outputs
    assert second.stats == first.stats


def test_changed_config_same_version_refused(tmp_path: Path, cfg: DarwinexConfig) -> None:
    # Regresión (revisión adversarial, hallazgo 1): cambiar el desfase sin cambiar la versión.
    c = _setup_raw(tmp_path, cfg, TWO_YEARS)
    _run(tmp_path, c)
    data = c.model_dump(by_alias=True)
    data["server_offset_vs_new_york_hours"]["value"] = 6
    with pytest.raises(RawIntegrityError, match="otros parámetros"):
        _run(tmp_path, DarwinexConfig.model_validate(data))


def test_input_checksum_mismatch_raises(tmp_path: Path, cfg: DarwinexConfig) -> None:
    c = _setup_raw(tmp_path, cfg, TWO_YEARS)
    data = c.model_dump(by_alias=True)
    data["input"]["sha256"] = "f" * 64
    with pytest.raises(RawIntegrityError, match="sha256"):
        _run(tmp_path, DarwinexConfig.model_validate(data))


def test_missing_input_raises(tmp_path: Path, cfg: DarwinexConfig) -> None:
    with pytest.raises(RawIntegrityError, match="no existe"):
        _run(tmp_path, cfg)


def test_deterministic_hash(tmp_path: Path, cfg: DarwinexConfig) -> None:
    a = _run(tmp_path / "a", _setup_raw(tmp_path / "a", cfg, TWO_YEARS))
    b = _run(tmp_path / "b", _setup_raw(tmp_path / "b", cfg, TWO_YEARS))
    assert a.outputs == b.outputs


def test_dirty_tree_refused(tmp_path: Path, cfg: DarwinexConfig) -> None:
    c = _setup_raw(tmp_path, cfg, TWO_YEARS)
    with pytest.raises(RawIntegrityError, match="dirty"):
        dwx.run_normalize(
            raw_root=tmp_path / "raw",
            out_root=tmp_path / "normalized",
            manifest_dir=tmp_path / "manifests",
            cfg=c,
            commit="abc-dirty",
        )


# ---------------------------------------------------------------- revisión adversarial


@pytest.mark.parametrize(
    "row",
    [
        "2018.01.08,02:00,50.0,inf,49.9,50.05,10,0.04",
        "2018.01.08,02:00,50.0,50.1,49.9,50.05,10,inf",
        "2018.01.08,02:00,NaN,50.1,49.9,50.05,10,0.04",
    ],
)
def test_non_finite_rejected(cfg: DarwinexConfig, row: str) -> None:
    with pytest.raises(SchemaError, match="no finito"):
        _norm(cfg, row)


def test_non_finite_rejected_even_in_dropped_bar(cfg: DarwinexConfig) -> None:
    with pytest.raises(SchemaError, match="no finito"):
        _norm(
            _relaxed(cfg),
            _row("2018.01.08", "02:00"),
            "2018.01.09,00:30,NaN,50.1,49.9,50.0,10,0.04",
        )


@pytest.mark.parametrize(
    ("d", "t", "match"),
    [
        ("18.01.08", "02:00", "YYYY.MM.DD"),
        ("2018.1.8", "02:00", "YYYY.MM.DD"),
        ("2018.01.08", "2:0", "HH:MM"),
        ("2018.01.08", " 02:00", "HH:MM"),
        ("2009.12.31", "02:00", "rango plausible"),
        ("2026.10.17", "02:00", "rango plausible"),
    ],
)
def test_strict_datetime_format(cfg: DarwinexConfig, d: str, t: str, match: str) -> None:
    with pytest.raises(SchemaError, match=match):
        _norm(cfg, _row(d, t))


def test_drop_threshold_boundary_is_inclusive(cfg: DarwinexConfig) -> None:
    # Exactamente max_drop_fraction (0,5) se acepta; por encima, error.
    out, stats = _norm(_relaxed(cfg), _row("2018.01.08", "02:00"), _row("2018.01.09", "00:30"))
    assert out.height == 1
    assert stats.dropped == {"daily_break": 1}


@pytest.mark.parametrize(
    ("srv", "kept"),
    [
        (("2018.01.12", "23:59"), True),  # vie 16:59 ET: última barra válida
        (("2018.01.15", "00:59"), False),  # dom 17:59 ET: aún cerrado
        (("2018.01.15", "01:00"), True),  # dom 18:00 ET: apertura
    ],
)
def test_week_boundaries(cfg: DarwinexConfig, srv: tuple[str, str], kept: bool) -> None:
    out, _ = _norm(_relaxed(cfg), _row("2018.01.12", "02:00"), _row(*srv))
    assert (out.filter(pl.col("source_line") == 3).height == 1) is kept


@pytest.mark.parametrize(
    ("srv", "expected"),
    [
        (("2018.03.12", "01:00"), date(2018, 3, 12)),  # dom 18:00 EDT tras el salto
        (("2018.11.05", "01:00"), date(2018, 11, 5)),  # dom 18:00 EST tras la vuelta
        (("2018.03.10", "00:59"), None),  # vie 17:59 EST: fuera
    ],
)
def test_trade_date_in_dst_weeks(
    cfg: DarwinexConfig, srv: tuple[str, str], expected: date | None
) -> None:
    out, _ = _norm(_relaxed(cfg), _row("2018.03.09", "02:00"), _row(*srv))
    got = out.filter(pl.col("source_line") == 3)["trade_date"].to_list()
    assert got == ([] if expected is None else [expected])


def _relaxed_full() -> DarwinexConfig:
    data = load_darwinex_config(CONFIG_DIR).model_dump(by_alias=True)
    data["max_drop_fraction"]["value"] = 0.999999
    return DarwinexConfig.model_validate(data)


RELAXED_FULL = _relaxed_full()


@settings(max_examples=150, deadline=None)
@given(st.datetimes(min_value=datetime(2015, 1, 1), max_value=datetime(2026, 9, 30)))
def test_property_utc_consistent_with_ny_and_trade_date(server: datetime) -> None:
    # Propiedad: si la barra se conserva, UTC → NY devuelve hora servidor - 7 h y la barra cae
    # en su ventana de trade_date; si no, se descarta por pausa/fin de semana o falla por DST.
    server = server.replace(second=0, microsecond=0)
    anchor = _row("2014.12.30", "02:00")  # mar 2014-12-30 02:00 servidor: barra válida previa
    row = _row(server.strftime("%Y.%m.%d"), server.strftime("%H:%M"))
    try:
        out, stats = dwx.normalize_bars(dwx.parse_csv(_csv(anchor, row)), RELAXED_FULL)
    except CalendarError:
        return
    target = out.filter(pl.col("source_line") == 3)
    if target.height == 0:
        assert sum(stats.dropped.values()) == 1
        return
    ny = target["bar_start_utc"][0].astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    assert ny == server - timedelta(hours=7)
    td = target["trade_date"][0]
    assert datetime.combine(td - timedelta(days=1), time(18)) <= ny < datetime.combine(td, time(17))
    assert td.weekday() < 5

"""Tests del COT point-in-time (FASE_02_fundamentales.md §B).

Ficheros SINTÉTICOS con el formato CFTC; fechas reales calculadas a mano.
"""

import io
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from test_federal_calendar import STATUTE as STATUTE_TEXT
from wti_news_alpha.config import CONFIG_DIR, CftcCotConfig, load_cftc_cot_config
from wti_news_alpha.errors import RawIntegrityError, SchemaError, UnverifiedValueError
from wti_news_alpha.ingestion.rawstore import ManifestEntry, RawStore, _sha256
from wti_news_alpha.normalization import cftc_cot as cot
from wti_news_alpha.normalization.federal_calendar import FederalCalendar

D = date.fromisoformat
COLS = list(cot.POSITION_COLUMNS)


def _approved(cfg: CftcCotConfig) -> CftcCotConfig:
    data = cfg.model_dump(by_alias=True)
    for k in ("api_created_at_margin_minutes", "excluded_as_of"):
        data[k]["status"] = "approved"
    return CftcCotConfig.model_validate(data)


@pytest.fixture
def cfg() -> CftcCotConfig:
    return _approved(load_cftc_cot_config(CONFIG_DIR))


@pytest.fixture
def cal() -> FederalCalendar:
    return FederalCalendar.build(2014, 2026, {D("2018-12-05"): "eo_closure"})


def _txt(rows: list[tuple[str, str]], extra_code: bool = True) -> str:
    header = [
        "Market_and_Exchange_Names",
        "Report_Date_as_YYYY-MM-DD",
        "CFTC_Contract_Market_Code",
        *COLS,
    ]
    lines = [",".join(f'"{h}"' for h in header)]
    for i, (as_of, code) in enumerate(rows):
        vals = [str(1000 + i)] * len(COLS)
        lines.append(
            ",".join(['"WTI-PHYSICAL - NYMEX"', as_of, f'"{code}"', *[f" {v}" for v in vals]])
        )
    if extra_code:
        lines.append(",".join(['"OTHER"', rows[0][0], '"06765A"', *["1"] * len(COLS)]))
    return "\n".join(lines) + "\n"


def _zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("f_year.txt", text)
    return buf.getvalue()


# ---------------------------------------------------------------- parseo


def test_parse_zip_filters_code_and_types() -> None:
    df = cot.parse_zip(_zip(_txt([("2018-11-27", "067651"), ("2018-12-04", "067651")])), "067651")
    assert df["as_of_date"].to_list() == [D("2018-11-27"), D("2018-12-04")]
    assert df["open_interest"].to_list() == [1000, 1001]
    assert df.schema["mm_long"] == pl.Int64


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (_zip(_txt([("2018-11-27", "067651")]).replace(" 1000", " abc", 1)), "entero"),
        (_zip(_txt([("2018-11-27", "067651"), ("2018-11-27", "067651")])), "duplicad"),
        (_zip(_txt([("2018-11-29", "067651")])), "martes"),
        (_zip(_txt([("2018-11-27", "999999")], extra_code=False)), "sin filas"),
    ],
)
def test_parse_zip_errors(content: bytes, match: str) -> None:
    with pytest.raises(SchemaError, match=match):
        cot.parse_zip(content, "067651")


def test_parse_zip_monday_as_of_allowed_only_before_holiday_tuesday() -> None:
    # 2018-12-24 (lunes; martes 25 festivo) es válido.
    df = cot.parse_zip(_zip(_txt([("2018-12-24", "067651")])), "067651")
    assert df.height == 1


def test_parse_zip_two_members_raises() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "x")
        z.writestr("b.txt", "y")
    with pytest.raises(SchemaError, match="miembro"):
        cot.parse_zip(buf.getvalue(), "067651")


def _sys(*rows: tuple[str, str]) -> str:
    return json.dumps(
        [
            {
                ":created_at": c,
                "id": f"{a[2:4]}{a[5:7]}{a[8:10]}067651F",
                "report_date_as_yyyy_mm_dd": f"{a}T00:00:00.000",
            }
            for a, c in rows
        ]
    )


def test_parse_created_at() -> None:
    out = cot.parse_created_at(_sys(("2026-10-06", "2026-10-09T19:30:07.123Z")), "067651")
    assert out == {D("2026-10-06"): datetime(2026, 10, 9, 19, 30, 7, 123000, tzinfo=UTC)}


def test_parse_created_at_duplicate_raises() -> None:
    with pytest.raises(SchemaError, match="duplicad"):
        cot.parse_created_at(
            _sys(("2026-10-06", "2026-10-09T19:30:07Z"), ("2026-10-06", "2026-10-09T19:31:07Z")),
            "067651",
        )


# ---------------------------------------------------------------- disponibilidad


def _avail(
    cfg: CftcCotConfig,
    cal: FederalCalendar,
    as_of: str,
    created: dict[date, datetime] | None = None,
) -> tuple[date, datetime, str] | None:
    rows = cot.release_rows([D(as_of)], created or {}, cal, cfg)
    if rows.height == 0:
        return None
    r = rows.row(0, named=True)
    return r["release_date"], r["available_at"], r["time_status"]


def test_rule_date_normal_week(cfg: CftcCotConfig, cal: FederalCalendar) -> None:
    # as-of mar 2018-11-27 → vie 30, 18:00 EST = 23:00 UTC.
    assert _avail(cfg, cal, "2018-11-27") == (
        D("2018-11-30"),
        datetime(2018, 11, 30, 23, 0, tzinfo=UTC),
        "rule_date",
    )


def test_rule_date_with_holiday_is_unconfirmed(cfg: CftcCotConfig, cal: FederalCalendar) -> None:
    # as-of 2018-12-04: cierre del 5 → regla lun 10 (desplazamiento no confirmado) → mar 11 18:00.
    assert _avail(cfg, cal, "2018-12-04") == (
        D("2018-12-10"),
        datetime(2018, 12, 11, 23, 0, tzinfo=UTC),
        "rule_date_unconfirmed_shift",
    )


def test_verified_release(cfg: CftcCotConfig, cal: FederalCalendar) -> None:
    # Cierre 2018-19: as-of 2018-12-24 publicado el vie 2019-02-01 → 18:00 EST.
    assert _avail(cfg, cal, "2018-12-24") == (
        D("2019-02-01"),
        datetime(2019, 2, 1, 23, 0, tzinfo=UTC),
        "verified_release",
    )


def test_catch_up_weeks_excluded(cfg: CftcCotConfig, cal: FederalCalendar) -> None:
    assert _avail(cfg, cal, "2019-01-15") is None
    assert _avail(cfg, cal, "2019-03-05") is not None


def test_api_created_at_with_margin(cfg: CftcCotConfig, cal: FederalCalendar) -> None:
    created = {D("2024-03-05"): datetime(2024, 3, 8, 20, 30, 10, tzinfo=UTC)}
    assert _avail(cfg, cal, "2024-03-05", created) == (
        D("2024-03-08"),
        datetime(2024, 3, 8, 20, 35, 10, tzinfo=UTC),
        "api_created_at",
    )


def test_api_missing_created_at_raises(cfg: CftcCotConfig, cal: FederalCalendar) -> None:
    with pytest.raises(SchemaError, match="created_at"):
        _avail(cfg, cal, "2024-03-05", {})


def test_api_created_at_disagrees_with_verified_raises(
    cfg: CftcCotConfig, cal: FederalCalendar
) -> None:
    created = {D("2023-01-31"): datetime(2023, 2, 23, 20, 30, tzinfo=UTC)}
    with pytest.raises(SchemaError, match="verificad"):
        _avail(cfg, cal, "2023-01-31", created)


def test_created_at_before_as_of_raises(cfg: CftcCotConfig, cal: FederalCalendar) -> None:
    created = {D("2024-03-05"): datetime(2024, 3, 4, 20, 30, tzinfo=UTC)}
    with pytest.raises(SchemaError, match="antes"):
        _avail(cfg, cal, "2024-03-05", created)


def test_unapproved_params_block(cal: FederalCalendar) -> None:
    with pytest.raises(UnverifiedValueError):
        cot.release_rows([D("2018-11-27")], {}, cal, load_cftc_cot_config(CONFIG_DIR))


# ---------------------------------------------------------------- integración


def _write_raw(store: RawStore, source: str, rel: str, content: bytes) -> None:
    path = store.raw_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    store._append(
        ManifestEntry(source, rel, "https://x.invalid", {}, "t", 200, "x", _sha256(path), 1, "c")
    )


def test_run_end_to_end(tmp_path: Path, cfg: CftcCotConfig) -> None:
    store = RawStore(tmp_path / "raw", tmp_path / "manifests", "c")
    _write_raw(
        store,
        "cftc",
        "cftc/history/fut_disagg_txt_2018.zip",
        _zip(_txt([("2018-11-27", "067651"), ("2018-12-04", "067651")])),
    )
    _write_raw(
        store,
        "cftc",
        "cftc/history/fut_disagg_txt_2024.zip",
        _zip(_txt([("2024-03-05", "067651")])),
    )
    _write_raw(
        store,
        "cftc",
        "cftc/socrata/sys.json",
        _sys(("2024-03-05", "2024-03-08T20:30:10Z")).encode(),
    )
    _write_raw(store, "federal_calendar", "federal_calendar/usc.htm", STATUTE_TEXT.encode())
    eos = {
        "count": 1,
        "results": [
            {"title": "Providing for the Closing of Executive Departments on December 5, 2018"}
        ],
    }
    _write_raw(store, "federal_calendar", "federal_calendar/eos.json", json.dumps(eos).encode())
    data = cfg.model_dump(by_alias=True)
    data["inputs"] = {
        "zips_glob": "cftc/history/fut_disagg_txt_*.zip",
        "socrata_systemfields": "cftc/socrata/sys.json",
        "statute": "federal_calendar/usc.htm",
        "closure_eos": "federal_calendar/eos.json",
    }
    c = CftcCotConfig.model_validate(data)
    res = cot.run_normalize(
        raw_root=tmp_path / "raw",
        out_root=tmp_path / "norm",
        manifest_dir=tmp_path / "manifests",
        cfg=c,
        commit="abc",
    )
    out = pl.read_parquet(tmp_path / "norm" / "cftc_cot" / c.version / "cot.parquet")
    assert out["time_status"].to_list() == [
        "rule_date",
        "rule_date_unconfirmed_shift",
        "api_created_at",
    ]
    assert out["revision_risk"].to_list() == [True] * 3
    assert res.manifest["time_status_counts"] == {
        "api_created_at": 1,
        "rule_date": 1,
        "rule_date_unconfirmed_shift": 1,
    }
    assert res.manifest["excluded_as_of"] == 0


def test_monday_as_of_requires_tuesday_holiday(cfg: CftcCotConfig, cal: FederalCalendar) -> None:
    with pytest.raises(SchemaError, match="lunes"):
        _avail(cfg, cal, "2018-11-26")


def test_verified_release_not_after_as_of_raises(cfg: CftcCotConfig, cal: FederalCalendar) -> None:
    data = cfg.model_dump(by_alias=True)
    data["verified_releases"]["value"] = {"2018-11-27": "2018-11-27"}
    with pytest.raises(SchemaError, match="no posterior"):
        _avail(CftcCotConfig.model_validate(data), cal, "2018-11-27")


def test_parse_created_at_ignores_other_rows() -> None:
    rows = [
        {
            ":created_at": "2026-10-09T19:30:07Z",
            "id": "261006067651C",
            "report_date_as_yyyy_mm_dd": "2026-10-06T00:00:00.000",
        },
        {
            ":created_at": "2026-10-09T19:30:07Z",
            "id": "26100606765AF",
            "report_date_as_yyyy_mm_dd": "2026-10-06T00:00:00.000",
        },
    ]
    assert cot.parse_created_at(json.dumps(rows), "067651") == {}


def _cfg_inputs(cfg: CftcCotConfig) -> CftcCotConfig:
    data = cfg.model_dump(by_alias=True)
    data["inputs"] = {
        "zips_glob": "cftc/history/fut_disagg_txt_*.zip",
        "socrata_systemfields": "cftc/socrata/sys.json",
        "statute": "federal_calendar/usc.htm",
        "closure_eos": "federal_calendar/eos.json",
    }
    return CftcCotConfig.model_validate(data)


def test_run_without_zips_raises(tmp_path: Path, cfg: CftcCotConfig) -> None:
    with pytest.raises(RawIntegrityError, match="no hay ficheros"):
        cot.run_normalize(
            raw_root=tmp_path,
            out_root=tmp_path / "n",
            manifest_dir=tmp_path / "m",
            cfg=_cfg_inputs(cfg),
            commit="abc",
        )


def test_run_duplicate_as_of_across_zips_raises(tmp_path: Path, cfg: CftcCotConfig) -> None:
    store = RawStore(tmp_path / "raw", tmp_path / "manifests", "c")
    for y in ("2018", "2019"):
        _write_raw(
            store,
            "cftc",
            f"cftc/history/fut_disagg_txt_{y}.zip",
            _zip(_txt([("2018-11-27", "067651")])),
        )
    _write_raw(store, "cftc", "cftc/socrata/sys.json", b"[]")
    _write_raw(store, "federal_calendar", "federal_calendar/usc.htm", STATUTE_TEXT.encode())
    _write_raw(
        store,
        "federal_calendar",
        "federal_calendar/eos.json",
        json.dumps({"count": 0, "results": []}).encode(),
    )
    with pytest.raises(SchemaError, match="repetidos"):
        cot.run_normalize(
            raw_root=tmp_path / "raw",
            out_root=tmp_path / "n",
            manifest_dir=tmp_path / "manifests",
            cfg=_cfg_inputs(cfg),
            commit="abc",
        )

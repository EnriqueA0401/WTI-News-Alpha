"""Tests del calendario y valores WPSR point-in-time (FASE_02_fundamentales.md §A).

HTML/JSON SINTÉTICOS con el formato de las fuentes; fechas reales donde se citan.
"""

import json
from datetime import UTC, date, datetime, time
from pathlib import Path

import polars as pl
import pytest

from wti_news_alpha.config import CONFIG_DIR, EiaWpsrConfig, load_eia_wpsr_config
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.ingestion.rawstore import ManifestEntry, RawStore, _sha256
from wti_news_alpha.normalization import eia_wpsr as w

D = date.fromisoformat


@pytest.fixture
def cfg() -> EiaWpsrConfig:
    return load_eia_wpsr_config(CONFIG_DIR)


# ---------------------------------------------------------------- parseo


def _index(*days: str) -> str:
    links = "".join(
        f'<a href="/petroleum/supply/weekly/archive/{d[:4]}/{d.replace("-", "_")}/wpsr.php">x</a>'
        for d in days
    )
    return f"<html>{links}{links}</html>"


def test_parse_archive_index_sorted_unique() -> None:
    assert w.parse_archive_index(_index("2015-01-14", "2015-01-07")) == [
        D("2015-01-07"),
        D("2015-01-14"),
    ]


def test_parse_archive_index_empty_raises() -> None:
    with pytest.raises(SchemaError, match="índice"):
        w.parse_archive_index("<html></html>")


def _fred(*days: str, count: int | None = None) -> str:
    rows = [{"release_id": 212, "date": d} for d in days]
    return json.dumps({"count": len(rows) if count is None else count, "release_dates": rows})


def test_parse_fred_dates() -> None:
    assert w.parse_fred_release_dates(_fred("2015-01-07", "2015-01-14")) == {
        D("2015-01-07"),
        D("2015-01-14"),
    }


def test_parse_fred_dates_truncated_raises() -> None:
    with pytest.raises(SchemaError, match="count"):
        w.parse_fred_release_dates(_fred("2015-01-07", count=2))


def _schedule(*rows: tuple[str, str, str, str]) -> str:
    body = "".join(
        f'<tr class="past-holiday"><th scope="row">{we}</th><td>{rd}</td><td>{day}</td>'
        f"<td>{t}</td><td>Holiday</td></tr>"
        for we, rd, day, t in rows
    )
    head = '<table class="basic-table schedule"><thead><tr><th>h</th></tr></thead>'
    return f"{head}<tbody>{body}</tbody></table>"


def test_parse_schedule_exceptions() -> None:
    html = _schedule(
        ("December 27, 2024", "January 2, 2025", "Thursday", "11:00 a.m."),
        ("December 19, 2025", "December 29, 2025", "Monday", "5:00 p.m."),
        ("January 16, 2026", "January 22, 2026", "Thursday", "12:00 p.m."),
    )
    out = w.parse_schedule_exceptions(html)
    assert out == [
        w.ScheduleException(D("2024-12-27"), D("2025-01-02"), time(11, 0)),
        w.ScheduleException(D("2025-12-19"), D("2025-12-29"), time(17, 0)),
        w.ScheduleException(D("2026-01-16"), D("2026-01-22"), time(12, 0)),
    ]


@pytest.mark.parametrize(
    "row",
    [
        ("December 27, 2024", "January 2, 2025", "Wednesday", "11:00 a.m."),  # día incoherente
        ("December 27, 2024", "January 2, 2025", "Thursday", "11 o'clock"),  # hora ilegible
        ("Dec 27 2024", "January 2, 2025", "Thursday", "11:00 a.m."),  # fecha ilegible
    ],
)
def test_parse_schedule_bad_rows_raise(row: tuple[str, str, str, str]) -> None:
    with pytest.raises(SchemaError):
        w.parse_schedule_exceptions(_schedule(row))


def test_parse_schedule_empty_raises() -> None:
    with pytest.raises(SchemaError, match="excepciones"):
        w.parse_schedule_exceptions("<html></html>")


def test_parse_week_ending_page() -> None:
    assert w.parse_week_ending_page("<p>Data for week ending January 2, 2015</p>") == D(
        "2015-01-02"
    )
    assert w.parse_week_ending_page("<p>nothing</p>") is None


def _api(
    series: str, rows: list[tuple[str, object]], *, units: str = "MBBL", total: int | None = None
) -> str:
    data = [{"period": p, "series": series, "value": v, "units": units} for p, v in rows]
    return json.dumps({"response": {"total": len(data) if total is None else total, "data": data}})


def test_parse_api_series() -> None:
    df = w.parse_api_series(_api("WCESTUS1", [("2015-01-09", 1), ("2015-01-02", 2.5)]), "WCESTUS1")
    assert df.to_dicts() == [
        {"series_id": "WCESTUS1", "week_ending": D("2015-01-02"), "value": 2.5, "units": "MBBL"},
        {"series_id": "WCESTUS1", "week_ending": D("2015-01-09"), "value": 1.0, "units": "MBBL"},
    ]


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (_api("WCESTUS1", [("2015-01-08", 1)]), "viernes"),
        (_api("WCESTUS1", [("2015-01-09", 1), ("2015-01-09", 2)]), "duplicad"),
        (_api("WCESTUS1", [("2015-01-09", "1")]), "numérico"),
        (_api("WCESTUS1", [("2015-01-09", True)]), "numérico"),
        (_api("WCESTUS1", [("2015-01-09", None)]), "numérico"),
        (_api("OTHER", [("2015-01-09", 1)]), "serie"),
        (_api("WCESTUS1", [("2015-01-09", 1)], total=5000), "total"),
        (_api("WCESTUS1", []), "vacía"),
    ],
)
def test_parse_api_series_errors(text: str, match: str) -> None:
    with pytest.raises(SchemaError, match=match):
        w.parse_api_series(text, "WCESTUS1")


def test_parse_api_series_mixed_units_raises() -> None:
    data = [
        {"period": "2015-01-02", "series": "X", "value": 1, "units": "MBBL"},
        {"period": "2015-01-09", "series": "X", "value": 1, "units": "%"},
    ]
    with pytest.raises(SchemaError, match="unidades"):
        w.parse_api_series(json.dumps({"response": {"total": 2, "data": data}}), "X")


# ---------------------------------------------------------------- semana → publicación


def _assign(
    weeks: list[str], releases: list[str], shared: tuple[str, ...] = ()
) -> dict[date, date]:
    return w.assign_first_release(
        [D(x) for x in weeks],
        [D(x) for x in releases],
        min_lag=5,
        max_lag=12,
        shared={D(x) for x in shared},
    )


def test_assign_normal_and_holiday_thursday() -> None:
    # 2015-01-16 → jue 22 (MLK); 2015-01-23 → mié 28.
    got = _assign(
        ["2015-01-09", "2015-01-16", "2015-01-23"], ["2015-01-14", "2015-01-22", "2015-01-28"]
    )
    assert got == {
        D("2015-01-09"): D("2015-01-14"),
        D("2015-01-16"): D("2015-01-22"),
        D("2015-01-23"): D("2015-01-28"),
    }


def test_assign_monday_then_wednesday_2025() -> None:
    # Semana 12-19 → lun 12-29 (5:00 p.m.); semana 12-26 → mié 12-31. Nunca 12-26 → 12-29.
    got = _assign(["2025-12-19", "2025-12-26"], ["2025-12-29", "2025-12-31"])
    assert got == {D("2025-12-19"): D("2025-12-29"), D("2025-12-26"): D("2025-12-31")}


def test_assign_week_without_own_release() -> None:
    got = _assign(
        ["2022-06-10", "2022-06-17", "2022-06-24"], ["2022-06-15", "2022-06-29"], ("2022-06-29",)
    )
    assert got[D("2022-06-17")] == D("2022-06-29")
    assert got[D("2022-06-24")] == D("2022-06-29")


def test_assign_unexpected_shared_release_raises() -> None:
    with pytest.raises(SchemaError, match="2 semanas"):
        _assign(["2022-06-10", "2022-06-17", "2022-06-24"], ["2022-06-15", "2022-06-29"])


def test_assign_declared_shared_release_with_one_week_raises() -> None:
    with pytest.raises(SchemaError, match="compartida"):
        _assign(["2022-06-17"], ["2022-06-22"], ("2022-06-22",))


def test_assign_release_without_week_raises() -> None:
    with pytest.raises(SchemaError, match="sin semana"):
        _assign(["2015-01-09", "2015-01-23"], ["2015-01-14", "2015-01-21", "2015-01-28"])


def test_assign_lag_too_long_raises() -> None:
    with pytest.raises(SchemaError, match="retraso"):
        _assign(["2015-01-09"], ["2015-01-22"])


def test_assign_week_after_last_release_raises() -> None:
    with pytest.raises(SchemaError, match="sin publicación"):
        _assign(["2015-01-09", "2015-01-16"], ["2015-01-14"])


# ---------------------------------------------------------------- hora


EXC = [w.ScheduleException(D("2024-12-27"), D("2025-01-02"), time(11, 0))]


def _times(
    cfg: EiaWpsrConfig, assign: dict[date, date], fred: set[date]
) -> dict[date, tuple[datetime, str]]:
    df = w.release_table(assign, fred, EXC, cfg)
    return {
        r["release_date"]: (r["available_at"], r["time_status"]) for r in df.iter_rows(named=True)
    }


def test_release_times(cfg: EiaWpsrConfig) -> None:
    assign = {
        D("2015-01-02"): D("2015-01-07"),  # mié normal, EST
        D("2015-07-03"): D("2015-07-08"),  # mié normal, EDT
        D("2015-01-16"): D("2015-01-22"),  # jue desplazado
        D("2024-12-27"): D("2025-01-02"),  # excepción oficial 11:00
        D("2019-12-27"): D("2020-01-03"),  # vie, no está en FRED → lunes siguiente 18:00
    }
    fred = {D("2015-01-07"), D("2015-07-08"), D("2015-01-22"), D("2025-01-02")}
    got = _times(cfg, assign, fred)
    assert got[D("2015-01-07")] == (datetime(2015, 1, 7, 15, 30, tzinfo=UTC), "d8_standard")
    assert got[D("2015-07-08")] == (datetime(2015, 7, 8, 14, 30, tzinfo=UTC), "d8_standard")
    assert got[D("2015-01-22")] == (datetime(2015, 1, 22, 23, 0, tzinfo=UTC), "rpit_date_verified")
    assert got[D("2025-01-02")] == (datetime(2025, 1, 2, 16, 0, tzinfo=UTC), "official_exception")
    assert got[D("2020-01-03")] == (datetime(2020, 1, 6, 23, 0, tzinfo=UTC), "rpit_date_unverified")


def test_wednesday_with_long_lag_is_not_standard(cfg: EiaWpsrConfig) -> None:
    # Miércoles con retraso de 12 días (semana sin publicación propia) y la semana nueva con 5.
    assign = {D("2022-06-17"): D("2022-06-29"), D("2022-06-24"): D("2022-06-29")}
    got = _times(cfg, assign, {D("2022-06-29")})
    assert got[D("2022-06-29")][1] == "d8_standard"
    # Si la única semana tiene retraso 12 (miércoles) → no es calendario normal.
    got2 = _times(cfg, {D("2022-06-17"): D("2022-06-29")}, {D("2022-06-29")})
    assert got2[D("2022-06-29")][1] == "rpit_date_verified"


def test_exception_week_mismatch_raises(cfg: EiaWpsrConfig) -> None:
    with pytest.raises(SchemaError, match="excepción"):
        w.release_table({D("2024-12-20"): D("2025-01-02")}, {D("2025-01-02")}, EXC, cfg)


# ---------------------------------------------------------------- validación con páginas


def test_validate_pages() -> None:
    assign = {D("2015-01-02"): D("2015-01-07")}
    pages = {D("2015-01-07"): "week ending January 2, 2015", D("2016-12-07"): "sin dato"}
    assert w.validate_against_pages(assign, pages) == {"checked": 1, "without_week_ending": 1}
    with pytest.raises(SchemaError, match="página"):
        w.validate_against_pages(assign, {D("2015-01-07"): "week ending January 9, 2015"})


# ---------------------------------------------------------------- tabla de valores


def test_values_table_flags(cfg: EiaWpsrConfig) -> None:
    series = pl.DataFrame(
        {
            "series_id": ["WCESTUS1", "WCESTUS1", "WGTSTUS1"],
            "week_ending": [D("2016-09-30"), D("2016-10-07"), D("2016-09-30")],
            "value": [1.0, 2.0, 3.0],
            "units": ["MBBL"] * 3,
        }
    )
    releases = w.release_table(
        {D("2016-09-30"): D("2016-10-05"), D("2016-10-07"): D("2016-10-13")},
        {D("2016-10-05"), D("2016-10-13")},
        [],
        cfg,
    )
    assign = {D("2016-09-30"): D("2016-10-05"), D("2016-10-07"): D("2016-10-13")}
    out = w.values_table(series, assign, releases, cfg)
    assert out["revision_risk"].to_list() == [True, True, True]
    assert out.filter(pl.col("series_id") == "WCESTUS1")["known_redefinition"].to_list() == [
        True,
        False,
    ]
    assert out.filter(pl.col("series_id") == "WGTSTUS1")["known_redefinition"].to_list() == [False]
    assert (out["available_at"] > pl.Series([datetime(2016, 10, 1, tzinfo=UTC)] * 3)).all()


# ---------------------------------------------------------------- integración con raw sintético


def _write_raw(store: RawStore, source: str, rel: str, text: str) -> None:
    path = store.raw_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    store._append(
        ManifestEntry(
            source,
            rel,
            "https://example.invalid",
            {},
            "2026-10-15T00:00:00+00:00",
            200,
            "x",
            _sha256(path),
            1,
            "c",
        )
    )


DEFAULT_INDEX = ("2014-12-31", "2015-01-07", "2015-01-14")
DEFAULT_API = (("2014-12-26", 0), ("2015-01-02", 1), ("2015-01-09", 2), ("2015-01-16", 3))
DEFAULT_SCHED = (("December 27, 2024", "January 2, 2025", "Thursday", "11:00 a.m."),)


def _synthetic_raw(  # noqa: PLR0913 - variantes explícitas del raw sintético
    tmp_path: Path,
    cfg: EiaWpsrConfig,
    *,
    index: tuple[str, ...] = DEFAULT_INDEX,
    api: tuple[tuple[str, object], ...] = DEFAULT_API,
    sched: tuple[tuple[str, str, str, str], ...] = DEFAULT_SCHED,
    extra_page: str | None = None,
) -> EiaWpsrConfig:
    store = RawStore(tmp_path / "raw", tmp_path / "manifests", "c")
    _write_raw(store, "eia", "eia/web/idx.html", _index(*index))
    _write_raw(store, "fred", "fred/dates.json", _fred(*[d for d in index if d >= "2015"]))
    _write_raw(store, "eia", "eia/web/sched.html", _schedule(*sched))
    _write_raw(
        store, "eia", "eia/web/archive/wpsr_2015_01_07.php.html", "week ending January 2, 2015"
    )
    if extra_page is not None:
        _write_raw(store, "eia", extra_page, "x")
    _write_raw(store, "eia", "eia/api/a.json", _api("WCESTUS1", list(api)))
    data = cfg.model_dump(by_alias=True)
    data["inputs"] = {
        "archive_index": "eia/web/idx.html",
        "fred_release_dates": "fred/dates.json",
        "schedule": "eia/web/sched.html",
        "archive_pages_glob": "eia/web/archive/wpsr_*.php.html",
    }
    data["series"] = {"WCESTUS1": "eia/api/a.json"}
    return EiaWpsrConfig.model_validate(data)


def test_run_end_to_end(tmp_path: Path, cfg: EiaWpsrConfig) -> None:
    c = _synthetic_raw(tmp_path, cfg)
    res = w.run_normalize(
        raw_root=tmp_path / "raw",
        out_root=tmp_path / "norm",
        manifest_dir=tmp_path / "manifests",
        cfg=c,
        commit="abc",
    )
    assert res.created
    base = tmp_path / "norm" / "eia_wpsr" / c.version
    values = pl.read_parquet(base / "values.parquet")
    # 2014-12-26 → publicación 2014-12-31 (antes de la ventana); 2015-01-16 sin publicación aún.
    assert values["week_ending"].to_list() == [D("2015-01-02"), D("2015-01-09")]
    assert values["available_at"].to_list() == [
        datetime(2015, 1, 7, 15, 30, tzinfo=UTC),
        datetime(2015, 1, 14, 15, 30, tzinfo=UTC),
    ]
    man = res.manifest
    assert man["excluded_weeks"] == {"before_window": 1, "after_last_release": 1}
    assert man["time_status_counts"] == {"d8_standard": 2}
    assert man["pages"] == {"checked": 1, "without_week_ending": 0}
    assert set(man["inputs"]) == {
        "eia/web/idx.html",
        "fred/dates.json",
        "eia/web/sched.html",
        "eia/web/archive/wpsr_2015_01_07.php.html",
        "eia/api/a.json",
    }
    again = w.run_normalize(
        raw_root=tmp_path / "raw",
        out_root=tmp_path / "norm",
        manifest_dir=tmp_path / "manifests",
        cfg=c,
        commit="abc",
    )
    assert not again.created


def test_run_unregistered_input_raises(tmp_path: Path, cfg: EiaWpsrConfig) -> None:
    c = _synthetic_raw(tmp_path, cfg)
    (tmp_path / "raw" / "eia/web/archive/wpsr_2015_01_14.php.html").write_text(
        "x", encoding="utf-8"
    )
    with pytest.raises(RawIntegrityError, match="manifiesto"):
        w.run_normalize(
            raw_root=tmp_path / "raw",
            out_root=tmp_path / "norm",
            manifest_dir=tmp_path / "manifests",
            cfg=c,
            commit="abc",
        )


def _run_tmp(tmp_path: Path, c: EiaWpsrConfig) -> object:
    return w.run_normalize(
        raw_root=tmp_path / "raw",
        out_root=tmp_path / "norm",
        manifest_dir=tmp_path / "manifests",
        cfg=c,
        commit="abc",
    )


def test_run_window_release_without_week_raises(tmp_path: Path, cfg: EiaWpsrConfig) -> None:
    # Publicación 2015-01-21 sin semana 2015-01-16 en la API.
    c = _synthetic_raw(tmp_path, cfg, index=(*DEFAULT_INDEX, "2015-01-21"), api=DEFAULT_API[:3])
    with pytest.raises(SchemaError, match="sin semana"):
        _run_tmp(tmp_path, c)


def test_run_exception_not_in_index_raises(tmp_path: Path, cfg: EiaWpsrConfig) -> None:
    sched = (("January 2, 2015", "January 8, 2015", "Thursday", "12:00 p.m."),)
    c = _synthetic_raw(tmp_path, cfg, sched=sched)
    with pytest.raises(SchemaError, match="no están en el índice"):
        _run_tmp(tmp_path, c)


def test_run_unexpected_page_name_raises(tmp_path: Path, cfg: EiaWpsrConfig) -> None:
    c = _synthetic_raw(tmp_path, cfg, extra_page="eia/web/archive/wpsr_old.php.html")
    with pytest.raises(RawIntegrityError, match="nombre de página"):
        _run_tmp(tmp_path, c)


def test_values_table_week_without_release_raises(cfg: EiaWpsrConfig) -> None:
    series = pl.DataFrame(
        {"series_id": ["X"], "week_ending": [D("2016-09-30")], "value": [1.0], "units": ["MBBL"]}
    )
    releases = w.release_table({D("2016-09-23"): D("2016-09-28")}, {D("2016-09-28")}, [], cfg)
    with pytest.raises(SchemaError, match="join"):
        w.values_table(series, {D("2016-09-23"): D("2016-09-28")}, releases, cfg)


def test_values_table_available_before_week_raises(cfg: EiaWpsrConfig) -> None:
    series = pl.DataFrame(
        {"series_id": ["X"], "week_ending": [D("2016-09-30")], "value": [1.0], "units": ["MBBL"]}
    )
    assign = {D("2016-09-30"): D("2016-10-05")}
    releases = w.release_table(assign, {D("2016-10-05")}, [], cfg).with_columns(
        available_at=pl.lit(datetime(2016, 9, 1, tzinfo=UTC)).cast(pl.Datetime("us", "UTC"))
    )
    with pytest.raises(SchemaError, match="antes del cierre"):
        w.values_table(series, assign, releases, cfg)

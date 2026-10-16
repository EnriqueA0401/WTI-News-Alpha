"""Tests de vintages ALFRED point-in-time (FASE_02_fundamentales.md §C). JSON SINTÉTICO."""

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from test_federal_calendar import STATUTE
from wti_news_alpha.config import CONFIG_DIR, FredConfig, load_fred_config
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.ingestion.rawstore import ManifestEntry, RawStore, _sha256
from wti_news_alpha.normalization import fred_alfred as fa
from wti_news_alpha.normalization.federal_calendar import FederalCalendar

D = date.fromisoformat
CAL = FederalCalendar.build(2014, 2027, {D("2018-12-05"): "eo_closure"})
T1615 = time(16, 15)


def _win(lo: str, hi: str, rows: list[tuple[str, str, str, str]], **over: object) -> str:
    obs = [{"realtime_start": a, "realtime_end": b, "date": d, "value": v} for d, v, a, b in rows]
    body = {
        "realtime_start": lo,
        "realtime_end": hi,
        "observation_start": "2014-01-01",
        "output_type": 1,
        "count": len(obs),
        "limit": 100000,
        "observations": obs,
    }
    body.update(over)
    return json.dumps(body)


W15 = _win(
    "2015-01-01",
    "2015-12-31",
    [
        ("2014-12-31", "2.17", "2015-01-01", "2015-12-31"),  # recortado por la ventana
        ("2015-12-30", "2.31", "2015-12-31", "2015-12-31"),  # cruza el borde de ventana
        ("2015-12-31", ".", "2015-12-31", "2015-12-31"),
    ],
)
W16 = _win(
    "2016-01-01",
    "2016-12-31",
    [
        ("2014-12-31", "2.17", "2016-01-01", "2016-12-31"),
        ("2015-12-30", "2.31", "2016-01-01", "2016-03-01"),
        ("2015-12-30", "2.30", "2016-03-02", "2016-12-31"),  # revisión
        ("2015-12-31", "2.27", "2016-01-04", "2016-12-31"),  # se rellena el "."
    ],
)


def _merged(*texts: str, sid: str = "X") -> pl.DataFrame:
    return fa.merge_windows([fa.parse_window(t, sid) for t in texts])


def test_parse_window() -> None:
    lo, hi, df, obs_start = fa.parse_window(W15, "DGS10")
    assert (lo, hi, obs_start) == (D("2015-01-01"), D("2015-12-31"), "2014-01-01")
    assert df["value"].to_list() == [2.17, 2.31, None]


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (_win("2015-01-01", "2015-12-31", [], output_type=4), "output_type"),
        (
            _win(
                "2015-01-01",
                "2015-12-31",
                [("2015-01-02", "1", "2015-01-05", "2015-12-31")],
                count=2,
            ),
            "truncada",
        ),
        (
            _win("2015-01-01", "2015-12-31", [("2015-01-02", "abc", "2015-01-05", "2015-12-31")]),
            "no numérico",
        ),
        (
            _win("2015-01-01", "2015-12-31", [("2015-01-02", "NaN", "2015-01-05", "2015-12-31")]),
            "no finito",
        ),
        (
            _win("2015-01-01", "2015-12-31", [("2015-01-02", "1e999", "2015-01-05", "2015-12-31")]),
            "no finito",
        ),
        (
            _win("2015-01-01", "2015-12-31", [("2015-01-02", "1", "2014-12-05", "2015-12-31")]),
            "fuera de la ventana",
        ),
    ],
)
def test_parse_window_errors(text: str, match: str) -> None:
    with pytest.raises(SchemaError, match=match):
        fa.parse_window(text, "DGS10")


def test_merge_windows() -> None:
    out = _merged(W16, W15)
    rows = {(r["obs_date"], r["vintage_start"]): r for r in out.iter_rows(named=True)}
    r = rows[(D("2014-12-31"), D("2015-01-01"))]
    assert r["vintage_end"] == fa.OPEN_END
    assert r["vintage_start_clipped"] is True
    assert rows[(D("2015-12-30"), D("2015-12-31"))]["vintage_end"] == D("2016-03-01")
    assert rows[(D("2015-12-30"), D("2016-03-02"))]["value"] == 2.30
    assert rows[(D("2015-12-31"), D("2015-12-31"))]["value"] is None
    assert out.height == 5


def test_first_vintage_is_not_clipping() -> None:
    w = _win("2019-02-04", "2019-12-31", [("2019-01-02", "92", "2019-02-04", "2019-12-31")])
    out = fa.merge_windows([fa.parse_window(w, "DTWEXBGS")], D("2019-02-04"))
    assert out["vintage_start_clipped"].to_list() == [False]


def test_window_errors() -> None:
    w17 = _win("2017-01-01", "2017-12-31", [])
    with pytest.raises(SchemaError, match="contiguas"):
        _merged(W15, w17)
    w16b = _win("2016-01-01", "2016-12-31", [], observation_start="2015-01-01")
    with pytest.raises(SchemaError, match="observation_start"):
        _merged(W15, w16b)
    bad = _win(
        "2016-01-01",
        "2016-12-31",
        [
            ("2015-12-30", "2.4", "2016-01-01", "2016-12-31"),
            ("2015-12-30", "2.5", "2016-06-01", "2016-12-31"),
        ],
    )
    with pytest.raises(SchemaError, match="solapados"):
        _merged(bad)


# ---------------------------------------------------------------- disponibilidad


def test_official_release_hand_cases() -> None:
    # H.15: dato del vie 2018-12-04 → mié 5 cerrado → jue 6 16:15 EST = 21:15 UTC.
    assert fa.official_release(D("2018-12-04"), "h15", CAL, T1615) == datetime(
        2018, 12, 6, 21, 15, tzinfo=UTC
    )
    # H.10: dato del mié 2024-02-14 → lun 19 Presidents' Day → mar 20 16:15 EST.
    assert fa.official_release(D("2024-02-14"), "h10", CAL, T1615) == datetime(
        2024, 2, 20, 21, 15, tzinfo=UTC
    )
    # Sin calendario: 00:00 ET del día siguiente.
    # 2024-03-10 es el cambio de hora → 00:00 EDT del 11 = 04:00 UTC.
    assert fa.official_release(D("2024-03-10"), "none", CAL, T1615) == datetime(
        2024, 3, 11, 4, 0, tzinfo=UTC
    )
    with pytest.raises(SchemaError, match="desconocido"):
        fa.official_release(D("2024-03-10"), "xx", CAL, T1615)


def test_available_at_rules() -> None:
    win = _win(
        "2018-01-01",
        "2018-12-31",
        [
            ("2018-10-08", "15.69", "2018-10-05", "2018-12-31"),  # vintage antes de la observación
            ("2018-03-09", "1.0", "2018-03-12", "2018-05-01"),
            ("2018-03-09", "1.1", "2018-05-02", "2018-12-31"),  # revisión
        ],
    )
    out, hidden = fa.add_available_at(_merged(win, sid="VIXCLS"), {"VIXCLS": "none"}, CAL, T1615)
    rows = {(r["obs_date"], r["vintage_start"]): r for r in out.iter_rows(named=True)}
    r = rows[(D("2018-10-08"), D("2018-10-05"))]
    assert r["vintage_before_obs"] is True
    assert r["floored_by_schedule"] is True
    assert r["available_at"] == datetime(2018, 10, 9, 4, 0, tzinfo=UTC)
    first = rows[(D("2018-03-09"), D("2018-03-12"))]
    # 00:00 EDT del 13-mar (DST empezó el 11) = 04:00 UTC.
    assert first["available_at"] == datetime(2018, 3, 13, 4, 0, tzinfo=UTC)
    assert first["superseded_at"] == datetime(2018, 5, 3, 4, 0, tzinfo=UTC)
    assert rows[(D("2018-03-09"), D("2018-05-02"))]["superseded_at"] is None
    assert hidden == 0


def test_h15_floor_for_same_day_vintage() -> None:
    # T10YIE: vintage el mismo día de la observación → se espera a H.15 (día hábil siguiente 16:15).
    win = _win("2020-01-01", "2020-12-31", [("2020-06-02", "1.2", "2020-06-02", "2020-12-31")])
    out, _ = fa.add_available_at(_merged(win, sid="T10YIE"), {"T10YIE": "h15"}, CAL, T1615)
    assert out["available_at"].to_list() == [datetime(2020, 6, 3, 20, 15, tzinfo=UTC)]


def test_same_instant_vintages_keep_last() -> None:
    win = _win(
        "2018-01-01",
        "2018-12-31",
        [
            ("2018-10-08", "15.0", "2018-10-04", "2018-10-04"),
            ("2018-10-08", "15.69", "2018-10-05", "2018-12-31"),
        ],
    )
    out, hidden = fa.add_available_at(_merged(win, sid="VIXCLS"), {"VIXCLS": "none"}, CAL, T1615)
    assert hidden == 1
    assert out["value"].to_list() == [15.69]


@settings(max_examples=100, deadline=None)
@given(
    st.lists(st.tuples(st.integers(1, 40), st.integers(0, 3)), min_size=1, max_size=8),
    st.integers(1, 3),
)
def test_property_window_split_is_invisible(segments: list[tuple[int, int]], n_cuts: int) -> None:
    # Una historia de vintages de una observación cortada en ventanas contiguas debe dar la
    # misma fusión que la historia en una sola ventana.
    start = D("2016-01-04")
    periods = []
    cur = start
    for length, v in segments:
        periods.append((cur, cur + timedelta(days=length - 1), v))
        cur += timedelta(days=length)
    end = cur - timedelta(days=1)

    def window(lo: date, hi: date) -> str:
        rows = [
            ("2016-01-01", str(v), str(max(a, lo)), str(min(b, hi)))
            for a, b, v in periods
            if a <= hi and b >= lo
        ]
        return _win(str(lo), str(hi), rows)

    full = _merged(window(start, end))
    span = (end - start).days + 1
    cuts = sorted(
        {start + timedelta(days=max(1, span * k // (n_cuts + 1))) for k in range(1, n_cuts + 1)}
        - {start}
    )
    bounds = [start, *[c for c in cuts if c <= end]]
    wins = [
        window(a, (bounds[i + 1] - timedelta(days=1)) if i + 1 < len(bounds) else end)
        for i, a in enumerate(bounds)
    ]
    split = _merged(*wins)
    cols = ["obs_date", "value", "vintage_start", "vintage_end"]
    assert split.select(cols).equals(full.select(cols))


# ---------------------------------------------------------------- integración


def _write(store: RawStore, source: str, rel: str, text: str) -> None:
    p = store.raw_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    store._append(
        ManifestEntry(source, rel, "https://x.invalid", {}, "t", 200, "x", _sha256(p), 1, "c")
    )


def _cfg(series: list[str]) -> FredConfig:
    data = load_fred_config(CONFIG_DIR).model_dump(by_alias=True)
    data["series"]["value"] = series
    return FredConfig.model_validate(data)


def _setup(tmp_path: Path, files: dict[str, str]) -> None:
    store = RawStore(tmp_path / "raw", tmp_path / "manifests", "c")
    for rel, text in files.items():
        _write(store, "fred", rel, text)
    _write(store, "federal_calendar", "federal_calendar/usc5_6103_2023.htm", STATUTE)
    eos = json.dumps({"count": 0, "results": []})
    _write(store, "federal_calendar", "federal_calendar/fr_closing_eos_2014plus_20261016.json", eos)


def _run(tmp_path: Path, cfg: FredConfig) -> object:
    return fa.run_normalize(
        raw_root=tmp_path / "raw",
        out_root=tmp_path / "n",
        manifest_dir=tmp_path / "manifests",
        cfg=cfg,
        commit="abc",
    )


def test_run_end_to_end(tmp_path: Path) -> None:
    _setup(
        tmp_path,
        {
            "fred/api/DGS10/vintages_rt2015_20261016.json": W15,
            "fred/api/DGS10/vintages_rt2016_20261016.json": W16,
        },
    )
    res = _run(tmp_path, _cfg(["DGS10"]))
    by = res.manifest["by_series"]["DGS10"]  # type: ignore[attr-defined]
    assert by["n_rows"] == 5
    assert by["n_null"] == 1
    out = pl.read_parquet(tmp_path / "n" / "fred_alfred" / "fred-alfred-v2" / "vintages.parquet")
    assert out.height == 5


def test_run_file_year_mismatch_raises(tmp_path: Path) -> None:
    _setup(tmp_path, {"fred/api/DGS10/vintages_rt2017_20261016.json": W15})
    with pytest.raises(SchemaError, match="año del fichero"):
        _run(tmp_path, _cfg(["DGS10"]))


def test_run_series_without_schedule_raises(tmp_path: Path) -> None:
    _setup(tmp_path, {"fred/api/ABC/vintages_rt2015_20261016.json": W15})
    with pytest.raises(SchemaError, match="sin calendario"):
        _run(tmp_path, _cfg(["ABC"]))


def test_run_missing_series_raises(tmp_path: Path) -> None:
    with pytest.raises(RawIntegrityError, match="no hay ventanas"):
        _run(tmp_path, _cfg(["DGS10"]))

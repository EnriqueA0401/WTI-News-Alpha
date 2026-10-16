"""Tests de vintages ALFRED point-in-time (FASE_02_fundamentales.md §C). JSON SINTÉTICO."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from wti_news_alpha.config import CONFIG_DIR, FredConfig, load_fred_config
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.ingestion.rawstore import ManifestEntry, RawStore, _sha256
from wti_news_alpha.normalization import fred_alfred as fa

D = date.fromisoformat


def _win(lo: str, hi: str, rows: list[tuple[str, str, str, str]], **over: object) -> str:
    obs = [{"realtime_start": a, "realtime_end": b, "date": d, "value": v} for d, v, a, b in rows]
    body = {
        "realtime_start": lo,
        "realtime_end": hi,
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


def test_parse_window() -> None:
    lo, hi, df = fa.parse_window(W15, "DGS10")
    assert (lo, hi) == (D("2015-01-01"), D("2015-12-31"))
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
            _win("2015-01-01", "2015-12-31", [("2015-01-02", "1", "2014-12-05", "2015-12-31")]),
            "fuera de la ventana",
        ),
    ],
)
def test_parse_window_errors(text: str, match: str) -> None:
    with pytest.raises(SchemaError, match=match):
        fa.parse_window(text, "DGS10")


def test_merge_and_available_at() -> None:
    merged = fa.merge_windows([fa.parse_window(W16, "X"), fa.parse_window(W15, "X")])
    out = fa.add_available_at(merged)
    rows = {(r["obs_date"], r["vintage_start"]): r for r in out.iter_rows(named=True)}
    # Periodo partido por el borde con el mismo valor → uno solo, abierto (última ventana).
    r = rows[(D("2014-12-31"), D("2015-01-01"))]
    assert r["vintage_end"] == fa.OPEN_END
    assert r["vintage_start_clipped"] is True
    r = rows[(D("2015-12-30"), D("2015-12-31"))]
    assert (r["value"], r["vintage_end"]) == (2.31, D("2016-03-01"))
    # 00:00 ET del día siguiente: 2016-01-01 00:00 EST = 05:00 UTC.
    assert r["available_at"] == datetime(2016, 1, 1, 5, 0, tzinfo=UTC)
    assert rows[(D("2015-12-30"), D("2016-03-02"))]["value"] == 2.30
    # El "." se conserva como nulo y después se rellena.
    assert rows[(D("2015-12-31"), D("2015-12-31"))]["value"] is None
    assert rows[(D("2015-12-31"), D("2016-01-04"))]["value"] == 2.27
    assert out.height == 5


def test_non_contiguous_windows_raise() -> None:
    w17 = _win("2017-01-01", "2017-12-31", [])
    with pytest.raises(SchemaError, match="contiguas"):
        fa.merge_windows([fa.parse_window(W15, "X"), fa.parse_window(w17, "X")])


def test_overlapping_periods_raise() -> None:
    bad = _win("2016-01-01", "2016-12-31", [("2015-12-30", "2.4", "2016-01-01", "2016-12-31")])
    bad2 = _win(
        "2016-01-01",
        "2016-12-31",
        [
            ("2015-12-30", "2.4", "2016-01-01", "2016-12-31"),
            ("2015-12-30", "2.5", "2016-06-01", "2016-12-31"),
        ],
    )
    fa.merge_windows([fa.parse_window(bad, "X")])
    with pytest.raises(SchemaError, match="solapados"):
        fa.merge_windows([fa.parse_window(bad2, "X")])


def test_vintage_before_observation_uses_observation_day() -> None:
    # Regresión (datos reales VIXCLS 2018-10-08): vintage del viernes para el valor del lunes.
    win = _win("2018-01-01", "2018-12-31", [("2018-10-08", "15.69", "2018-10-05", "2018-12-31")])
    out = fa.add_available_at(fa.merge_windows([fa.parse_window(win, "VIXCLS")]))
    row = out.row(0, named=True)
    assert row["vintage_before_obs"] is True
    assert row["available_at"] == datetime(2018, 10, 9, 4, 0, tzinfo=UTC)  # 00:00 EDT del martes


def test_config_loads() -> None:
    c = load_fred_config(CONFIG_DIR)
    assert "DTWEXBGS" in c.series.require("s")


def _write(store: RawStore, rel: str, text: str) -> None:
    p = store.raw_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    store._append(
        ManifestEntry("fred", rel, "https://x.invalid", {}, "t", 200, "x", _sha256(p), 1, "c")
    )


def _cfg(series: list[str]) -> FredConfig:
    data = load_fred_config(CONFIG_DIR).model_dump(by_alias=True)
    data["series"]["value"] = series
    return FredConfig.model_validate(data)


def test_run_end_to_end(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw", tmp_path / "manifests", "c")
    _write(store, "fred/api/DGS10/vintages_rt2015_20261016.json", W15)
    _write(store, "fred/api/DGS10/vintages_rt2016_20261016.json", W16)
    res = fa.run_normalize(
        raw_root=tmp_path / "raw",
        out_root=tmp_path / "n",
        manifest_dir=tmp_path / "manifests",
        cfg=_cfg(["DGS10"]),
        commit="abc",
    )
    assert res.created
    assert res.manifest["by_series"]["DGS10"] == {
        "n_rows": 5,
        "n_obs": 3,
        "n_null": 1,
        "n_clipped": 1,
        "n_vintage_before_obs": 0,
    }
    out = pl.read_parquet(tmp_path / "n" / "fred_alfred" / "fred-alfred-v1" / "vintages.parquet")
    assert out.height == 5


def test_run_missing_series_raises(tmp_path: Path) -> None:
    with pytest.raises(RawIntegrityError, match="no hay ventanas"):
        fa.run_normalize(
            raw_root=tmp_path,
            out_root=tmp_path / "n",
            manifest_dir=tmp_path / "m",
            cfg=_cfg(["DGS10"]),
            commit="abc",
        )

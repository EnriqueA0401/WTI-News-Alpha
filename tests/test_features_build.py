"""Tests de la capa de features (FASE_04.md). Datos SINTÉTICOS, solo para tests."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from wti_news_alpha.config import CONFIG_DIR, FeaturePeriod, load_features_config
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.features import build, pit

CFG = load_features_config(CONFIG_DIR)
T0 = datetime(2024, 1, 31, 22, 0, tzinfo=UTC)  # 17:00 ET; las barras cruzan el cambio de mes
UTC_US = pl.Datetime("us", "UTC")


def _write(tmp: Path, source: str, version: str, frames: dict[str, pl.DataFrame]) -> None:
    base = tmp / "norm" / source / version
    base.mkdir(parents=True)
    outputs = {}
    for name, df in frames.items():
        df.write_parquet(base / name)
        outputs[name] = hashlib.sha256((base / name).read_bytes()).hexdigest()
    mdir = tmp / "manifests" / "normalized" / source
    mdir.mkdir(parents=True)
    (mdir / f"{version}.json").write_text(json.dumps({"outputs": outputs}))


def _setup(tmp: Path) -> None:
    ends = [T0 + timedelta(minutes=m) for m in range(1, 181)]  # 3 h de barras de 1 min
    closes = [70.0 + 0.01 * (m % 13) for m in range(1, 181)]
    bars = pl.DataFrame(
        {
            "available_at": ends,
            "bid_high": [c + 0.02 for c in closes],
            "bid_low": [c - 0.02 for c in closes],
            "bid_close": closes,
            "tick_volume": [5] * 180,
            "avg_spread": [0.03] * 180,
        },
        schema_overrides={"available_at": UTC_US},
    )
    _write(tmp, "darwinex_xti_m1", CFG.inputs.price, {"2024.parquet": bars})
    rel = T0 - timedelta(days=1)
    wpsr = pl.DataFrame(
        {
            "series_id": ["WCESTUS1", "WCESTUS1"],
            "week_ending": [date(2024, 1, 19), date(2024, 1, 26)],
            "value": [420000.0, 421000.0],
            "available_at": [rel - timedelta(days=7), rel],
        },
        schema_overrides={"available_at": UTC_US},
    )
    _write(tmp, "eia_wpsr", CFG.inputs.wpsr, {"values.parquet": wpsr})
    cot = pl.DataFrame(
        {
            "as_of_date": [date(2024, 1, 23)],
            "mm_long": [300],
            "mm_short": [100],
            "open_interest": [1000],
            "available_at": [T0 - timedelta(days=5)],
        },
        schema_overrides={"available_at": UTC_US},
    )
    _write(tmp, "cftc_cot", CFG.inputs.cot, {"cot.parquet": cot})
    fred = pl.DataFrame(
        {
            "series_id": ["DGS10"],
            "obs_date": [date(2024, 1, 30)],
            "value": [4.0],
            "available_at": [T0 - timedelta(hours=2)],
        },
        schema_overrides={"available_at": UTC_US},
    )
    _write(tmp, "fred_alfred", CFG.inputs.fred, {"vintages.parquet": fred})
    # Ficheros GDELT cada 15 min de T0-6h a T0+3h con 5 artículos de relleno no elegibles,
    # más 2 elegibles de OPEP+ (T0-1h, evento nuevo; T0+30 min).
    noms = [T0 - timedelta(hours=6) + timedelta(minutes=15 * i) for i in range(37)]
    fill = [(n, "other", False, False) for n in noms for _ in range(5)]
    elig = [(T0 - timedelta(minutes=75), "opec_policy", True, True)]
    elig += [(T0 + timedelta(minutes=15), "opec_policy", True, False)]
    rows = fill + elig
    arts = pl.DataFrame(
        {
            "source_file_ts": [r[0] for r in rows],
            "first_available_at": [r[0] + timedelta(minutes=15) for r in rows],
            "family": [r[1] for r in rows],
            "eligible": [r[2] for r in rows],
            "is_event_first": [r[3] for r in rows],
            "title_source": ["title"] * len(rows),
        },
        schema_overrides={"source_file_ts": UTC_US, "first_available_at": UTC_US},
    )
    jan = pl.col("first_available_at") < datetime(2024, 2, 1, tzinfo=UTC)
    _write(
        tmp,
        "events",
        CFG.inputs.events,
        {"articles_202401.parquet": arts.filter(jan), "articles_202402.parquet": arts.filter(~jan)},
    )


def _run(tmp: Path) -> build.Published:
    return build.run_build(
        out_root=tmp / "norm", manifest_dir=tmp / "manifests", cfg=CFG, commit="abc"
    )


def test_build_end_to_end(tmp_path: Path) -> None:
    _setup(tmp_path)
    res = _run(tmp_path)
    assert res.created
    base = tmp_path / "features" / CFG.version
    assert sorted(p.name for p in base.iterdir()) == [
        "features_202401.parquet",
        "features_202402.parquet",
    ]
    out = pl.concat([pl.read_parquet(p) for p in sorted(base.iterdir())])
    pit.check_no_leakage(out)
    assert out["feature_version"].unique().to_list() == [CFG.version]
    assert out["data_cutoff_time"].equals(out["decision_time"], check_names=False)
    # Rejilla de 5 min de 22:05 a 01:05 (+ 5 min tras la última barra).
    assert out.height == 37
    row = out.filter(pl.col("decision_time") == T0 + timedelta(minutes=35)).row(0, named=True)
    assert row["wpsr_WCESTUS1_value"] == 421000.0
    assert row["wpsr_WCESTUS1_chg"] == 1000.0
    assert row["cot_mm_net"] == 200
    assert row["fred_DGS10_value"] == 4.0
    assert row["news_n_opec_policy_15m"] == 1  # el artículo de T0 + 30 min
    assert row["news_event_age_min_opec_policy"] == pytest.approx(95.0)
    assert row["news_source_ok"] is True
    assert row["cot_data_lag_d"] == 8  # as-of 23-ene, decisión 31-ene
    assert res.manifest["event_families"] == ["opec_policy"]  # type: ignore[index]
    again = _run(tmp_path)
    assert not again.created  # idempotente: verifica la versión publicada


def test_build_refuses_tampered_input(tmp_path: Path) -> None:
    _setup(tmp_path)
    path = tmp_path / "norm" / "cftc_cot" / CFG.inputs.cot / "cot.parquet"
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(RawIntegrityError, match="integridad"):
        _run(tmp_path)
    assert not (tmp_path / "features").exists()


def test_build_refuses_missing_manifest_and_ambiguous_files(tmp_path: Path) -> None:
    _setup(tmp_path)
    (tmp_path / "manifests" / "normalized" / "fred_alfred" / f"{CFG.inputs.fred}.json").unlink()
    with pytest.raises(RawIntegrityError, match="falta el manifiesto"):
        _run(tmp_path)
    with pytest.raises(RawIntegrityError, match="único"):
        build._one({"a/x.parquet": Path("x"), "b/x.parquet": Path("y")}, "x.parquet")


def test_gdelt_slots_require_a_single_availability_per_file(tmp_path: Path) -> None:
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    df = pl.DataFrame(
        {
            "source_file_ts": [ts, ts],
            "first_available_at": [ts + timedelta(minutes=15), ts + timedelta(minutes=16)],
        },
        schema_overrides={"source_file_ts": UTC_US, "first_available_at": UTC_US},
    )
    df.write_parquet(tmp_path / "a.parquet")
    with pytest.raises(SchemaError, match="varias horas"):
        build.gdelt_slots([tmp_path / "a.parquet"])


def test_build_refuses_empty_grid_outside_periods(tmp_path: Path) -> None:
    _setup(tmp_path)
    per = CFG.periods.model_copy(
        update={
            "unsafe_value": [
                FeaturePeriod(
                    start=datetime(2030, 1, 1, tzinfo=UTC), end=datetime(2031, 1, 1, tzinfo=UTC)
                )
            ]
        }
    )
    cfg = CFG.model_copy(update={"periods": per})
    with pytest.raises(SchemaError, match="rejilla vacía"):
        build.run_build(
            out_root=tmp_path / "norm", manifest_dir=tmp_path / "manifests", cfg=cfg, commit="abc"
        )

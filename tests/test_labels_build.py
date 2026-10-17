"""Capa de etiquetas ``label-v1`` de extremo a extremo (FASE_06.md). Datos SINTÉTICOS."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from wti_news_alpha.config import CONFIG_DIR, load_labels_config, load_project_config
from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.labels import build

CFG = load_labels_config(CONFIG_DIR)
PROJ = load_project_config(CONFIG_DIR)
T0 = datetime(2024, 1, 31, 20, 0, tzinfo=UTC)
UTC_US = pl.Datetime("us", "UTC")


def _write(base: Path, mdir: Path, frames: dict[str, pl.DataFrame], version: str) -> None:
    base.mkdir(parents=True)
    outputs = {}
    for name, df in frames.items():
        df.write_parquet(base / name)
        outputs[name] = hashlib.sha256((base / name).read_bytes()).hexdigest()
    mdir.mkdir(parents=True)
    (mdir / f"{version}.json").write_text(json.dumps({"outputs": outputs}))


def _setup(tmp: Path) -> None:
    ends = [T0 + timedelta(minutes=m) for m in range(1, 121)]
    closes = [70.0 + 0.01 * m for m in range(1, 121)]
    bars = pl.DataFrame(
        {
            "available_at": ends,
            "bid_high": [c + 0.02 for c in closes],
            "bid_low": [c - 0.02 for c in closes],
            "bid_close": closes,
            "tick_volume": [5] * 120,
            "avg_spread": [0.04] * 120,
        },
        schema_overrides={"available_at": UTC_US},
    )
    _write(
        tmp / "norm" / "darwinex_xti_m1" / CFG.inputs.price,
        tmp / "manifests" / "normalized" / "darwinex_xti_m1",
        {"2024.parquet": bars},
        CFG.inputs.price,
    )
    feats = pl.DataFrame(
        {"decision_time": [T0 + timedelta(minutes=5 * i) for i in range(1, 13)]},
        schema={"decision_time": UTC_US},
    )
    _write(
        tmp / "features" / CFG.inputs.features,
        tmp / "manifests" / "normalized" / "features",
        {"features_202401.parquet": feats.head(8), "features_202402.parquet": feats.tail(4)},
        CFG.inputs.features,
    )


def _run(tmp: Path) -> build.Published:
    return build.run_build(
        out_root=tmp / "norm", manifest_dir=tmp / "manifests", cfg=CFG, proj=PROJ, commit="abc"
    )


def test_labels_layer_end_to_end(tmp_path: Path) -> None:
    _setup(tmp_path)
    res = _run(tmp_path)
    assert res.created
    base = tmp_path / "labels" / CFG.version
    out = pl.concat([pl.read_parquet(p) for p in sorted(base.iterdir())])
    assert out.height == 12
    row = out.filter(pl.col("decision_time") == T0 + timedelta(minutes=5)).row(0, named=True)
    assert row["entry_at"] == T0 + timedelta(minutes=6)
    assert row["ret_15m"] is not None
    assert row["ret_60m"] is not None
    assert row["label_version"] == CFG.version
    assert res.manifest["sealed_start"].startswith("2025-07-01")  # type: ignore[index]
    assert not _run(tmp_path).created


def test_labels_refuse_tampered_input_and_unsupported_session_end(tmp_path: Path) -> None:
    _setup(tmp_path)
    f = tmp_path / "features" / CFG.inputs.features / "features_202401.parquet"
    f.write_bytes(f.read_bytes() + b"x")
    with pytest.raises(RawIntegrityError):
        _run(tmp_path)
    hz = PROJ.horizons.session_end_reference.model_copy(update={"unsafe_value": "settlement"})
    proj = PROJ.model_copy(
        update={"horizons": PROJ.horizons.model_copy(update={"session_end_reference": hz})}
    )
    with pytest.raises(SchemaError, match="17:00"):
        build.label_params(CFG, proj)

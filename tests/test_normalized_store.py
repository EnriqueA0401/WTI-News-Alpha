"""Tests del publicador inmutable de capas normalizadas."""

import json
import os
import stat
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.normalization.store import LayerSpec, publish_or_verify

INPUTS = {"a/b.json": "1" * 64}
PARAMS = {"offset": 7, "day": date(2015, 1, 7)}


def _spec(tmp_path: Path) -> LayerSpec:
    return LayerSpec(tmp_path / "norm", tmp_path / "man", "src", "v1")


def _writable(spec: LayerSpec) -> None:
    os.chmod(spec.out_dir, stat.S_IRWXU)
    for p in spec.out_dir.iterdir():
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def _build() -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
    return {"x.parquet": pl.DataFrame({"a": [1, 2]})}, {"rows": 2}


def test_publish_then_verify(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    first = publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c1")
    assert first.created
    assert not os.stat(spec.out_dir / "x.parquet").st_mode & stat.S_IWUSR
    man = json.loads(spec.manifest_path.read_text())
    assert man["rows"] == 2
    assert man["inputs"] == INPUTS
    assert man["code_commit"] == "c1"
    assert man["params"] == {"day": "2015-01-07", "offset": 7}
    assert not os.stat(spec.out_dir).st_mode & stat.S_IWUSR
    assert not os.stat(spec.manifest_path).st_mode & stat.S_IWUSR
    assert [p.name for p in spec.manifest_path.parent.iterdir()] == ["v1.json"]

    def boom() -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
        raise AssertionError("no debe reconstruir")

    second = publish_or_verify(spec, INPUTS, PARAMS, boom, commit="c2")
    assert not second.created
    assert second.outputs == first.outputs


def test_deterministic(tmp_path: Path) -> None:
    a = publish_or_verify(_spec(tmp_path / "a"), INPUTS, PARAMS, _build, commit="c")
    b = publish_or_verify(_spec(tmp_path / "b"), INPUTS, PARAMS, _build, commit="c")
    assert a.outputs == b.outputs


def test_dirty_refused(tmp_path: Path) -> None:
    with pytest.raises(RawIntegrityError, match="dirty"):
        publish_or_verify(_spec(tmp_path), INPUTS, PARAMS, _build, commit="c-dirty")


def test_other_inputs_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")
    with pytest.raises(RawIntegrityError, match="otras entradas"):
        publish_or_verify(spec, {"a/b.json": "2" * 64}, PARAMS, _build, commit="c")


def test_altered_output_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")
    _writable(spec)
    p = spec.out_dir / "x.parquet"
    p.write_bytes(b"x")
    with pytest.raises(RawIntegrityError, match="checksum"):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")


def test_extra_file_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")
    _writable(spec)
    (spec.out_dir / "y.parquet").write_bytes(b"x")
    with pytest.raises(RawIntegrityError, match="manifiesto"):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")


def test_dir_without_manifest_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")
    spec.manifest_path.unlink()
    with pytest.raises(RawIntegrityError, match="sin manifiesto"):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")


def test_manifest_without_dir_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")
    _writable(spec)
    for p in spec.out_dir.iterdir():
        p.unlink()
    spec.out_dir.rmdir()
    with pytest.raises(RawIntegrityError, match="falta"):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")


def test_invalid_output_name_refused(tmp_path: Path) -> None:
    def bad() -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
        return {"../evil.parquet": pl.DataFrame({"a": [1]})}, {}

    with pytest.raises(SchemaError, match="nombres"):
        publish_or_verify(_spec(tmp_path), INPUTS, PARAMS, bad, commit="c")
    assert not (tmp_path / "norm" / "evil.parquet").exists()


def test_failed_write_leaves_nothing(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    def bad() -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
        obj = pl.Series("a", [object()], dtype=pl.Object)
        return {"a.parquet": pl.DataFrame({"a": [1]}), "b.parquet": pl.DataFrame(obj)}, {}

    with pytest.raises(pl.exceptions.PolarsError):
        publish_or_verify(spec, INPUTS, PARAMS, bad, commit="c")
    assert not spec.out_dir.exists()
    assert list(spec.out_dir.parent.iterdir()) == []
    assert not spec.manifest_path.exists()


def test_other_params_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")
    with pytest.raises(RawIntegrityError, match="otros parámetros"):
        publish_or_verify(spec, INPUTS, {**PARAMS, "offset": 6}, _build, commit="c")


@pytest.mark.parametrize(
    ("text", "match"), [("{bad", "ilegible"), ('{"inputs": {}}', "incompleto")]
)
def test_corrupt_manifest_detected(tmp_path: Path, text: str, match: str) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")
    os.chmod(spec.manifest_path, stat.S_IRUSR | stat.S_IWUSR)
    spec.manifest_path.write_text(text)
    with pytest.raises(RawIntegrityError, match=match):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")


def test_stale_staging_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    (spec.out_root / "src" / ".staging-v1-abc").mkdir(parents=True)
    with pytest.raises(RawIntegrityError, match="staging"):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c")

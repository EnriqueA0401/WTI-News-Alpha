"""Tests del publicador inmutable de capas normalizadas."""

import json
import os
import stat
import types
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from wti_news_alpha.errors import RawIntegrityError, SchemaError
from wti_news_alpha.normalization.store import (
    LayerSpec,
    code_hashes,
    publish_or_verify,
    publish_or_verify_streaming,
)

INPUTS = {"a/b.json": "1" * 64}
PARAMS = {"offset": 7, "day": date(2015, 1, 7)}
CODE = {"mod": "a" * 64}


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
    first = publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c1", code=CODE)
    assert first.created
    assert not os.stat(spec.out_dir / "x.parquet").st_mode & stat.S_IWUSR
    man = json.loads(spec.manifest_path.read_text())
    assert man["rows"] == 2
    assert man["inputs"] == INPUTS
    assert man["code_commit"] == "c1"
    assert man["params"] == {"day": "2015-01-07", "offset": 7}
    assert man["code"] == CODE
    assert not os.stat(spec.out_dir).st_mode & stat.S_IWUSR
    assert not os.stat(spec.manifest_path).st_mode & stat.S_IWUSR
    assert [p.name for p in spec.manifest_path.parent.iterdir()] == ["v1.json"]

    def boom() -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
        raise AssertionError("no debe reconstruir")

    second = publish_or_verify(spec, INPUTS, PARAMS, boom, commit="c2", code=CODE)
    assert not second.created
    assert second.outputs == first.outputs


def test_deterministic(tmp_path: Path) -> None:
    a = publish_or_verify(_spec(tmp_path / "a"), INPUTS, PARAMS, _build, commit="c", code=CODE)
    b = publish_or_verify(_spec(tmp_path / "b"), INPUTS, PARAMS, _build, commit="c", code=CODE)
    assert a.outputs == b.outputs


def test_dirty_refused(tmp_path: Path) -> None:
    with pytest.raises(RawIntegrityError, match="dirty"):
        publish_or_verify(_spec(tmp_path), INPUTS, PARAMS, _build, commit="c-dirty", code=CODE)


def test_other_inputs_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)
    with pytest.raises(RawIntegrityError, match="otras entradas"):
        publish_or_verify(spec, {"a/b.json": "2" * 64}, PARAMS, _build, commit="c", code=CODE)


def test_altered_output_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)
    _writable(spec)
    p = spec.out_dir / "x.parquet"
    p.write_bytes(b"x")
    with pytest.raises(RawIntegrityError, match="checksum"):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)


def test_extra_file_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)
    _writable(spec)
    (spec.out_dir / "y.parquet").write_bytes(b"x")
    with pytest.raises(RawIntegrityError, match="manifiesto"):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)


def test_dir_without_manifest_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)
    spec.manifest_path.unlink()
    with pytest.raises(RawIntegrityError, match="sin manifiesto"):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)


def test_invalid_output_name_refused(tmp_path: Path) -> None:
    def bad() -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
        return {"../evil.parquet": pl.DataFrame({"a": [1]})}, {}

    with pytest.raises(SchemaError, match="nombres"):
        publish_or_verify(_spec(tmp_path), INPUTS, PARAMS, bad, commit="c", code=CODE)
    assert not (tmp_path / "norm" / "evil.parquet").exists()


def test_failed_write_leaves_nothing(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    def bad() -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
        obj = pl.Series("a", [object()], dtype=pl.Object)
        return {"a.parquet": pl.DataFrame({"a": [1]}), "b.parquet": pl.DataFrame(obj)}, {}

    with pytest.raises(pl.exceptions.PolarsError):
        publish_or_verify(spec, INPUTS, PARAMS, bad, commit="c", code=CODE)
    assert not spec.out_dir.exists()
    assert list(spec.out_dir.parent.iterdir()) == []
    assert not spec.manifest_path.exists()


def test_other_params_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)
    with pytest.raises(RawIntegrityError, match="otros parámetros"):
        publish_or_verify(spec, INPUTS, {**PARAMS, "offset": 6}, _build, commit="c", code=CODE)


@pytest.mark.parametrize(
    ("text", "match"), [("{bad", "ilegible"), ('{"inputs": {}}', "incompleto")]
)
def test_corrupt_manifest_detected(tmp_path: Path, text: str, match: str) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)
    os.chmod(spec.manifest_path, stat.S_IRUSR | stat.S_IWUSR)
    spec.manifest_path.write_text(text)
    with pytest.raises(RawIntegrityError, match=match):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)


def test_stale_staging_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    (spec.out_root / "src" / ".staging-v1-abc").mkdir(parents=True)
    with pytest.raises(RawIntegrityError, match="staging"):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)


def test_other_code_detected(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)
    with pytest.raises(RawIntegrityError, match="otro código"):
        publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code={"mod": "b" * 64})


def test_reserved_extra_key_refused(tmp_path: Path) -> None:
    def bad() -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
        return {"x.parquet": pl.DataFrame({"a": [1]})}, {"inputs": {}}

    with pytest.raises(SchemaError, match="reservados"):
        publish_or_verify(_spec(tmp_path), INPUTS, PARAMS, bad, commit="c", code=CODE)


def test_recovery_when_dir_missing(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    first = publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)
    _writable(spec)
    for p in spec.out_dir.iterdir():
        p.unlink()
    spec.out_dir.rmdir()
    again = publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c2", code=CODE)
    assert again.created
    assert again.outputs == first.outputs
    assert again.manifest["code_commit"] == "c"


def test_recovery_with_different_output_raises(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)
    _writable(spec)
    for p in spec.out_dir.iterdir():
        p.unlink()
    spec.out_dir.rmdir()

    def other() -> tuple[dict[str, pl.DataFrame], dict[str, Any]]:
        return {"x.parquet": pl.DataFrame({"a": [9, 9]})}, {}

    with pytest.raises(RawIntegrityError, match="no reproduce"):
        publish_or_verify(spec, INPUTS, PARAMS, other, commit="c", code=CODE)
    assert not spec.out_dir.exists()


def test_code_hashes() -> None:
    out = code_hashes(json)
    assert set(out) == {"json"}
    assert all(len(v) == 64 for v in out.values())


def test_publisher_hash_ignored_for_old_manifests(tmp_path: Path) -> None:
    # Manifiestos anteriores incluían el hash del publicador: no forma parte de la identidad.
    spec = _spec(tmp_path)
    old = {**CODE, "wti_news_alpha.normalization.store": "f" * 64}
    publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=old)
    again = publish_or_verify(spec, INPUTS, PARAMS, _build, commit="c", code=CODE)
    assert not again.created


def test_streaming_writer(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    def writer(staging: Path) -> dict[str, Any]:
        for i in range(3):
            pl.DataFrame({"a": [i]}).write_parquet(staging / f"p{i}.parquet")
        return {"parts": 3}

    pub = publish_or_verify_streaming(spec, INPUTS, PARAMS, writer, commit="c", code=CODE)
    assert sorted(pub.outputs) == ["p0.parquet", "p1.parquet", "p2.parquet"]
    assert pub.manifest["parts"] == 3


@pytest.mark.parametrize("name", ["bad.csv", "Upper.parquet"])
def test_streaming_writer_bad_names(tmp_path: Path, name: str) -> None:
    spec = _spec(tmp_path)

    def writer(staging: Path) -> dict[str, Any]:
        (staging / name).write_bytes(b"x")
        return {}

    with pytest.raises(SchemaError, match="staging"):
        publish_or_verify_streaming(spec, INPUTS, PARAMS, writer, commit="c", code=CODE)
    assert not spec.out_dir.exists()
    assert not spec.manifest_path.exists()


def test_streaming_writer_nothing_written(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match="ninguna"):
        publish_or_verify_streaming(
            _spec(tmp_path), INPUTS, PARAMS, lambda _: {}, commit="c", code=CODE
        )


def test_code_hashes_module_without_file_raises() -> None:
    mod = types.ModuleType("fake")
    with pytest.raises(RawIntegrityError, match="sin fichero"):
        code_hashes(mod)

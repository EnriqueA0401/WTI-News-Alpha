"""Tests del almacén de datos crudos inmutable con manifiesto (CLAUDE.md §3.3)."""

import hashlib
import json
import os
import stat
from pathlib import Path

import httpx
import pytest

from wti_news_alpha.errors import RawIntegrityError, SourceAccessError
from wti_news_alpha.ingestion.rawstore import RawStore

BODY = b"col1,col2\n1,2\n"
SHA = hashlib.sha256(BODY).hexdigest()
SECRET = "s3cr3t-key"  # noqa: S105 - valor sintético de test


class Counter:
    def __init__(self, status: int = 200, body: bytes = BODY) -> None:
        self.calls: list[httpx.Request] = []
        self.status = status
        self.body = body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(self.status, content=self.body)


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(raw_root=tmp_path / "raw", manifest_dir=tmp_path / "manifests", commit="abc")


def _client(handler: Counter) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_writes_file_and_manifest(store: RawStore) -> None:
    h = Counter()
    with _client(h) as c:
        entry = store.fetch(c, "src", "https://x.org/f.csv", "src/2026/f.csv")
    path = store.raw_root / "src/2026/f.csv"
    assert path.read_bytes() == BODY
    assert entry.sha256 == SHA
    assert entry.n_bytes == len(BODY)
    assert entry.code_commit == "abc"
    assert entry.retrieved_at_utc.endswith("+00:00")
    lines = (store.manifest_dir / "src.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["sha256"] == SHA


def test_raw_file_is_read_only(store: RawStore) -> None:
    with _client(Counter()) as c:
        store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv")
    mode = (store.raw_root / "src/f.csv").stat().st_mode
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


def test_second_fetch_is_cached_without_network(store: RawStore) -> None:
    h = Counter()
    with _client(h) as c:
        first = store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv")
        second = store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv")
    assert len(h.calls) == 1
    assert second == first
    assert len((store.manifest_dir / "src.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_tampered_file_fails_loudly(store: RawStore) -> None:
    with _client(Counter()) as c:
        store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv")
        path = store.raw_root / "src/f.csv"
        os.chmod(path, 0o644)
        path.write_bytes(b"tampered")
        with pytest.raises(RawIntegrityError, match="checksum"):
            store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv")


def test_existing_file_without_manifest_is_never_overwritten(store: RawStore) -> None:
    path = store.raw_root / "src/f.csv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"orphan")
    with _client(Counter()) as c, pytest.raises(RawIntegrityError, match="sin manifiesto"):
        store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv")
    assert path.read_bytes() == b"orphan"


def test_manifest_without_file_fails(store: RawStore) -> None:
    with _client(Counter()) as c:
        store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv")
        path = store.raw_root / "src/f.csv"
        os.chmod(path, 0o644)
        path.unlink()
        with pytest.raises(RawIntegrityError, match="falta el fichero"):
            store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv")


def test_http_error_writes_nothing(store: RawStore) -> None:
    with _client(Counter(status=404)) as c, pytest.raises(SourceAccessError, match="404"):
        store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv")
    assert not (store.raw_root / "src/f.csv").exists()
    assert not (store.manifest_dir / "src.jsonl").exists()
    assert (
        not any((store.raw_root / "src").glob("*")) if (store.raw_root / "src").exists() else True
    )


def test_network_error_is_source_access_error(store: RawStore) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(boom)) as c,
        pytest.raises(SourceAccessError, match="ConnectError"),
    ):
        store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv")


def test_max_bytes_guard(store: RawStore) -> None:
    with _client(Counter()) as c, pytest.raises(SourceAccessError, match="max_bytes"):
        store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv", max_bytes=3)
    assert not (store.raw_root / "src/f.csv").exists()


def test_secrets_never_in_manifest(store: RawStore) -> None:
    h = Counter()
    with _client(h) as c:
        store.fetch(
            c,
            "src",
            "https://x.org/f.csv?series=A",
            "src/f.csv",
            params={"api_key": SECRET, "x": "1"},
            secret_params=("api_key",),
        )
    assert h.calls[0].url.params["api_key"] == SECRET
    assert h.calls[0].url.params["series"] == "A"
    text = (store.manifest_dir / "src.jsonl").read_text(encoding="utf-8")
    assert SECRET not in text
    assert '"api_key": "***"' in text


def test_basic_auth_is_used_and_not_recorded(store: RawStore) -> None:
    h = Counter()
    with _client(h) as c:
        store.fetch(c, "src", "https://x.org/f.csv", "src/f.csv", basic_auth=(SECRET, ""))
    assert h.calls[0].headers["authorization"].startswith("Basic ")
    assert SECRET not in (store.manifest_dir / "src.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("bad", ["../escape.csv", "/abs/f.csv", "src/../../x"])
def test_rejects_paths_outside_raw_root(store: RawStore, bad: str) -> None:
    with _client(Counter()) as c, pytest.raises(RawIntegrityError, match="fuera"):
        store.fetch(c, "src", "https://x.org/f.csv", bad)


def test_verify_all(store: RawStore) -> None:
    with _client(Counter()) as c:
        store.fetch(c, "src", "https://x.org/a.csv", "src/a.csv")
        store.fetch(c, "src", "https://x.org/b.csv", "src/b.csv")
    assert store.verify_all("src") == 2
    path = store.raw_root / "src/b.csv"
    os.chmod(path, 0o644)
    path.write_bytes(b"x")
    with pytest.raises(RawIntegrityError, match=r"b\.csv"):
        store.verify_all("src")


def test_verify_all_without_manifest_is_zero(store: RawStore) -> None:
    assert store.verify_all("nothing") == 0


def test_verify_single_file(store: RawStore) -> None:
    with _client(Counter()) as c:
        e = store.fetch(c, "src", "https://x.org/a.csv", "src/a.csv")
    assert store.verify("src", "src/a.csv") == e
    with pytest.raises(RawIntegrityError, match="no está"):
        store.verify("src", "src/zz.csv")
    path = store.raw_root / "src/a.csv"
    os.chmod(path, 0o644)
    path.write_bytes(b"x")
    with pytest.raises(RawIntegrityError, match="integridad"):
        store.verify("src", "src/a.csv")

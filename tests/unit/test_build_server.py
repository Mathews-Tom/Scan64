"""Packaging inputs remain pinned and incomplete downloads never become resources."""

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def builder() -> ModuleType:
    script = Path(__file__).resolve().parents[2] / "scripts" / "build-server.py"
    spec = importlib.util.spec_from_file_location("build_server", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_engine_pins_cover_source_and_both_networks(builder: ModuleType) -> None:
    dockerfile = (builder.ROOT / "Dockerfile").read_text()
    files = builder.pinned_engine_files(dockerfile)
    assert len(files) == 3
    assert {item[0] for item in files} == {
        "stockfish-sf_18.tar.gz",
        "nn-c288c895ea92.nnue",
        "nn-37f18f62d772.nnue",
    }
    assert all(len(item[2]) == 64 for item in files)
    with pytest.raises(ValueError, match="Dockerfile pin missing"):
        builder.pinned_engine_files(dockerfile.replace(files[0][2], "untrusted"))
    with pytest.raises(ValueError, match="Dockerfile pin missing"):
        builder.pinned_engine_files(dockerfile.replace(files[1][1], "https://example.com"))


def test_download_verifies_cache_and_keeps_previous_file_on_bad_hash(
    builder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "network.nnue"
    target.write_bytes(b"previous")
    digest = hashlib.sha256(b"verified").hexdigest()
    calls = []

    def fetch(*args: str) -> None:
        calls.append(args)
        Path(args[-1]).write_bytes(b"wrong")

    monkeypatch.setattr(builder, "run", fetch)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        builder.download("https://example.com/network", target, digest)
    assert target.read_bytes() == b"previous"
    assert not target.with_suffix(".nnue.part").exists()
    assert len(calls) == 1
    target.write_bytes(b"verified")
    builder.download("https://example.com/network", target, digest)
    assert len(calls) == 1


def test_download_publishes_only_verified_complete_file(
    builder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "source.tar.gz"

    def fetch(*args: str) -> None:
        Path(args[-1]).write_bytes(b"verified")

    monkeypatch.setattr(builder, "run", fetch)
    builder.download("https://example.com/source", target, hashlib.sha256(b"verified").hexdigest())
    assert target.read_bytes() == b"verified"
    assert not target.with_suffix(".gz.part").exists()

"""Packaging inputs remain pinned and incomplete downloads never become resources."""

import hashlib
import importlib.util
import shutil
import tarfile
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


def test_web_notices_are_required_before_building(
    builder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder.sys, "platform", "darwin")
    monkeypatch.setattr(builder.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(builder, "run", lambda *args: pytest.fail("Build started before preflight"))
    with pytest.raises(SystemExit, match="Build the web client first"):
        builder.main()
    assert not (tmp_path / "build").exists()


def test_build_only_copies_pinned_inputs_from_a_dirty_cache(
    builder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dockerfile = (builder.ROOT / "Dockerfile").read_text()
    inputs = builder.pinned_engine_files(dockerfile)
    networks = {name for name, _, _ in inputs if name.endswith(".nnue")}
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "Dockerfile").write_text(dockerfile)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        (root / name).write_text(name)
    web = root / "apps" / "scan64-web" / "dist"
    web.mkdir(parents=True)
    (web / "THIRD_PARTY_LICENSES.md").write_text("web notices")
    source_tree = tmp_path / "fixture" / "Stockfish-sf_18"
    (source_tree / "src").mkdir(parents=True)
    (source_tree / "Copying.txt").write_text("GPL")
    source_archive = tmp_path / "source.tar.gz"
    with tarfile.open(source_archive, "w:gz") as archive:
        archive.add(source_tree, arcname=source_tree.name)
    stage = tmp_path / "cache"
    cached_engine = stage / "stockfish"
    cached_engine.mkdir(parents=True)
    resources = tmp_path / "resources"
    (resources / "stockfish").mkdir(parents=True)
    for name in ("old.nnue", "network.nnue.part", "unexpected.txt"):
        (cached_engine / name).write_text("unverified cached input")
        (resources / "stockfish" / name).write_text("previous staged input")

    def fetch(url: str, destination: Path, digest: str) -> None:
        if destination.name == "stockfish-sf_18.tar.gz":
            shutil.copy2(source_archive, destination)
        else:
            destination.write_text(destination.name)

    def build(*args: str) -> None:
        if args[0] == "make":
            source = Path(args[2])
            assert {item.name for item in source.iterdir()} == networks
            (source / "stockfish").write_text("newly built engine")
        elif "PyInstaller" in args:
            frozen = root / "dist" / "scan64-server"
            frozen.mkdir(parents=True)
            (frozen / "scan64-server").write_text("frozen server")
        else:
            assert args == ("xcode-select", "-p")

    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder, "STAGE", stage)
    monkeypatch.setattr(builder, "RESOURCES", resources)
    monkeypatch.setattr(builder.sys, "platform", "darwin")
    monkeypatch.setattr(builder.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(builder.shutil, "which", lambda tool: tool)
    monkeypatch.setattr(builder, "download", fetch)
    monkeypatch.setattr(builder, "python_notices", lambda: None)
    monkeypatch.setattr(builder, "run", build)
    builder.main()
    assert {item.name for item in (resources / "stockfish").iterdir()} == {
        *(name for name, _, _ in inputs),
        "stockfish",
        "Copying.txt",
    }
    assert (resources / "stockfish" / "stockfish").read_text() == "newly built engine"
    assert (resources / "notices" / "WEB_THIRD_PARTY_LICENSES.md").read_text() == "web notices"

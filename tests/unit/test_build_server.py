"""Packaging inputs remain pinned and incomplete downloads never become resources."""

import hashlib
import http.client
import importlib.util
import io
import json
import shutil
import tarfile
import urllib.error
from pathlib import Path
from types import ModuleType
from typing import Any

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

    def fetch(request: object, *, timeout: int) -> io.BytesIO:
        calls.append((request, timeout))
        return io.BytesIO(b"wrong")

    monkeypatch.setattr(builder.urllib.request, "urlopen", fetch)
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

    monkeypatch.setattr(
        builder.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"verified")
    )
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


@pytest.mark.parametrize(
    ("system", "machine", "make", "arch", "compiler", "suffix"),
    [
        ("darwin", "arm64", "make", "apple-silicon", "clang", ""),
        ("win32", "AMD64", "mingw32-make", "x86-64-sse41-popcnt", "mingw", ".exe"),
        ("linux", "x86_64", "make", "x86-64-sse41-popcnt", "gcc", ""),
    ],
)
def test_build_only_copies_pinned_inputs_from_a_dirty_cache(
    builder: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
    make: str,
    arch: str,
    compiler: str,
    suffix: str,
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
    public = root / "apps" / "scan64-web" / "public"
    (public / "fonts").mkdir(parents=True)
    for name in builder.FONT_LICENSES:
        (public / "fonts" / name).write_text("SIL OPEN FONT LICENSE")
    (public / "lucide-LICENSE.txt").write_text("ISC")
    rust = tmp_path / "rust"
    rust.mkdir()
    for name in ("rust-licenses.html", "rust-licenses.json"):
        (rust / name).write_text("verified Rust notices")
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
        if args[0] == make:
            source = Path(args[2])
            assert {item.name for item in source.iterdir()} == networks
            assert f"ARCH={arch}" in args
            assert f"COMP={compiler}" in args
            if system == "win32":
                assert "COMPCXX=g++" in args
                assert "EXTRALDFLAGS=-Wl,--subsystem,windows" in args
            (source / f"stockfish{suffix}").write_text("newly built engine")
        elif "PyInstaller" in args:
            frozen = root / "dist" / "scan64-server"
            frozen.mkdir(parents=True)
            (frozen / f"scan64-server{suffix}").write_text("frozen server")
        else:
            assert args == ("xcode-select", "-p")

    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder, "STAGE", stage)
    monkeypatch.setattr(builder, "RESOURCES", resources)
    monkeypatch.setattr(builder.sys, "platform", system)
    monkeypatch.setattr(builder.platform, "machine", lambda: machine)
    monkeypatch.setattr(builder.shutil, "which", lambda tool: tool)
    monkeypatch.setattr(builder, "download", fetch)
    monkeypatch.setattr(builder, "python_notices", lambda: None)
    monkeypatch.setattr(builder, "rust_notices", lambda: rust)
    monkeypatch.setattr(
        builder.subprocess, "check_output", lambda *args, **kwargs: "x86_64-w64-mingw32\n"
    )
    monkeypatch.setattr(builder, "run", build)
    builder.main()
    assert {item.name for item in (resources / "stockfish").iterdir()} == {
        *(name for name, _, _ in inputs),
        f"stockfish{suffix}",
        "Copying.txt",
    }
    assert (resources / "stockfish" / f"stockfish{suffix}").read_text() == "newly built engine"
    assert (resources / "notices" / "WEB_THIRD_PARTY_LICENSES.md").read_text() == "web notices"
    assert (resources / "notices" / "rust-licenses.html").read_text() == "verified Rust notices"
    assert (resources / "notices" / "rust-licenses.json").read_text() == "verified Rust notices"
    assert (resources / "notices" / "lucide-LICENSE.txt").read_text() == "ISC"
    for name in builder.FONT_LICENSES:
        assert (resources / "notices" / "fonts" / name).read_text() == "SIL OPEN FONT LICENSE"


@pytest.mark.parametrize(
    "error", [urllib.error.URLError("TLS EOF"), http.client.IncompleteRead(b"x")]
)
def test_download_retries_interrupted_transfer_from_empty_temporary_file(
    builder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    destination = tmp_path / "source.tar.gz"
    destination.write_bytes(b"previous")
    calls = 0
    sleeps = []

    class InterruptedResponse(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            if self.tell():
                raise error
            return super().read(2)

    def open_url(request: object, *, timeout: int) -> io.BytesIO:
        nonlocal calls
        calls += 1
        assert timeout == 60
        assert destination.read_bytes() == b"previous"
        return InterruptedResponse(b"partial") if calls == 1 else io.BytesIO(b"verified")

    monkeypatch.setattr(builder.urllib.request, "urlopen", open_url)
    monkeypatch.setattr(builder.time, "sleep", sleeps.append)
    builder.download(
        "https://example.com/source", destination, hashlib.sha256(b"verified").hexdigest()
    )
    assert calls == 2
    assert sleeps == [1]
    assert destination.read_bytes() == b"verified"
    assert not destination.with_suffix(".gz.part").exists()


def test_exhausted_download_preserves_previous_input(
    builder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "source"
    destination.write_bytes(b"previous")
    calls = []

    def open_url(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise urllib.error.URLError("TLS EOF")

    monkeypatch.setattr(builder.urllib.request, "urlopen", open_url)
    monkeypatch.setattr(builder.time, "sleep", lambda _: None)
    with pytest.raises(urllib.error.URLError):
        builder.download("https://example.com/source", destination, "a" * 64)
    assert len(calls) == 4
    assert destination.read_bytes() == b"previous"
    assert not destination.with_suffix(".part").exists()


@pytest.mark.parametrize(
    ("system", "machine", "target", "optimization"),
    [
        ("darwin", "arm64", "aarch64-apple-darwin", "pgo+lto"),
        ("win32", "AMD64", "x86_64-pc-windows-msvc", "pgo"),
        ("linux", "x86_64", "x86_64-unknown-linux-gnu", "pgo+lto"),
    ],
)
def test_managed_python_licenses_use_exact_public_hash_checked_native_archive(
    builder: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
    target: str,
    optimization: str,
) -> None:
    import zstandard

    (tmp_path / "BUILD").write_text("20261110\n")
    stage = tmp_path / "stage"
    stage.mkdir()
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        license_file = tarfile.TarInfo("python/licenses/LICENSE.openssl.txt")
        license_file.size = len(b"full component license")
        archive.addfile(license_file, io.BytesIO(b"full component license"))
    payload = zstandard.ZstdCompressor().compress(archive_bytes.getvalue())
    digest = hashlib.sha256(payload).hexdigest()
    name = f"cpython-3.12.15+20261110-{target}-{optimization}-full.tar.zst"
    urls = []

    def open_url(request: Any, *, timeout: int) -> io.BytesIO:
        urls.append(request.full_url)
        assert not request.has_header("Authorization")
        if request.full_url.endswith("/SHA256SUMS"):
            return io.BytesIO(f"{digest}  {name}\n".encode())
        assert request.full_url.endswith("/" + name)
        return io.BytesIO(payload)

    monkeypatch.setattr(builder.sys, "platform", system)
    monkeypatch.setattr(builder.sys, "base_prefix", str(tmp_path))
    monkeypatch.setattr(builder.sys, "version_info", (3, 12, 15))
    monkeypatch.setattr(builder.platform, "machine", lambda: machine)
    monkeypatch.setattr(builder, "STAGE", stage)
    monkeypatch.setattr(builder.urllib.request, "urlopen", open_url)
    builder.python_notices()
    assert len(urls) == 2
    assert (stage / "python-licenses" / "LICENSE.openssl.txt").read_bytes() == (
        b"full component license"
    )


@pytest.mark.parametrize(("system", "machine"), [("darwin", "x86_64"), ("linux", "aarch64")])
def test_unsupported_architectures_do_not_start_building(
    builder: ModuleType, monkeypatch: pytest.MonkeyPatch, system: str, machine: str
) -> None:
    monkeypatch.setattr(builder.sys, "platform", system)
    monkeypatch.setattr(builder.platform, "machine", lambda: machine)
    with pytest.raises(SystemExit, match="requires macOS arm64, Windows x64 or Linux x64"):
        builder.build_platform()


def rust_report() -> tuple[dict[str, Any], dict[str, Any]]:
    crate = {"name": "example", "version": "1.2.3", "source": "registry+crates.io"}
    return (
        {
            "crates": [{"package": crate, "license": "MIT"}],
            "licenses": [
                {
                    "id": "MIT",
                    "text": "Permission is hereby granted <copyright holder>.",
                    "used_by": [{"crate": crate}],
                }
            ],
        },
        {"package": [crate]},
    )


def test_rust_notices_contain_full_escaped_text_and_every_crate(builder: ModuleType) -> None:
    report, lock = rust_report()
    output = builder.rust_license_html(report, lock)
    assert "example 1.2.3: MIT" in output
    assert "Permission is hereby granted &lt;copyright holder&gt;." in output
    assert "all 1 Cargo.lock packages verified" in output


@pytest.mark.parametrize("missing", ["expression", "text", "crate", "usage", "source", "version"])
def test_rust_notices_fail_closed_on_unresolved_or_uncovered_crates(
    builder: ModuleType, missing: str
) -> None:
    report, lock = rust_report()
    if missing == "expression":
        report["crates"][0]["license"] = "UNKNOWN"
    elif missing == "text":
        report["licenses"][0]["text"] = " "
    elif missing == "crate":
        report["crates"] = []
    elif missing == "usage":
        report["licenses"][0]["used_by"] = []
    else:
        lock["package"] = [{**lock["package"][0], missing: "different"}]
    with pytest.raises(ValueError, match="Rust"):
        builder.rust_license_html(report, lock)


def test_rust_license_generator_is_pinned_locked_and_fail_closed(
    builder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, _ = rust_report()
    shell = tmp_path / "apps" / "scan64-desktop" / "src-tauri"
    shell.mkdir(parents=True)
    (shell / "Cargo.lock").write_text(
        '[[package]]\nname = "example"\nversion = "1.2.3"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
    )
    (shell / "Cargo.toml").write_text(
        '[package]\nname = "scan64-desktop"\nversion = "0.0.0"\n'
        'edition = "2021"\nlicense = "GPL-3.0-or-later"\n'
    )
    report["crates"][0]["package"]["source"] = (
        "registry+https://github.com/rust-lang/crates.io-index"
    )
    metadata_calls = []

    def metadata(args: list[str], **kwargs: Any) -> None:
        metadata_calls.append(args)
        assert args[:2] == ["cargo", "metadata"]
        assert kwargs["stdout"] == builder.subprocess.DEVNULL

    monkeypatch.setattr(builder.subprocess, "run", metadata)
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "STAGE", tmp_path)
    monkeypatch.setattr(
        builder.subprocess, "check_output", lambda *args, **kwargs: "cargo-about 0.8.2\n"
    )
    calls = []

    def generate(*args: str) -> None:
        calls.append(args)
        assert args[:2] == ("cargo-about", "generate")
        assert {"--locked", "--workspace", "--fail"}.issubset(args)
        assert args[args.index("--format") + 1] == "json"
        assert "--target" not in args
        Path(args[args.index("--output-file") + 1]).write_text(json.dumps(report))

    monkeypatch.setattr(builder, "run", generate)
    output = builder.rust_notices()
    assert (output / "rust-licenses.html").is_file()
    assert (output / "rust-licenses.json").is_file()
    assert len(calls) == 1
    assert len(metadata_calls) == 1
    monkeypatch.setattr(
        builder.subprocess, "check_output", lambda *args, **kwargs: "cargo-about 0.8.3\n"
    )
    with pytest.raises(ValueError, match="Install cargo-about 0.8.2"):
        builder.rust_notices()
    assert len(calls) == 1


def test_license_inventory_activates_all_exact_pins_without_default_features(
    builder: ModuleType, tmp_path: Path
) -> None:
    package = {
        "name": "scan64-desktop",
        "version": "0.0.0",
        "edition": "2021",
        "license": "GPL-3.0-or-later",
    }
    source = tmp_path / "source with spaces" / "main.rs"
    registry = "registry+https://github.com/rust-lang/crates.io-index"
    lock = {
        "package": [
            {"name": "scan64-desktop", "version": "0.0.0"},
            {"name": "optional", "version": "1.2.3", "source": registry, "checksum": "a" * 64},
            {"name": "optional", "version": "2.3.4", "source": registry, "checksum": "b" * 64},
        ]
    }
    manifest = builder.tomllib.loads(builder.license_inventory_manifest(package, lock, source))
    assert manifest["bin"] == [{"name": "scan64-desktop", "path": str(source)}]
    assert list(manifest["dependencies"].values()) == [
        {"package": "optional", "version": "=1.2.3", "default-features": False},
        {"package": "optional", "version": "=2.3.4", "default-features": False},
    ]
    changed = {"package": [{**item} for item in lock["package"]]}
    changed["package"][1]["checksum"] = "c" * 64
    assert builder.locked_inputs(lock) != builder.locked_inputs(changed)


def test_license_inventory_rejects_unresolved_source(builder: ModuleType, tmp_path: Path) -> None:
    package = {
        "name": "scan64-desktop",
        "version": "0.0.0",
        "edition": "2021",
        "license": "GPL-3.0-or-later",
    }
    for source in ("git+https://example.com/crate", ""):
        lock = {"package": [{"name": "unknown", "version": "1.0.0", "source": source}]}
        with pytest.raises(ValueError, match="Rust license inventory"):
            builder.license_inventory_manifest(package, lock, tmp_path / "main.rs")

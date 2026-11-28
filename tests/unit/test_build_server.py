"""Packaging inputs remain pinned and incomplete downloads never become resources."""

import hashlib
import http.client
import importlib.util
import io
import json
import re
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


def test_engine_pins_cover_every_source_network_and_licence(builder: ModuleType) -> None:
    dockerfile = (builder.ROOT / "Dockerfile").read_text()
    pins = builder.pinned_engine_files(dockerfile)
    assert {engine: {item[0] for item in files} for engine, files in pins.items()} == {
        "stockfish": {"stockfish-sf_18.tar.gz", "nn-c288c895ea92.nnue", "nn-37f18f62d772.nnue"},
        "caissa": {"caissa-2.0.tar.gz", "eval-ml-8-152B-spsa.pnn", "Caissa-Nets-LICENSE"},
        "arasan": {"arasan-v26.1.tar.gz", "fathom-c9c6fef0.tar.gz"},
    }
    for name, url, digest in (item for files in pins.values() for item in files):
        assert re.fullmatch(r"[0-9a-f]{64}", digest), name
        with pytest.raises(ValueError, match=f"Dockerfile pin missing: {re.escape(name)}"):
            builder.pinned_engine_files(dockerfile.replace(digest, "untrusted"))
        with pytest.raises(ValueError, match=f"Dockerfile pin missing: {re.escape(name)}"):
            builder.pinned_engine_files(dockerfile.replace(url, "https://example.com"))
    network = builder.dockerfile_digest(dockerfile, builder.ARASAN_NETWORK)
    assert network == "1f98bc6c1ac615646d66d409e138225c56765ad0c1065f1ce20a22a56c631927"
    # A second pin for the same name is ambiguous, never "the first one wins".
    line = f"RUN echo '{'0' * 64}  /out/{builder.ARASAN_NETWORK}' | sha256sum --check\n"
    with pytest.raises(ValueError, match="Dockerfile pin missing"):
        builder.dockerfile_digest(dockerfile + line, builder.ARASAN_NETWORK)


def test_engine_pins_bind_to_the_actual_download_and_check(builder: ModuleType) -> None:
    dockerfile = (builder.ROOT / "Dockerfile").read_text()
    url = "https://codeload.github.com/Witek902/Caissa/tar.gz/refs/tags/2.0"
    # A comment never pins a URL: the real download now fetches something else.
    moved = dockerfile.replace(url, "https://example.com/caissa.tar.gz") + f"# {url}\n"
    with pytest.raises(ValueError, match="Dockerfile pin missing: caissa-2.0.tar.gz"):
        builder.pinned_engine_files(moved)
    # A comment never pins a digest either.
    check = "echo 'c3ac41d0b8c2a9c9cd5259c2a50b63bd01fb470ffc85afef7691fe67a5b5e4c7  "
    commented = dockerfile.replace(check, "# " + check, 1)
    assert commented != dockerfile
    with pytest.raises(ValueError, match="Dockerfile pin missing: caissa-2.0.tar.gz"):
        builder.pinned_engine_files(commented)
    # The check must cover the downloaded path, not another file of the same name.
    elsewhere = dockerfile.replace(
        "--output /out/caissa-2.0.tar.gz", "--output /tmp/caissa-2.0.tar.gz", 1
    )
    with pytest.raises(ValueError, match="Dockerfile pin missing: caissa-2.0.tar.gz"):
        builder.pinned_engine_files(elsewhere)
    digest = "c3ac41d0b8c2a9c9cd5259c2a50b63bd01fb470ffc85afef7691fe67a5b5e4c7"
    pinned = f"{check}/out/caissa-2.0.tar.gz' | sha256sum --check"
    # A neutralised check never pins.
    with pytest.raises(ValueError, match="Dockerfile pin missing: caissa-2.0.tar.gz"):
        builder.pinned_engine_files(dockerfile.replace(pinned, pinned + " || true"))
    # A check before its download never pins.
    swapped = dockerfile.replace(pinned, "true").replace(
        "      --output /out/caissa-2.0.tar.gz && \\\n",
        "      --output /out/caissa-2.0.tar.gz && \\\n    " + pinned.strip() + " && \\\n",
    )
    builder.pinned_engine_files(swapped)  # the control: the same check after the download
    before = dockerfile.replace(pinned, "true").replace(
        "RUN mkdir /out && \\\n    curl --fail --location --retry 3 \\\n"
        "      https://codeload.github.com/Witek902/Caissa",
        "RUN mkdir /out && \\\n    " + pinned.strip() + " && \\\n"
        "    curl --fail --location --retry 3 \\\n"
        "      https://codeload.github.com/Witek902/Caissa",
    )
    assert before != dockerfile.replace(pinned, "true")
    with pytest.raises(ValueError, match="Dockerfile pin missing: caissa-2.0.tar.gz"):
        builder.pinned_engine_files(before)
    # A download in a stage no COPY --from uses never reaches the image.
    stage = (
        "FROM scratch AS dead\n"
        f"RUN curl --fail --location --retry 3 {url} --output /x/caissa-2.0.tar.gz && "
        f"echo '{digest}  /x/caissa-2.0.tar.gz' | sha256sum --check\n"
    )
    moved_out = (
        dockerfile.replace(url, "https://example.com/caissa.tar.gz")
        .replace("--output /out/caissa-2.0.tar.gz", "--output /out/other.tar.gz", 1)
        .replace(pinned, pinned.replace("caissa-2.0", "other"))
    )
    runtime = moved_out.index("FROM python:")
    dead = moved_out[:runtime] + stage + moved_out[runtime:]
    with pytest.raises(ValueError, match="Dockerfile pin missing: caissa-2.0.tar.gz"):
        builder.pinned_engine_files(dead)


def test_makefile_list_reads_continuation_lines(builder: ModuleType) -> None:
    makefile = "A = x.cpp \\\ny.cpp \\\nz.cpp\n\nARASANX_SOURCES = a.cpp b.cpp \\\nc.cpp\nB = d\n"
    assert builder.makefile_list(makefile, "ARASANX_SOURCES") == ["a.cpp", "b.cpp", "c.cpp"]
    with pytest.raises(ValueError, match="Makefile variable missing"):
        builder.makefile_list(makefile, "UNIT_SOURCES")


def test_source_archive_needs_one_top_level_directory(builder: ModuleType, tmp_path: Path) -> None:
    for name in ("first", "second"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "file").write_text(name)
    archive_path = tmp_path / "two.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(tmp_path / "first", arcname="first")
        archive.add(tmp_path / "second", arcname="second")
    with pytest.raises(ValueError, match="no single top-level directory"):
        builder.extract(archive_path, tmp_path / "out")


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


def source_archive(path: Path, tree: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(text)
    with tarfile.open(path, "w:gz") as archive:
        archive.add(tree, arcname=tree.name)


@pytest.mark.parametrize(
    ("system", "machine", "make", "arch", "compiler", "suffix", "cxx", "caissa"),
    [
        ("darwin", "arm64", "make", "apple-silicon", "clang", "", "clang++", "neon"),
        (
            "win32",
            "AMD64",
            "mingw32-make",
            "x86-64-sse41-popcnt",
            "mingw",
            ".exe",
            "g++",
            "sse4",
        ),
        ("linux", "x86_64", "make", "x86-64-sse41-popcnt", "gcc", "", "g++", "sse4"),
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
    cxx: str,
    caissa: str,
) -> None:
    dockerfile = (builder.ROOT / "Dockerfile").read_text()
    pins = builder.pinned_engine_files(dockerfile)
    networks = {name for name, _, _ in pins["stockfish"] if name.endswith(".nnue")}
    root = tmp_path / "checkout"
    root.mkdir()
    # The in-archive Arasan network is checked against the checkout's Dockerfile.
    arasan_network = builder.dockerfile_digest(dockerfile, builder.ARASAN_NETWORK)
    fixture_network = hashlib.sha256(b"fixture network").hexdigest()
    (root / "Dockerfile").write_text(dockerfile.replace(arasan_network, fixture_network))
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
    fixture = tmp_path / "fixture"
    archives = tmp_path / "archives"
    archives.mkdir()
    source_tree = fixture / "Stockfish-sf_18"
    (source_tree / "src").mkdir(parents=True)
    source_archive(archives / "stockfish-sf_18.tar.gz", source_tree, {"Copying.txt": "GPL"})
    source_archive(
        archives / "caissa-2.0.tar.gz",
        fixture / "Caissa-2.0",
        {
            "LICENSE": "Caissa MIT",
            "src/backend/syzygy/LICENSE": "Fathom MIT in Caissa",
            "src/makefile": "FLAGS = $(WFLAGS) -flto -flto-partition=one -O3 -fno-exceptions\n",
        },
    )
    source_archive(
        archives / "arasan-v26.1.tar.gz",
        fixture / "arasan-chess-26.1",
        {
            "LICENSE": "Arasan MIT",
            "src/Makefile": "ARASANX_SOURCES = arasanx.cpp \\\nsearch.cpp\n",
            **{
                f"src/{name}": f"before\n{old}\nafter\n"
                for name, old, _ in builder.ARASAN_MINGW_EDITS
            },
            f"network/{builder.ARASAN_NETWORK}": "fixture network",
        },
    )
    source_archive(
        archives / "fathom-c9c6fef0.tar.gz",
        fixture / "Fathom-c9c6fef0dddc05d2e242c183acf5833149ab676d",
        {"LICENSE": "Fathom MIT", "src/tbprobe.c": "tablebase probe"},
    )
    stage = tmp_path / "cache"
    resources = tmp_path / "resources"
    for engine in pins:
        (stage / engine).mkdir(parents=True)
        (resources / engine).mkdir(parents=True)
        for name in ("old.nnue", "network.nnue.part", "unexpected.txt"):
            (stage / engine / name).write_text("unverified cached input")
            (resources / engine / name).write_text("previous staged input")
    calls: list[tuple[str, ...]] = []

    def fetch(url: str, destination: Path, digest: str) -> None:
        if (archives / destination.name).is_file():
            shutil.copy2(archives / destination.name, destination)
        else:
            destination.write_text(destination.name)

    def build(*args: str) -> None:
        calls.append(args)
        if args[0] == make and f"ARCH={arch}" in args:
            source = Path(args[2])
            assert {item.name for item in source.iterdir()} == networks
            assert f"COMP={compiler}" in args
            if system == "win32":
                assert "COMPCXX=g++" in args
                assert "EXTRALDFLAGS=-Wl,--subsystem,windows" in args
            (source / f"stockfish{suffix}").write_text("newly built engine")
        elif args[0] == make:
            source = Path(args[2])
            assert args[3] == caissa
            assert f"CC={cxx}" in args
            assert "EVALFILE=../data/neuralNets/eval-ml-8-152B-spsa.pnn" in args
            network = source.parent / "data" / "neuralNets" / "eval-ml-8-152B-spsa.pnn"
            assert network.read_text() == "eval-ml-8-152B-spsa.pnn"
            windowless = "LIBS=-pthread -Wl,--subsystem,windows"
            assert (windowless in args) == (system == "win32")
            flags = [arg for arg in args if arg.startswith("FLAGS=")]
            if cxx == "clang++":
                assert flags == ["FLAGS=$(WFLAGS) -flto -O3"]
            else:
                assert flags == []
            (source / f"caissa-2.0-built{suffix}").write_text("newly built Caissa")
        elif args[0] == cxx:
            sources = {Path(arg).name for arg in args if arg.endswith((".cpp", ".c"))}
            assert sources == {"arasanx.cpp", "search.cpp", "syzygy.cpp", "tbprobe.c"}
            assert "-DNETWORK=arasanv8-20261005.nnue" in args
            assert ("-Wl,--subsystem,windows" in args) == (system == "win32")
            assert ("-static" in args) == (system == "win32")
            assert ("-mpopcnt" in args) == (system != "darwin")
            assert ("-DNEON" in args) == (system == "darwin")
            src = Path(args[args.index("-o") - 1]).parents[2]
            for name, old, new in builder.ARASAN_MINGW_EDITS:
                text = (src / name).read_text()
                assert (new in text, old in text) == (
                    (True, False) if system == "win32" else (False, True)
                )
            Path(args[args.index("-o") + 1]).write_text("newly built Arasan")
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
        *(name for name, _, _ in pins["stockfish"]),
        f"stockfish{suffix}",
        "Copying.txt",
    }
    assert {item.name for item in (resources / "caissa").iterdir()} == {
        *(name for name, _, _ in pins["caissa"]),
        f"caissa{suffix}",
        "LICENSE",
        "Fathom-LICENSE",
    }
    assert {item.name for item in (resources / "arasan").iterdir()} == {
        *(name for name, _, _ in pins["arasan"]),
        f"arasan{suffix}",
        builder.ARASAN_NETWORK,
        "LICENSE",
        "Fathom-LICENSE",
    }
    assert (resources / "stockfish" / f"stockfish{suffix}").read_text() == "newly built engine"
    assert (resources / "caissa" / f"caissa{suffix}").read_text() == "newly built Caissa"
    assert (resources / "caissa" / "Fathom-LICENSE").read_text() == "Fathom MIT in Caissa"
    assert (resources / "arasan" / f"arasan{suffix}").read_text() == "newly built Arasan"
    assert (resources / "arasan" / builder.ARASAN_NETWORK).read_text() == "fixture network"
    assert (resources / "arasan" / "Fathom-LICENSE").read_text() == "Fathom MIT"
    assert len([args for args in calls if args[0] == cxx]) == 1
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


def test_arasan_network_inside_the_archive_must_match_the_dockerfile_pin(
    builder: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = tmp_path / "arasan"
    engine.mkdir()
    source_archive(
        engine / "arasan-v26.1.tar.gz",
        tmp_path / "fixture" / "arasan-chess-26.1",
        {"LICENSE": "MIT", f"network/{builder.ARASAN_NETWORK}": "tampered network"},
    )
    source_archive(
        engine / "fathom-c9c6fef0.tar.gz",
        tmp_path / "fixture" / "Fathom-c9c6fef0",
        {"LICENSE": "MIT"},
    )
    monkeypatch.setattr(builder, "run", lambda *args: pytest.fail("Compiled an unchecked network"))
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match=f"SHA-256 mismatch: {builder.ARASAN_NETWORK}"):
        builder.build_arasan(
            builder.build_platform(), engine, source, (builder.ROOT / "Dockerfile").read_text()
        )
    assert not (engine / builder.ARASAN_NETWORK).exists()


def test_pinned_source_edits_need_exactly_one_match(builder: ModuleType, tmp_path: Path) -> None:
    source = tmp_path / "types.h"
    for text in ("no guard here\n", "#ifdef A\n#ifdef A\n"):
        source.write_text(text)
        with pytest.raises(ValueError, match="Expected one match in types.h"):
            builder.replace_once(source, "#ifdef A", "#if defined(A)")
        assert source.read_text() == text
    source.write_text("#ifdef A\n")
    builder.replace_once(source, "#ifdef A", "#if defined(A)")
    assert source.read_text() == "#if defined(A)\n"

#!/usr/bin/env python3
"""Stage native frozen servers, Stockfish and complete redistribution notices.

Run ``uv sync --locked --group build --python-preference only-managed``, build
the web client, and install ``cargo-about`` 0.8.2 with ``cargo install cargo-about
--version 0.8.2 --locked``. Then run ``uv run --no-sync python
scripts/build-server.py`` before Tauri's bundle build on each target OS.
"""

from __future__ import annotations

import hashlib
import html
import http.client
import io
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "build" / "server-inputs"
RESOURCES = ROOT / "apps" / "scan64-desktop" / "resources"
ENGINE_FILES = (
    (
        "stockfish-sf_18.tar.gz",
        "https://codeload.github.com/official-stockfish/Stockfish/tar.gz/refs/tags/sf_18",
    ),
    ("nn-c288c895ea92.nnue", "https://tests.stockfishchess.org/api/nn/nn-c288c895ea92.nnue"),
    ("nn-37f18f62d772.nnue", "https://tests.stockfishchess.org/api/nn/nn-37f18f62d772.nnue"),
)
CARGO_ABOUT_VERSION = "0.8.2"
FONT_LICENSES = ("host-grotesk-OFL.txt", "jetbrains-mono-OFL.txt")


class BuildPlatform(NamedTuple):
    engine_arch: str
    compiler: str
    compiler_binary: str
    make: str
    suffix: str
    python_target: str
    python_optimization: str


def build_platform() -> BuildPlatform:
    """Only native builds for the three supported desktop architectures."""
    target = (sys.platform, platform.machine().lower())
    if target == ("darwin", "arm64"):
        return BuildPlatform(
            "apple-silicon",
            "clang",
            "clang",
            "make",
            "",
            "aarch64-apple-darwin",
            "pgo+lto",
        )
    if target in (("win32", "amd64"), ("win32", "x86_64")):
        return BuildPlatform(
            "x86-64-sse41-popcnt",
            "mingw",
            "g++",
            "mingw32-make",
            ".exe",
            "x86_64-pc-windows-msvc",
            "pgo",
        )
    if target == ("linux", "x86_64"):
        return BuildPlatform(
            "x86-64-sse41-popcnt",
            "gcc",
            "g++",
            "make",
            "",
            "x86_64-unknown-linux-gnu",
            "pgo+lto",
        )
    raise SystemExit("Server packaging requires macOS arm64, Windows x64 or Linux x64")


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def pinned_engine_files(dockerfile: str) -> list[tuple[str, str, str]]:
    """Use the container's authoritative pins; never accept an unchecked input."""
    files = []
    for name, url in ENGINE_FILES:
        match = re.search(
            r"echo '([0-9a-f]{64})  (?:/out/|src/)" + re.escape(name) + "'", dockerfile
        )
        if match is None or url not in dockerfile:
            raise ValueError(f"Dockerfile pin missing: {name}")
        files.append((name, url, match[1]))
    return files


def fetch_url(url: str, destination: BinaryIO) -> None:
    """Retry interrupted HTTPS transfers without weakening TLS verification."""
    request = urllib.request.Request(url, headers={"User-Agent": "Scan64-desktop-build"})
    for attempt in range(4):
        destination.seek(0)
        destination.truncate()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                shutil.copyfileobj(response, destination)
            return
        except (OSError, http.client.HTTPException):
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def download(url: str, destination: Path, expected: str) -> None:
    """Verify cached inputs too; only publish a complete, checked download."""
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise ValueError(f"Invalid SHA-256 pin: {destination.name}")
    if destination.is_file():
        with destination.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() == expected:
                return
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with temporary.open("w+b") as output:
            fetch_url(url, output)
            output.seek(0)
            if hashlib.file_digest(output, "sha256").hexdigest() != expected:
                raise ValueError(f"SHA-256 mismatch: {destination.name}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def python_notices() -> None:
    """Recover the exact managed interpreter's component notices on every OS."""
    import zstandard

    target = build_platform()
    build = (Path(sys.base_prefix) / "BUILD").read_text().strip()
    version = ".".join(str(value) for value in sys.version_info[:3])
    if re.fullmatch(r"[0-9]{8}", build) is None:
        raise ValueError("Managed Python has an invalid BUILD marker")
    name = (
        f"cpython-{version}+{build}-{target.python_target}-"
        f"{target.python_optimization}-full.tar.zst"
    )
    # Public release assets avoid GitHub API rate limits and require no token.
    base_url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{build}"
    checksums = io.BytesIO()
    fetch_url(f"{base_url}/SHA256SUMS", checksums)
    matches = [
        fields[0]
        for line in checksums.getvalue().decode("utf-8").splitlines()
        if len(fields := line.split()) == 2 and fields[1].removeprefix("*") == name
    ]
    if len(matches) != 1 or re.fullmatch(r"[0-9a-f]{64}", matches[0]) is None:
        raise ValueError(f"Python full archive has no unique SHA-256 digest: {name}")
    archive_path = STAGE / name
    download(f"{base_url}/{name}", archive_path, matches[0])
    output = STAGE / "python-licenses"
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir()
    count = 0
    with (
        archive_path.open("rb") as source,
        zstandard.ZstdDecompressor().stream_reader(source) as stream,
    ):
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for member in archive:
                if not member.isfile() or not member.name.startswith("python/licenses/"):
                    continue
                relative = Path(member.name).relative_to("python/licenses")
                if ".." in relative.parts:
                    raise ValueError("Unsafe Python license archive path")
                content = archive.extractfile(member)
                assert content is not None
                destination = output / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content.read())
                count += 1
    if not count:
        raise ValueError("Python full archive did not include component licenses")
    print(f"Python {version}+{build} component licenses: {count}", flush=True)


def crate_key(package: dict[str, Any]) -> tuple[str, str, str]:
    return package["name"], package["version"], package.get("source") or ""


def rust_license_html(report: dict[str, Any], lock: dict[str, Any]) -> str:
    """Require a resolved expression and actual license text for every lock entry."""
    expected = {crate_key(package) for package in lock["package"]}
    declared = {
        crate_key(item["package"])
        for item in report["crates"]
        if item["license"].strip() and item["license"] not in ("NOASSERTION", "UNKNOWN")
    }
    covered = set()
    sections = []
    for license_entry in report["licenses"]:
        if not license_entry["text"].strip() or license_entry["id"] in (
            "",
            "NOASSERTION",
            "UNKNOWN",
        ):
            raise ValueError("Rust crate license text or SPDX identifier is unresolved")
        crates = sorted({crate_key(item["crate"]) for item in license_entry["used_by"]})
        covered.update(crates)
        users = "\n".join(
            f"<li>{html.escape(name)} {html.escape(version)}</li>" for name, version, _ in crates
        )
        sections.append(
            f"<section><h2>{html.escape(license_entry['id'])}</h2><ul>{users}</ul>"
            f"<pre>{html.escape(license_entry['text'])}</pre></section>"
        )
    if not expected or expected != declared or expected != covered:
        missing = sorted(expected - (declared & covered))
        unexpected = sorted((declared | covered) - expected)
        raise ValueError(
            f"Rust license coverage differs from Cargo.lock: missing={missing}, "
            f"unexpected={unexpected}"
        )
    inventory = "\n".join(
        f"<li>{html.escape(item['package']['name'])} "
        f"{html.escape(item['package']['version'])}: {html.escape(item['license'])}</li>"
        for item in report["crates"]
    )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        "<title>Scan64 Rust licenses</title>"
        "<style>pre{white-space:pre-wrap}body{max-width:80ch;margin:2rem auto}</style>"
        f"<h1>Rust crate licenses</h1><p>Generated by cargo-about {CARGO_ABOUT_VERSION}; "
        f"all {len(expected)} Cargo.lock packages verified, including build and "
        f"other-platform dependencies.</p><ul>{inventory}</ul>" + "\n".join(sections) + "</html>\n"
    )


def license_inventory_manifest(package: dict[str, Any], lock: dict[str, Any], source: Path) -> str:
    """Activate every locked crate for attribution, including unused optional crates."""
    lines = ["[package]"]
    for key in ("name", "version", "edition", "license"):
        lines.append(f"{key} = {json.dumps(package[key])}")
    lines.extend(
        [
            "[workspace]",
            "[[bin]]",
            f"name = {json.dumps(package['name'])}",
            f"path = {json.dumps(str(source))}",
            "[dependencies]",
        ]
    )
    for index, crate in enumerate(lock["package"]):
        if not crate.get("source"):
            if (crate["name"], crate["version"]) != (package["name"], package["version"]):
                raise ValueError("Unresolved local Rust license inventory package")
            continue
        if crate["source"] != "registry+https://github.com/rust-lang/crates.io-index":
            raise ValueError(f"Unsupported Rust license inventory source: {crate['source']}")
        lines.append(
            f"crate_{index} = {{ package = {json.dumps(crate['name'])}, "
            f"version = {json.dumps('=' + crate['version'])}, default-features = false }}"
        )
    return "\n".join(lines) + "\n"


def locked_inputs(lock: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    return {(*crate_key(crate), crate.get("checksum", "")) for crate in lock["package"]}


def rust_notices() -> Path:
    """Pin the resolver and fail closed before staging any distribution resources."""
    version = subprocess.check_output(["cargo-about", "--version"], cwd=ROOT, text=True).strip()
    if version != f"cargo-about {CARGO_ABOUT_VERSION}":
        raise ValueError(f"Install cargo-about {CARGO_ABOUT_VERSION} with --locked")
    shell = ROOT / "apps" / "scan64-desktop" / "src-tauri"
    output = STAGE / "rust-notices"
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir()
    lock_path = shell / "Cargo.lock"
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    package = tomllib.loads((shell / "Cargo.toml").read_text(encoding="utf-8"))["package"]
    # cargo-about follows activated features, but Cargo.lock also records optional
    # crates that are not compiled. A metadata-only inventory activates all pins.
    # It never builds or changes the application's manifest or lockfile.
    inventory = STAGE / "rust-license-inventory"
    shutil.rmtree(inventory, ignore_errors=True)
    inventory.mkdir()
    manifest = inventory / "Cargo.toml"
    manifest.write_text(
        license_inventory_manifest(package, lock, shell / "src" / "main.rs"), encoding="utf-8"
    )
    shutil.copy2(lock_path, inventory / "Cargo.lock")
    subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--manifest-path", str(manifest)],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    inventory_lock = tomllib.loads((inventory / "Cargo.lock").read_text(encoding="utf-8"))
    if locked_inputs(lock) != locked_inputs(inventory_lock):
        raise ValueError("Rust license inventory changed the locked source/checksum inputs")
    report_path = output / "rust-licenses.json"
    run(
        "cargo-about",
        "generate",
        "--locked",
        "--workspace",
        "--fail",
        "--format",
        "json",
        "--manifest-path",
        str(manifest),
        "--config",
        str(shell / "about.toml"),
        "--output-file",
        str(report_path),
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    (output / "rust-licenses.html").write_text(rust_license_html(report, lock), encoding="utf-8")
    return output


def main() -> None:
    target = build_platform()
    web = ROOT / "apps" / "scan64-web" / "dist" / "THIRD_PARTY_LICENSES.md"
    if not web.is_file():
        raise SystemExit("Build the web client first to include THIRD_PARTY_LICENSES.md")
    public = ROOT / "apps" / "scan64-web" / "public"
    for name in FONT_LICENSES:
        if not (public / "fonts" / name).is_file():
            raise SystemExit(f"Bundled font license missing: {name}")
    tools = [target.make, target.compiler_binary, "cargo-about"]
    if sys.platform == "darwin":
        tools.append("xcode-select")
    for tool in tools:
        if shutil.which(tool) is None:
            raise SystemExit(f"Required build tool missing: {tool}")
    if sys.platform == "darwin":
        run("xcode-select", "-p")
    elif sys.platform == "win32":
        compiler_target = subprocess.check_output(
            ["g++", "-dumpmachine"], cwd=ROOT, text=True
        ).strip()
        if not compiler_target.startswith("x86_64-") or "mingw" not in compiler_target:
            raise SystemExit("Windows Stockfish requires MinGW64 g++ on PATH")
    STAGE.mkdir(parents=True, exist_ok=True)
    engine = STAGE / "stockfish"
    engine.mkdir(exist_ok=True)
    inputs = pinned_engine_files((ROOT / "Dockerfile").read_text())
    for name, url, digest in inputs:
        download(url, engine / name, digest)
    source = STAGE / "source"
    shutil.rmtree(source, ignore_errors=True)
    source.mkdir()
    with tarfile.open(engine / "stockfish-sf_18.tar.gz", "r:gz") as archive:
        archive.extractall(source, filter="data")
    tree = source / "Stockfish-sf_18"
    for name, _, _ in inputs:
        if name.endswith(".nnue"):
            shutil.copy2(engine / name, tree / "src" / name)
    command = [
        target.make,
        "-C",
        str(tree / "src"),
        "-j2",
        "build",
        f"ARCH={target.engine_arch}",
        f"COMP={target.compiler}",
    ]
    if sys.platform == "win32":
        # MSYS2 MINGW64 exposes native g++, not the cross-compiler's prefixed name.
        command.append("COMPCXX=g++")
        # Stockfish is also a child without a console. Its CRT still receives
        # the UCI pipes through STARTF_USESTDHANDLES; upstream MinGW adds -static.
        command.append("EXTRALDFLAGS=-Wl,--subsystem,windows")
    run(*command)
    binary = "stockfish" + target.suffix
    shutil.copy2(tree / "src" / binary, engine / binary)
    shutil.copy2(tree / "Copying.txt", engine / "Copying.txt")
    python_notices()
    rust = rust_notices()
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "scan64-server.spec")
    # Stage only complete build outputs; no system interpreter or PATH engine is needed at runtime.
    RESOURCES.mkdir(parents=True, exist_ok=True)
    for name in ("scan64-server", "stockfish", "notices"):
        shutil.rmtree(RESOURCES / name, ignore_errors=True)
    shutil.copytree(ROOT / "dist" / "scan64-server", RESOURCES / "scan64-server")
    engine_resources = RESOURCES / "stockfish"
    engine_resources.mkdir()
    for name in [item[0] for item in inputs] + [binary, "Copying.txt"]:
        shutil.copy2(engine / name, engine_resources / name)
    notices = RESOURCES / "notices"
    notices.mkdir()
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, notices / name)
    shutil.copy2(web, notices / "WEB_THIRD_PARTY_LICENSES.md")
    for name in ("rust-licenses.html", "rust-licenses.json"):
        shutil.copy2(rust / name, notices / name)
    fonts = notices / "fonts"
    fonts.mkdir()
    for name in FONT_LICENSES:
        shutil.copy2(public / "fonts" / name, fonts / name)
    shutil.copy2(public / "lucide-LICENSE.txt", notices / "lucide-LICENSE.txt")
    print(f"Tauri resources ready: {RESOURCES}", flush=True)


if __name__ == "__main__":
    main()

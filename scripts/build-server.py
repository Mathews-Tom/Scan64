#!/usr/bin/env python3
"""Stage the frozen server and redistributable resources for Tauri (macOS arm64).

Run with ``uv run --group build python scripts/build-server.py`` before the
Tauri build command in README (with ``src-tauri/tauri.bundle.conf.json``).
Windows/Linux packaging is deliberately not implemented until M83.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

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


def download(url: str, destination: Path, expected: str) -> None:
    """Verify cached inputs too; only publish a complete, checked download."""
    if destination.is_file():
        with destination.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() == expected:
                return
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        run("curl", "--fail", "--location", "--retry", "3", url, "--output", str(temporary))
        with temporary.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                raise ValueError(f"SHA-256 mismatch: {destination.name}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def python_notices() -> None:
    """Recover managed Python's statically linked component notices, as before."""
    import zstandard

    build = (Path(sys.base_prefix) / "BUILD").read_text().strip()
    version = ".".join(str(value) for value in sys.version_info[:3])
    name = f"cpython-{version}+{build}-aarch64-apple-darwin-pgo+lto-full.tar.zst"
    request = urllib.request.Request(
        f"https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{build}",
        headers={"Accept": "application/vnd.github+json"},
    )
    if token := os.environ.get("GITHUB_TOKEN"):
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    asset = next(item for item in release["assets"] if item["name"] == name)
    digest = asset["digest"].removeprefix("sha256:")
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("Python full archive has no SHA-256 digest")
    archive_path = STAGE / "python-full.tar.zst"
    download(asset["browser_download_url"], archive_path, digest)
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


def main() -> None:
    if (sys.platform, platform.machine()) != ("darwin", "arm64"):
        raise SystemExit(
            "Server packaging currently requires macOS arm64; Windows/Linux arrive in M83"
        )
    for tool in ("curl", "make", "clang", "xcode-select"):
        if shutil.which(tool) is None:
            raise SystemExit(f"Required build tool missing: {tool}")
    run("xcode-select", "-p")
    STAGE.mkdir(parents=True, exist_ok=True)
    engine = STAGE / "stockfish"
    engine.mkdir(exist_ok=True)
    for name, url, digest in pinned_engine_files((ROOT / "Dockerfile").read_text()):
        download(url, engine / name, digest)
    source = STAGE / "source"
    shutil.rmtree(source, ignore_errors=True)
    source.mkdir()
    with tarfile.open(engine / "stockfish-sf_18.tar.gz", "r:gz") as archive:
        archive.extractall(source, filter="data")
    tree = source / "Stockfish-sf_18"
    for network in engine.glob("*.nnue"):
        shutil.copy2(network, tree / "src" / network.name)
    run("make", "-C", str(tree / "src"), "-j2", "build", "ARCH=apple-silicon", "COMP=clang")
    shutil.copy2(tree / "src" / "stockfish", engine / "stockfish")
    shutil.copy2(tree / "Copying.txt", engine / "Copying.txt")
    python_notices()
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "scan64-server.spec")
    # Stage only complete build outputs; no system interpreter or PATH engine is needed at runtime.
    RESOURCES.mkdir(parents=True, exist_ok=True)
    for name in ("scan64-server", "stockfish", "notices"):
        shutil.rmtree(RESOURCES / name, ignore_errors=True)
    shutil.copytree(ROOT / "dist" / "scan64-server", RESOURCES / "scan64-server")
    shutil.copytree(engine, RESOURCES / "stockfish")
    notices = RESOURCES / "notices"
    notices.mkdir()
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, notices / name)
    web = ROOT / "apps" / "scan64-web" / "dist" / "THIRD_PARTY_LICENSES.md"
    if not web.is_file():
        raise SystemExit("Build the web client first to include THIRD_PARTY_LICENSES.md")
    shutil.copy2(web, notices / "WEB_THIRD_PARTY_LICENSES.md")
    print(f"Tauri resources ready: {RESOURCES}", flush=True)


if __name__ == "__main__":
    main()

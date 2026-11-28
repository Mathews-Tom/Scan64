#!/usr/bin/env python3
"""Stage native frozen servers, the bundled engines and complete redistribution notices.

The engines are Stockfish 18, Caissa 2.0 (analysis) and Arasan 26.1 (bot ladder),
each built on the host from its SHA-256-pinned source archive and networks. The
pins are the Dockerfile's; this script never accepts an input the image would not.

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
# Downloaded inputs per engine; each one's SHA-256 is read from the Dockerfile.
ENGINE_FILES = {
    "stockfish": (
        (
            "stockfish-sf_18.tar.gz",
            "https://codeload.github.com/official-stockfish/Stockfish/tar.gz/refs/tags/sf_18",
        ),
        ("nn-c288c895ea92.nnue", "https://tests.stockfishchess.org/api/nn/nn-c288c895ea92.nnue"),
        ("nn-37f18f62d772.nnue", "https://tests.stockfishchess.org/api/nn/nn-37f18f62d772.nnue"),
    ),
    "caissa": (
        ("caissa-2.0.tar.gz", "https://codeload.github.com/Witek902/Caissa/tar.gz/refs/tags/2.0"),
        (
            "eval-ml-8-152B-spsa.pnn",
            "https://github.com/Witek902/Caissa-Nets/releases/download/"
            "eval-ml-8-152B-spsa/eval-ml-8-152B-spsa.pnn",
        ),
        (
            "Caissa-Nets-LICENSE",
            "https://raw.githubusercontent.com/Witek902/Caissa-Nets/"
            "8098255ee7b6608d9698ce0c725b743c74ab2f9b/LICENSE",
        ),
    ),
    "arasan": (
        (
            "arasan-v26.1.tar.gz",
            "https://codeload.github.com/jdart1/arasan-chess/tar.gz/refs/tags/v26.1",
        ),
        (
            "fathom-c9c6fef0.tar.gz",
            "https://codeload.github.com/jdart1/Fathom/tar.gz/"
            "c9c6fef0dddc05d2e242c183acf5833149ab676d",
        ),
    ),
}
CAISSA_NETWORK = "eval-ml-8-152B-spsa.pnn"
# Inside the Arasan source archive; Arasan loads it from its executable's directory.
ARASAN_NETWORK = "arasanv8-20261005.nnue"
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
    cxx: str
    caissa_target: str


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
            "clang++",
            "neon",
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
            "g++",
            "sse4",
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
            "g++",
            "sse4",
        )
    raise SystemExit("Server packaging requires macOS arm64, Windows x64 or Linux x64")


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def dockerfile_digest(dockerfile: str, name: str) -> str:
    """The one SHA-256 the Dockerfile checks for a file of this name."""
    matches = re.findall(
        r"echo '([0-9a-f]{64})  (?:\S*/)?" + re.escape(name) + r"' \| sha256sum --check",
        dockerfile,
    )
    if len(matches) != 1:
        raise ValueError(f"Dockerfile pin missing: {name}")
    return matches[0]


def pinned_engine_files(dockerfile: str) -> dict[str, list[tuple[str, str, str]]]:
    """Use the container's authoritative pins; never accept an unchecked input."""
    pins = {}
    for engine, files in ENGINE_FILES.items():
        pins[engine] = []
        for name, url in files:
            if url not in dockerfile:
                raise ValueError(f"Dockerfile pin missing: {name}")
            pins[engine].append((name, url, dockerfile_digest(dockerfile, name)))
    return pins


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def extract(archive_path: Path, destination: Path) -> Path:
    """Unpack a source archive safely and return its single top-level directory."""
    with tarfile.open(archive_path, "r:gz") as archive:
        roots = {Path(member.name).parts[0] for member in archive.getmembers()}
        if len(roots) != 1:
            raise ValueError(f"Source archive has no single top-level directory: {archive_path}")
        archive.extractall(destination, filter="data")
    return destination / roots.pop()


def makefile_list(makefile: str, variable: str) -> list[str]:
    """A plain `NAME = a b \\` list from an upstream makefile, continuation lines included."""
    match = re.search(rf"^{re.escape(variable)} = ((?:.*\\\n)*.*)$", makefile, re.MULTILINE)
    if match is None:
        raise ValueError(f"Makefile variable missing: {variable}")
    return match[1].replace("\\\n", " ").split()


def build_caissa(target: BuildPlatform, engine: Path, source: Path) -> None:
    """src/makefile build with the verified network embedded through EVALFILE."""
    tree = extract(engine / "caissa-2.0.tar.gz", source)
    networks = tree / "data" / "neuralNets"
    networks.mkdir(parents=True, exist_ok=True)
    shutil.copy2(engine / CAISSA_NETWORK, networks / CAISSA_NETWORK)
    command = [
        target.make,
        "-C",
        str(tree / "src"),
        target.caissa_target,
        f"CC={target.cxx}",
        # GNU make 3.81 (macOS) cannot read version.txt with $(file <).
        "VERSION=2.0",
        f"EVALFILE=../data/neuralNets/{CAISSA_NETWORK}",
    ]
    if sys.platform == "win32":
        # The makefile links MinGW builds statically; a GUI subsystem keeps the
        # child windowless while its CRT still receives the UCI pipes.
        command.append("LIBS=-pthread -Wl,--subsystem,windows")
    run(*command)
    built = [path for path in (tree / "src").glob("caissa-2.0-*") if path.is_file()]
    if len(built) != 1:
        raise ValueError(f"Caissa build produced {len(built)} executables")
    shutil.copy2(built[0], engine / f"caissa{target.suffix}")
    shutil.copy2(tree / "LICENSE", engine / "LICENSE")
    shutil.copy2(tree / "src" / "backend" / "syzygy" / "LICENSE", engine / "Fathom-LICENSE")


def build_arasan(target: BuildPlatform, engine: Path, source: Path, dockerfile: str) -> None:
    """Compile the upstream Makefile's arasanx sources and flags in one compiler call.

    The Makefile itself needs bc, gawk and getconf for GCC and links with gold on
    Linux; the Windows runner's shell has none of them. Docker uses the Makefile.
    """
    tree = extract(engine / "arasan-v26.1.tar.gz", source)
    fathom = extract(engine / "fathom-c9c6fef0.tar.gz", source)
    shutil.copytree(fathom, tree / "src" / "syzygy", dirs_exist_ok=True)
    network = tree / "network" / ARASAN_NETWORK
    if sha256(network) != dockerfile_digest(dockerfile, ARASAN_NETWORK):
        raise ValueError(f"SHA-256 mismatch: {ARASAN_NETWORK}")
    src = tree / "src"
    sources = makefile_list((src / "Makefile").read_text(encoding="utf-8"), "ARASANX_SOURCES")
    if sys.platform == "darwin":
        simd = ["-D_MAC", "-DSIMD", "-DNEON"]
        optimization = ["-O3", "-fno-rtti", "-DNDEBUG"]
        libraries = ["-lm"]
    else:
        # BUILD_TYPE=modern, the SSE4.1/POPCNT level of the Stockfish build.
        simd = ["-DSIMD", "-DSSE2", "-msse2", "-msse3", "-DUSE_POPCNT", "-DSSSE3", "-DSSE41"]
        simd += ["-mssse3", "-msse4.1", "-msse4.2", "-mpopcnt"]
        optimization = ["-Ofast", "-fno-rtti", "-fno-enforce-eh-specs", "-DNDEBUG"]
        libraries = (
            ["-static", "-Wl,--subsystem,windows"] if sys.platform == "win32" else ["-lm", "-lrt"]
        )
    binary = engine / f"arasan{target.suffix}"
    run(
        target.cxx,
        "-std=c++17",
        "-fno-strict-aliasing",
        "-DUSE_INTRINSICS",
        "-DUSE_ASM",
        "-DSYZYGY_TBS",
        "-D_64BIT",
        "-DSMP_STATS",
        "-DARASAN_VERSION=26.1",
        f"-DNETWORK={ARASAN_NETWORK}",
        f"-I{src}",
        f"-I{src / 'nnue'}",
        *simd,
        *optimization,
        "-pthread",
        *(str(src / name) for name in [*sources, "syzygy.cpp"]),
        "-x",
        "c++",
        str(src / "syzygy" / "src" / "tbprobe.c"),
        "-o",
        str(binary),
        *libraries,
    )
    shutil.copy2(network, engine / ARASAN_NETWORK)
    shutil.copy2(tree / "LICENSE", engine / "LICENSE")
    shutil.copy2(fathom / "LICENSE", engine / "Fathom-LICENSE")


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
    tools = [target.make, target.compiler_binary, target.cxx, "cargo-about"]
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
            raise SystemExit("Windows engines require MinGW64 g++ on PATH")
    STAGE.mkdir(parents=True, exist_ok=True)
    dockerfile = (ROOT / "Dockerfile").read_text()
    pins = pinned_engine_files(dockerfile)
    for engine_name, files in pins.items():
        (STAGE / engine_name).mkdir(exist_ok=True)
        for name, url, digest in files:
            download(url, STAGE / engine_name / name, digest)
    source = STAGE / "source"
    shutil.rmtree(source, ignore_errors=True)
    source.mkdir()
    engine = STAGE / "stockfish"
    tree = extract(engine / "stockfish-sf_18.tar.gz", source)
    for name, _, _ in pins["stockfish"]:
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
    build_caissa(target, STAGE / "caissa", source)
    build_arasan(target, STAGE / "arasan", source, dockerfile)
    # Everything that accompanies each executable: pinned inputs, built files, licences.
    staged = {engine_name: [name for name, _, _ in files] for engine_name, files in pins.items()}
    staged["stockfish"] += [binary, "Copying.txt"]
    staged["caissa"] += [f"caissa{target.suffix}", "LICENSE", "Fathom-LICENSE"]
    staged["arasan"] += [f"arasan{target.suffix}", ARASAN_NETWORK, "LICENSE", "Fathom-LICENSE"]
    python_notices()
    rust = rust_notices()
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "scan64-server.spec")
    # Stage only complete build outputs; no system interpreter or PATH engine is needed at runtime.
    RESOURCES.mkdir(parents=True, exist_ok=True)
    for name in ("scan64-server", *staged, "notices"):
        shutil.rmtree(RESOURCES / name, ignore_errors=True)
    shutil.copytree(ROOT / "dist" / "scan64-server", RESOURCES / "scan64-server")
    for engine_name, names in staged.items():
        engine_resources = RESOURCES / engine_name
        engine_resources.mkdir()
        for name in names:
            shutil.copy2(STAGE / engine_name / name, engine_resources / name)
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

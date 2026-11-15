"""Install/extract each dry-run artifact, check notices and exercise its real binaries."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BUNDLES = ROOT / "apps/scan64-desktop/src-tauri/target/release/bundle"
EVIDENCE = ROOT / "build/desktop-evidence"


def command(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def smoke_helpers():
    spec = importlib.util.spec_from_file_location(
        "desktop_smoke", ROOT / "scripts/desktop-smoke.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_windowless(binary: Path) -> None:
    """A Windows GUI PE subsystem cannot allocate a console on normal launch."""
    with binary.open("rb") as source:
        assert source.read(2) == b"MZ", f"Invalid Windows executable: {binary.name}"
        source.seek(0x3C)
        pe_offset = int.from_bytes(source.read(4), "little")
        source.seek(pe_offset)
        assert source.read(4) == bytes([0x50, 0x45, 0, 0]), f"Invalid PE header: {binary.name}"
        source.seek(pe_offset + 24 + 68)
        assert int.from_bytes(source.read(2), "little") == 2, (
            f"Console subsystem in bundled executable: {binary.name}"
        )
    print(f"Windowless PE subsystem PASS: {binary.name}", flush=True)


def check_contents(resources: Path) -> list[str]:
    executable = ".exe" if os.name == "nt" else ""
    required = [
        f"scan64-server/scan64-server{executable}",
        f"stockfish/stockfish{executable}",
        "stockfish/stockfish-sf_18.tar.gz",
        "stockfish/Copying.txt",
        "stockfish/nn-c288c895ea92.nnue",
        "stockfish/nn-37f18f62d772.nnue",
        "notices/LICENSE",
        "notices/THIRD_PARTY_NOTICES.md",
        "notices/WEB_THIRD_PARTY_LICENSES.md",
        "notices/rust-licenses.html",
        "notices/rust-licenses.json",
        "notices/fonts/host-grotesk-OFL.txt",
        "notices/fonts/jetbrains-mono-OFL.txt",
    ]
    for relative in required:
        path = resources / relative
        assert path.is_file() and path.stat().st_size > 0, f"Missing bundled file: {relative}"
    if os.name == "nt":
        for relative in required[:2]:
            check_windowless(resources / relative)
    dockerfile = (ROOT / "Dockerfile").read_text()
    for path in (resources / "stockfish").iterdir():
        if path.name.endswith((".tar.gz", ".nnue")):
            match = re.search(
                r"echo '([0-9a-f]{64})  (?:/out/|src/)" + re.escape(path.name) + "'", dockerfile
            )
            assert match is not None, f"Missing authoritative digest: {path.name}"
            with path.open("rb") as source:
                assert hashlib.file_digest(source, "sha256").hexdigest() == match[1], path.name
    print("Bundle contents PASS: " + ", ".join(required), flush=True)
    return required


def request(port: int, path: str, token: str, body=None, authorization: str | None = None):
    headers = {"X-Scan64-Shell-Token": token, "Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = f"Bearer {authorization}"
    data = None if body is None else json.dumps(body).encode()
    with urlopen(
        Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers), timeout=60
    ) as r:
        assert r.status == 200, f"Unexpected HTTP status: {r.status}"
        return json.load(r)


def frozen_smoke(resources: Path, directory: Path) -> dict[str, object]:
    executable = ".exe" if os.name == "nt" else ""
    binary = resources / "scan64-server" / f"scan64-server{executable}"
    stockfish = resources / "stockfish" / f"stockfish{executable}"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_hex(32)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("SCAN64_")}
    for name in ("PYTHONPATH", "PYTHONHOME"):
        environment.pop(name, None)
    environment.update(
        BROWSER="/usr/bin/true",
        SCAN64_DATA_DIR=str(directory / "data"),
        SCAN64_PORT=str(port),
        SCAN64_SHELL_TOKEN=token,
        SCAN64_STOCKFISH_PATH=str(stockfish),
    )
    helpers = smoke_helpers()
    environment["PATH"] = helpers.runtime_path()
    children: list[int] = []
    with (directory / "frozen-server.log").open("wb") as log:
        process = subprocess.Popen(
            [str(binary)],
            cwd=stockfish.parent,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 60
            while True:
                assert process.poll() is None, "Frozen server exited before health check"
                try:
                    health = request(port, "/health", token)
                    assert health == {"status": "ok"}, health
                    break
                except (URLError, TimeoutError):
                    if time.monotonic() >= deadline:
                        raise AssertionError("Frozen server health deadline exceeded") from None
                    time.sleep(0.1)
            player = request(port, "/v1/players", token, {"id": "installer-smoke"})
            session = request(
                port,
                "/v1/play-sessions",
                token,
                {
                    "player_id": player["id"],
                    "player_color": "black",
                    "opponent_config": {"provider": "stockfish", "bot_level": "1"},
                },
                player["access_token"],
            )
            move = session["opponent_move"]
            assert isinstance(move, str) and re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", move), (
                move
            )
            children = helpers.descendants(process.pid)
            assert children, "No bundled Stockfish process was launched"
            assert process.stdin is not None
            process.stdin.close()
            process.wait(timeout=5)
            helpers.assert_stopped(process.pid, port)
            helpers.assert_children_stopped(children)
            receipt = {
                "health": "ok",
                "player_created": True,
                "bot_move": move,
                "stockfish": str(stockfish),
                "children_stopped": len(children),
            }
            print("Frozen server smoke PASS: " + json.dumps(receipt), flush=True)
            return receipt
        finally:
            if process.poll() is None:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if sys.exception() is not None:
                log.flush()
                print(
                    (directory / "frozen-server.log")
                    .read_text(errors="replace")
                    .replace(token, "[redacted]"),
                    flush=True,
                )


def verify(binary: Path, resources: Path, label: str, *, screenshot: bool = False) -> None:
    with tempfile.TemporaryDirectory(prefix="scan64-installed-") as temporary:
        directory = Path(temporary)
        contents = check_contents(resources)
        if os.name == "nt":
            check_windowless(binary)
        server = frozen_smoke(resources, directory)
        output = EVIDENCE / f"{label}-app-smoke.json"
        args = [
            sys.executable,
            str(ROOT / "scripts/desktop-smoke.py"),
            str(binary),
            str(output),
            "--packaged",
        ]
        if screenshot:
            args += ["--screenshot", str(EVIDENCE / "linux-board.png")]
        command(*args)
        receipt = {"bundle": label, "contents": contents, "frozen_server": server}
        (EVIDENCE / f"{label}-bundle-check.json").write_text(json.dumps(receipt, indent=2) + "\n")


def resource_root(directory: Path) -> Path:
    candidates = list(directory.rglob("stockfish/Copying.txt"))
    assert len(candidates) == 1, f"Expected one installed resource tree, got {candidates}"
    return candidates[0].parent.parent


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    suffixes = {"darwin": (".dmg",), "win32": ("-setup.exe",), "linux": (".AppImage", ".deb")}
    artifacts = [
        path
        for path in BUNDLES.rglob("*")
        if path.is_file() and any(path.name.endswith(suffix) for suffix in suffixes[sys.platform])
    ]
    assert len(artifacts) == len(suffixes[sys.platform]), artifacts
    listing = []
    for artifact in sorted(artifacts):
        with artifact.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        listing.append(f"{digest}  {artifact.name}")
        with tempfile.TemporaryDirectory(prefix="scan64-install-") as temporary:
            directory = Path(temporary)
            if artifact.suffix == ".dmg":
                mount = directory / "mount"
                mount.mkdir()
                command(
                    "hdiutil",
                    "attach",
                    "-readonly",
                    "-nobrowse",
                    "-mountpoint",
                    str(mount),
                    str(artifact),
                )
                try:
                    source_app = next(mount.glob("*.app"))
                    app = directory / "Applications" / source_app.name
                    app.parent.mkdir()
                    command("ditto", str(source_app), str(app))
                finally:
                    command("hdiutil", "detach", str(mount))
                binary = app / "Contents/MacOS/scan64-desktop"
                verify(binary, app / "Contents/Resources", "dmg")
            elif artifact.name.endswith("-setup.exe"):
                install = directory / "Scan64"
                command(str(artifact), "/S", f"/D={install}")
                binaries = [
                    path
                    for path in install.glob("*.exe")
                    if path.name.lower() in ("scan64.exe", "scan64-desktop.exe")
                ]
                assert len(binaries) == 1, binaries
                verify(binaries[0], resource_root(install), "nsis")
            elif artifact.suffix == ".AppImage":
                artifact.chmod(0o755)
                command(str(artifact), "--appimage-extract", cwd=directory)
                appdir = directory / "squashfs-root"
                verify(
                    appdir / "usr/bin/scan64-desktop",
                    resource_root(appdir),
                    "appimage",
                    screenshot=True,
                )
            else:
                command("sudo", "dpkg", "-i", str(artifact))
                listing_result = subprocess.run(
                    ["dpkg-deb", "-f", str(artifact), "Package"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                files = subprocess.run(
                    ["dpkg", "-L", listing_result.stdout.strip()],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
                copying = [Path(path) for path in files if path.endswith("/stockfish/Copying.txt")]
                assert len(copying) == 1, copying
                verify(Path("/usr/bin/scan64-desktop"), copying[0].parent.parent, "deb")
    text = "\n".join(listing) + "\n"
    (EVIDENCE / "SHA256SUMS.txt").write_text(text)
    print("Installer SHA-256 listing:\n" + text, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as output:
            output.write("### Installable dry-run artifacts\n\n```text\n" + text + "```\n")
    print("All installed bundles passed content, Stockfish and app lifetime checks", flush=True)


if __name__ == "__main__":
    main()

"""Build two test-only signed apps and exercise the real updater on the host OS.

Requires the frozen server/Stockfish resources from build-server.py. No production
key or hosted endpoint is used. All signing material lives in a private temporary
directory, is generated anew, and is never included in receipts or build output.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import http.server
import importlib.util
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "apps/scan64-desktop"
BUNDLES = DESKTOP / "src-tauri/target/release/bundle"
EVIDENCE = ROOT / "build/desktop-evidence/update"
FROM_VERSION = "0.6.1"
TO_VERSION = "0.6.2"
PLAYER_ID = "update-smoke-player"
DOWNLOAD_URL = "https://github.com/Mathews-Tom/Scan64/releases/latest"


def lifecycle_helpers():
    spec = importlib.util.spec_from_file_location(
        "desktop_smoke", ROOT / "scripts/desktop-smoke.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(args: list[str], *, environment: dict[str, str] | None = None) -> None:
    subprocess.run(args, cwd=ROOT, env=environment, check=True)


def tauri(*args: str, environment: dict[str, str], quiet: bool = False) -> None:
    # Never echo captured signer output. Ephemeral signing values are supplied
    # only through environment variables or private temporary files.
    result = subprocess.run(
        [shutil.which("pnpm") or "pnpm", "--dir", str(DESKTOP), "tauri", *args],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.PIPE if quiet else None,
    )
    if result.returncode:
        raise RuntimeError(f"Tauri {args[0]} failed (exit {result.returncode})")


def platform_key() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in {"arm64", "aarch64"}:
        return "darwin-aarch64"
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "windows-x86_64"
    if sys.platform == "linux" and machine == "x86_64":
        return "linux-x86_64"
    raise RuntimeError("Update acceptance supports macOS arm64, Windows x64 and Linux x64")


def exactly_one(paths: list[Path]) -> Path:
    assert len(paths) == 1, f"Expected one artifact, got {[p.name for p in paths]}"
    return paths[0]


def build(version: str, config: Path, environment: dict[str, str], saved: Path) -> Path:
    overlay = json.loads(config.read_text())
    overlay["version"] = version
    # Keep changes test-only: Tauri merges this generated overlay, never edits the
    # committed config/version, and the frozen server/engine are reused verbatim.
    version_config = saved.parent / f"test-{version}.json"
    version_config.write_text(json.dumps(overlay))
    if BUNDLES.exists():
        shutil.rmtree(BUNDLES)  # Generated build products owned by this runner.
    bundles = {"darwin": "app", "win32": "nsis", "linux": "appimage,deb"}[sys.platform]
    tauri(
        "build",
        "--ci",
        "--config",
        "src-tauri/tauri.bundle.conf.json",
        "--config",
        str(version_config),
        "--features",
        "updater-test",
        "--bundles",
        bundles,
        environment=environment,
    )
    saved.mkdir()
    if sys.platform == "darwin":
        source = exactly_one(list(BUNDLES.glob("macos/*.app.tar.gz")))
        app = exactly_one(list(BUNDLES.glob("macos/*.app")))
        run(["ditto", str(app), str(saved / app.name)])
    elif sys.platform == "win32":
        source = exactly_one(list(BUNDLES.glob("nsis/*-setup.exe")))
    else:
        source = exactly_one(list(BUNDLES.glob("appimage/*.AppImage")))
        if version == FROM_VERSION:
            deb = exactly_one(list(BUNDLES.glob("deb/*.deb")))
            shutil.copy2(deb, saved / deb.name)
    bundle = saved / source.name
    shutil.copy2(source, bundle)
    shutil.copy2(Path(str(source) + ".sig"), Path(str(bundle) + ".sig"))
    return bundle


def signature_tamper(signature: str) -> str:
    """Change the cryptographic signature, retaining valid minisign framing."""
    lines = base64.b64decode(signature).decode().splitlines()
    assert len(lines) >= 2
    data = bytearray(base64.b64decode(lines[1]))
    data[-1] ^= 1
    lines[1] = base64.b64encode(data).decode()
    return base64.b64encode(("\n".join(lines) + "\n").encode()).decode()


def manifest(bundle: Path, signature: str, port: int) -> dict[str, object]:
    return {
        "version": TO_VERSION,
        "notes": "Local signed updater acceptance fixture; not a release.",
        "pub_date": "2026-11-15T00:00:00Z",
        "platforms": {
            platform_key(): {
                "url": f"http://127.0.0.1:{port}/{quote(bundle.name)}",
                "signature": signature.strip(),
            }
        },
    }


class ReleaseHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        pass

    def copyfile(self, source, outputfile) -> None:
        # A paced local download makes intermediate percentage states observable
        # without faking progress or changing the production downloader.
        while chunk := source.read(256 * 1024):
            outputfile.write(chunk)
            outputfile.flush()
            time.sleep(0.002)


def install(old: Path, directory: Path) -> Path:
    if sys.platform == "darwin":
        app = exactly_one(list(old.parent.glob("*.app")))
        installed = directory / "Applications" / app.name
        installed.parent.mkdir()
        run(["ditto", str(app), str(installed)])
        return installed / "Contents/MacOS/scan64-desktop"
    if sys.platform == "win32":
        destination = directory / "Scan64"
        run([str(old), "/S", f"/D={destination}"])
        return exactly_one(
            [
                path
                for path in destination.glob("*.exe")
                if path.name.lower() in {"scan64.exe", "scan64-desktop.exe"}
            ]
        )
    installed = directory / old.name
    shutil.copy2(old, installed)
    installed.chmod(0o755)
    return installed


def events(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    # A writer may currently be appending the last record.
    result = []
    for index, line in enumerate(lines):
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise
    return result


def status_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return [record for record in records if "state" in record]


def assert_trace(records: list[dict[str, object]], mode: str) -> None:
    starts = [row for row in records if row.get("event") == "startup"]
    assert starts and starts[0]["version"] == FROM_VERSION, records
    states = status_records(records)
    if mode == "unavailable":
        unavailable = [row for row in states if row["state"] == "unavailable"]
        assert unavailable and "deb" in str(unavailable[-1]["reason"]).lower(), states
        assert unavailable[-1]["downloadUrl"] == DOWNLOAD_URL, states
        return
    assert any(row["state"] == "checking" for row in states), states
    assert any(row["state"] == "downloading" for row in states), states
    if mode == "reject":
        assert states[-1]["state"] == "failed", states
        assert "signature" in str(states[-1].get("reason", "")).lower(), states
        assert not any(row["state"] in {"ready", "installing"} for row in states), states
        assert [row["version"] for row in starts] == [FROM_VERSION], starts
        return
    percentages = [
        row["percent"]
        for row in states
        if row["state"] == "downloading" and isinstance(row.get("percent"), (int, float))
    ]
    assert any(0 < percent < 100 for percent in percentages), percentages
    names = [row["state"] for row in states]
    assert names.index("ready") < names.index("installing"), names
    assert [row["version"] for row in starts] == [FROM_VERSION, TO_VERSION], starts
    assert any(row.get("event") == "restart-click" for row in records), records


def player_present(database: Path) -> bool:
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
        return connection.execute(
            "SELECT profile.display_name FROM player "
            "JOIN playerprofile AS profile ON profile.player_id = player.id WHERE player.id = ?",
            (PLAYER_ID,),
        ).fetchone() == ("Update smoke",)


def run_app(binary: Path, directory: Path, mode: str) -> dict[str, object]:
    record_file = directory / "events.jsonl"
    data_dir = directory / "data"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SCAN64_", "TAURI_SIGNING_"))
    }
    environment.update(
        BROWSER="/usr/bin/true",
        SCAN64_DATA_DIR=str(data_dir),
        SCAN64_UPDATE_SMOKE_FILE=str(record_file),
        SCAN64_UPDATE_SMOKE_MODE=mode,
        SCAN64_UPDATE_SMOKE_FROM=FROM_VERSION,
        SCAN64_UPDATE_SMOKE_TO=TO_VERSION,
        APPIMAGE_EXTRACT_AND_RUN="1",
    )
    log_path = directory / "app.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                str(binary),
                "--scan64-update-smoke-file",
                str(record_file),
                "--scan64-update-smoke-mode",
                mode,
                "--scan64-update-smoke-from",
                FROM_VERSION,
                "--scan64-update-smoke-to",
                TO_VERSION,
                "--scan64-update-data-dir",
                str(data_dir),
            ],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    records: list[dict[str, object]] = []
    try:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            records = events(record_file)
            done = any(row.get("event") == "complete" for row in records)
            if done:
                break
            if any(row.get("event") == "error" for row in records):
                raise AssertionError(f"Updater smoke hook failed: {records}")
            # Windows installers and relaunch deliberately exit the first app.
            if mode != "install" and process.poll() is not None:
                break
            time.sleep(0.1)
        else:
            raise AssertionError(f"Update {mode} timed out: {records}")
        assert any(row.get("event") == "complete" for row in records), records
        assert_trace(records, mode)
        if process.poll() is None:
            process.wait(timeout=15)
        helpers = lifecycle_helpers()
        for record in records:
            if record.get("event") != "startup":
                continue
            helpers.assert_stopped(int(record["server_pid"]), int(record["port"]))
            pid = int(record["shell_pid"])
            deadline = time.monotonic() + 15
            while helpers.alive(pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not helpers.alive(pid), f"Updater shell {pid} did not exit"
        database = data_dir / "scan64.db"
        assert player_present(database), "Player was lost during update"
        expected_version = TO_VERSION if mode == "install" else FROM_VERSION
        assert (data_dir / "desktop-app-version").read_text().strip() == expected_version
        backups = sorted((data_dir / "backups").glob("*.db"))
        if mode == "install":
            assert backups and all(player_present(path) for path in backups), backups
        else:
            assert not backups, f"Rejected/unavailable update modified the database: {backups}"
        receipt = {
            "mode": mode,
            "versions": [row["version"] for row in records if row.get("event") == "startup"],
            "states": status_records(records),
            "player_intact": True,
            "backups": [str(path) for path in backups],
            "events": records,
        }
        return receipt
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if sys.exception() is not None:
            helpers = lifecycle_helpers()
            for record in events(record_file):
                if record.get("event") != "startup":
                    continue
                pid = int(record["shell_pid"])
                if pid != process.pid and helpers.alive(pid):
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(pid), "/T", "/F"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        try:
                            os.kill(pid, 9)
                        except ProcessLookupError:
                            pass
        # No credentials are intentionally emitted by either application process.
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        shutil.copy2(log_path, EVIDENCE / f"{directory.name}-app.log")
        if record_file.exists():
            shutil.copy2(record_file, EVIDENCE / f"{directory.name}-events.jsonl")


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_summary(path: Path) -> None:
    with path.open("a", encoding="utf-8") as report:
        report.write(f"### Signed update end-to-end ({platform_key()})\n\n")
        report.write(
            f"Observed {FROM_VERSION} → {TO_VERSION}; progress, explicit restart, "
            "database backup and player intact.\n\n"
        )
        report.write(
            "Modified bundle, modified signature and mismatched signed version: rejected; "
            "installed app unchanged.\n\n"
        )
        if sys.platform == "linux":
            report.write("Installed .deb: unavailable, release download link present.\n\n")


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scan64-update-test-") as temporary:
        workspace = Path(temporary).resolve()
        os.chmod(workspace, 0o700)
        private_key = workspace / "test-only.key"
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("TAURI_SIGNING_")
        }
        # The CLI's --ci generator uses an empty password without putting any
        # credential in argv. The private test key is protected by this 0700
        # temporary directory and removed deterministically when the run ends.
        environment["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"] = ""
        tauri(
            "signer",
            "generate",
            "--ci",
            "--write-keys",
            str(private_key),
            environment=environment,
            quiet=True,
        )
        environment["SCAN64_UPDATER_TEST"] = "1"
        environment["TAURI_SIGNING_PRIVATE_KEY"] = str(private_key)
        environment["SCAN64_UPDATER_DRY_RUN"] = "1"
        public_key = private_key.with_suffix(".key.pub").read_text().strip()
        release = workspace / "release"
        release.mkdir()
        handler = functools.partial(ReleaseHandler, directory=str(release))
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
            port = server.server_address[1]
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                config = workspace / "updater-test.json"
                overlay = json.loads(
                    (DESKTOP / "src-tauri/tauri.updater-test.conf.json").read_text()
                )
                overlay["identifier"] = "org.scan64.desktop.updater-test"
                overlay.setdefault("bundle", {})["createUpdaterArtifacts"] = True
                overlay.setdefault("plugins", {})["updater"] = {
                    "pubkey": public_key,
                    "endpoints": [f"http://127.0.0.1:{port}/latest.json"],
                    "dangerousInsecureTransportProtocol": True,
                    "requireSignedVersion": True,
                    "windows": {"installMode": "passive"},
                }
                config.write_text(json.dumps(overlay))
                feature_environment = {**environment, "TAURI_CONFIG": json.dumps(overlay)}
                manifest_path = str(DESKTOP / "src-tauri/Cargo.toml")
                run(
                    [
                        "cargo",
                        "test",
                        "--locked",
                        "--release",
                        "--manifest-path",
                        manifest_path,
                        "--features",
                        "updater-test",
                    ],
                    environment=feature_environment,
                )
                run(
                    [
                        "cargo",
                        "clippy",
                        "--locked",
                        "--release",
                        "--manifest-path",
                        manifest_path,
                        "--features",
                        "updater-test",
                        "--all-targets",
                        "--",
                        "-D",
                        "warnings",
                    ],
                    environment=feature_environment,
                )
                old = build(FROM_VERSION, config, environment, workspace / "old")
                new = build(TO_VERSION, config, environment, workspace / "new")
                signature = Path(str(new) + ".sig").read_text().strip()
                served = release / new.name
                shutil.copy2(new, served)
                pristine_digest = digest(old)
                results = []
                for case in (
                    "tampered-bundle",
                    "tampered-signature",
                    "mismatched-version",
                    "install",
                ):
                    signature_value = signature
                    shutil.copy2(new, served)
                    if case == "tampered-bundle":
                        with served.open("ab") as output:
                            output.write(b"tampered updater bundle")
                    elif case == "tampered-signature":
                        signature_value = signature_tamper(signature)
                    elif case == "mismatched-version":
                        # Cryptographically valid N bytes/signature, but the
                        # manifest announces N+1: only version binding rejects it.
                        shutil.copy2(old, served)
                        signature_value = Path(str(old) + ".sig").read_text().strip()
                    (release / "latest.json").write_text(
                        json.dumps(manifest(served, signature_value, port))
                    )
                    directory = workspace / case
                    directory.mkdir()
                    binary = install(old, directory)
                    before = digest(binary)
                    result = run_app(
                        binary, directory, "install" if case == "install" else "reject"
                    )
                    if case != "install":
                        assert digest(binary) == before, "Rejected update changed the installed app"
                        result["app_unchanged"] = True
                    if case == "mismatched-version":
                        assert "version" in str(result["states"][-1]["reason"]).lower()
                        result["signed_version"] = FROM_VERSION
                        result["manifest_version"] = TO_VERSION
                    result["case"] = case
                    results.append(result)
                    assert digest(old) == pristine_digest, "The saved N install was modified"
                if sys.platform == "linux":
                    deb = exactly_one(list(old.parent.glob("*.deb")))
                    run(["sudo", "dpkg", "-i", str(deb)])
                    directory = workspace / "deb-unavailable"
                    directory.mkdir()
                    result = run_app(Path("/usr/bin/scan64-desktop"), directory, "unavailable")
                    result["case"] = "deb-unavailable"
                    results.append(result)
                receipt = {
                    "platform": platform_key(),
                    "from": FROM_VERSION,
                    "to": TO_VERSION,
                    "results": results,
                }
                output = json.dumps(receipt, indent=2) + "\n"
                (EVIDENCE / "update-e2e.json").write_text(output)
                print(
                    json.dumps(
                        {
                            "platform": platform_key(),
                            "results": [
                                {
                                    key: value
                                    for key, value in result.items()
                                    if key not in {"states", "events"}
                                }
                                for result in results
                            ],
                            "progress_observed": True,
                        },
                        indent=2,
                    ),
                    flush=True,
                )
                summary = os.environ.get("GITHUB_STEP_SUMMARY")
                if summary:
                    write_summary(Path(summary))
            finally:
                server.shutdown()


if __name__ == "__main__":
    main()

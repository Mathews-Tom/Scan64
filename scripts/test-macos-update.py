#!/usr/bin/env python3
"""Exercise two real frozen app builds against an isolated signed local release.

Build both versions separately with scripts/build-macos-app.sh, preserving the
first bundle outside dist before building the newer version. This script never
builds, patches, signs, or launches either supplied bundle in place.

Run from the source checkout's uv environment::

    BROWSER=/usr/bin/true uv run --no-sync python scripts/test-macos-update.py \
        --old-app /absolute/old/Scan64.app --new-app /absolute/new/Scan64.app

All app execution, HTTP fixtures, HOME/data, helper output, and replacements live
under a new /private/tmp directory. No browser, Finder, real Applications folder,
or existing user database is used. Failed runs retain that directory for diagnosis;
--keep retains successful runs too. The supplied apps must actually contain their
respective package versions; editing Info.plist alone does not produce a valid input.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from email.parser import BytesParser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class HarnessFailure(RuntimeError):
    """An observed result did not satisfy the frozen-app update contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise HarnessFailure(message)


def bundle_identity(app: Path) -> tuple[str, Path]:
    """Read both identities without executing or modifying an input app."""
    require(
        app.is_dir() and (app / "Contents/Info.plist").is_file(),
        f"Not an app bundle: {app}",
    )
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    version = str(info["CFBundleShortVersionString"])
    executable_name = info["CFBundleExecutable"]
    require(
        isinstance(executable_name, str) and Path(executable_name).name == executable_name,
        f"Unsafe bundle executable name in {app}",
    )
    executable = app / "Contents/MacOS" / executable_name
    require(
        executable.is_file() and os.access(executable, os.X_OK), f"Missing binary: {executable}"
    )
    metadata_paths = list((app / "Contents").glob("**/scan64-*.dist-info/METADATA"))
    require(bool(metadata_paths), f"No frozen scan64 package metadata in {app}")
    for metadata_path in metadata_paths:
        metadata = BytesParser().parsebytes(metadata_path.read_bytes())
        require(metadata["Name"] == "scan64", f"Unexpected package metadata: {metadata_path}")
        require(
            metadata["Version"] == version,
            f"Info.plist version {version} differs from frozen package version "
            f"{metadata['Version']} in {app}; build a real second version instead of patching it",
        )
    return version, executable


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_digest(root: Path) -> str:
    """Cover names, permissions, symlink targets and bytes of the installed app."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        digest.update(json.dumps([relative, path.lstat().st_mode]).encode())
        if path.is_symlink():
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            digest.update(bytes.fromhex(file_digest(path)))
    return digest.hexdigest()


def environment(home: Path, scratch: Path) -> dict[str, str]:
    # A developer shell may point at a real database, engine, cache or release.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SCAN64_", "PYTHON", "XDG_", "DYLD_", "_PYI_"))
    }
    env.update(
        HOME=str(home),
        CFFIXED_USER_HOME=str(home),
        TMPDIR=str(scratch),
        BROWSER="/usr/bin/true",
        PYINSTALLER_RESET_ENVIRONMENT="1",
        XDG_CACHE_HOME=str(home / ".cache"),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_DATA_HOME=str(home / ".local/share"),
        HTTP_PROXY="",
        HTTPS_PROXY="",
        ALL_PROXY="",
        http_proxy="",
        https_proxy="",
        all_proxy="",
        NO_PROXY="127.0.0.1,localhost,::1",
        no_proxy="127.0.0.1,localhost,::1",
    )
    return env


def run_command(
    command: list[str], *, env: dict[str, str], cwd: Path, timeout: float, log: Path
) -> None:
    with log.open("ab") as output:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HarnessFailure(f"Command timed out after {timeout}s; see {log}") from exc
    require(result.returncode == 0, f"Command exited {result.returncode}; see {log}")


def seed_database(database: Path, *, env: dict[str, str], cwd: Path, timeout: float) -> None:
    """Use real migrations/models to save a legally played, completed chess game."""
    source = Path(__file__).resolve().parents[1] / "src"
    seed = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import chess
import chess.pgn
from sqlmodel import Session
from scan64.persistence.database import create_db_and_tables, engine
from scan64.api.models import Player
from scan64.chess.games.models import Game, PlaySession

create_db_and_tables()
board = chess.Board()
pgn = chess.pgn.Game()
pgn.headers.update(White="Update harness", Black="Local opponent", Result="0-1")
node = pgn
moves = ["f2f3", "e7e5", "g2g4", "d8h4"]
for uci in moves:
    move = chess.Move.from_uci(uci)
    assert move in board.legal_moves
    board.push(move)
    node = node.add_variation(move)
assert board.is_checkmate()
with Session(engine) as session:
    session.add(Player(id="update-e2e", preferences={"fixture": "preserve-me"}))
    game = Game(
        pgn=str(pgn), headers=dict(pgn.headers), moves=moves,
        white="Update harness", black="Local opponent", result="0-1",
        owner_player_id="update-e2e", owner_color="white",
    )
    session.add(game)
    session.flush()
    session.add(PlaySession(
        player_id="update-e2e", game_id=game.id,
        opponent_config={"bot_level": "1"}, status="completed",
    ))
    session.commit()
engine.dispose()
"""
    seed_env = {
        **env,
        "SCAN64_DATABASE_URL": f"sqlite:///{database}",
        "SCAN64_DATA_DIR": str(database.parent),
    }
    run_command(
        [sys.executable, "-c", seed, str(source)],
        env=seed_env,
        cwd=cwd,
        timeout=timeout,
        log=cwd / "seed.log",
    )
    database_dump(database)


def database_dump(path: Path) -> str:
    require(path.is_file(), f"Database is missing: {path}")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        require(
            connection.execute("PRAGMA integrity_check").fetchone() == ("ok",),
            f"Database integrity check failed: {path}",
        )
        require(
            connection.execute("SELECT count(*) FROM game WHERE result = '0-1'").fetchone() == (1,),
            f"Seeded played game was lost or duplicated: {path}",
        )
        require(
            connection.execute(
                "SELECT count(*) FROM playsession WHERE status = 'completed'"
            ).fetchone()
            == (1,),
            f"Seeded completed play session was lost: {path}",
        )
        return "\n".join(connection.iterdump())


class LocalReleaseHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # The enclosing harness retains subprocess diagnostics, not request noise.
        pass


@contextmanager
def serve_release(directory: Path):
    handler = partial(LocalReleaseHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def invoke_app(
    app: Path,
    *,
    action: str,
    release_url: str,
    public_key: str,
    home: Path,
    scratch: Path,
    marker: Path,
    timeout: float,
) -> dict[str, Any]:
    _, executable = bundle_identity(app)
    result = scratch / f"{action}-result.json"
    result.unlink(missing_ok=True)
    probe_config = scratch / "probe-config.json"
    relaunch_command = [str(executable), "--update-test-config", str(probe_config)]
    common = {
        "release_url": release_url,
        "public_key": public_key,
        "relaunch_command": relaunch_command,
    }
    probe_config.write_text(
        json.dumps(
            {
                **common,
                "action": "probe",
                "result_path": str(marker),
            }
        ),
        encoding="utf-8",
    )
    config = scratch / f"{action}-config.json"
    config.write_text(
        json.dumps(
            {
                **common,
                "action": action,
                "result_path": str(result),
            }
        ),
        encoding="utf-8",
    )
    run_command(
        [str(executable), "--update-test-config", str(config)],
        env=environment(home, scratch),
        cwd=scratch,
        timeout=timeout,
        log=scratch / f"{action}.log",
    )
    require(result.is_file(), f"Headless app did not write its result: {result}")
    snapshot = json.loads(result.read_text(encoding="utf-8"))
    require(isinstance(snapshot, dict), f"Invalid updater snapshot: {snapshot!r}")
    return snapshot


def publish_release(
    directory: Path,
    *,
    base_url: str,
    archive: Path,
    version: str,
    private_key: Any,
    scenario: str,
) -> str:
    """Sign independently of the updater, including its exact canonical bytes."""
    directory.mkdir()
    asset = f"Scan64-{version}-macos-arm64.zip"
    manifest = json.dumps(
        {
            "asset": asset,
            "format": 1,
            "platform": "macos-arm64",
            "sha256": file_digest(archive),
            "size": archive.stat().st_size,
            "version": version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = private_key.sign(manifest)
    if scenario == "bad-signature":
        signature = signature[:-1] + bytes([signature[-1] ^ 1])
    (directory / "update.json").write_bytes(manifest)
    (directory / "update.json.sig").write_bytes(base64.b64encode(signature) + b"\n")
    destination = directory / asset
    if scenario == "tampered-zip":
        shutil.copyfile(archive, destination)
        # Same length and still signed for the unmodified ZIP: exercise SHA-256,
        # not a truncated-download or malformed-release-metadata rejection.
        with destination.open("r+b") as stream:
            stream.seek(-1, os.SEEK_END)
            last = stream.read(1)
            stream.seek(-1, os.SEEK_END)
            stream.write(bytes([last[0] ^ 1]))
    else:
        os.link(archive, destination)
    release = {
        "tag_name": f"v{version}",
        "assets": [
            {"name": name, "browser_download_url": f"{base_url}/{directory.name}/{name}"}
            for name in ("update.json", "update.json.sig", asset)
        ],
    }
    (directory / "latest.json").write_text(json.dumps(release), encoding="utf-8")
    return f"{base_url}/{directory.name}/latest.json"


def wait_for_relaunch(marker: Path, *, data: Path, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.is_file():
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                # The headless probe may still be writing its result.
                time.sleep(0.1)
                continue
            require(isinstance(value, dict), f"Invalid relaunch probe result: {value!r}")
            # The helper logs this after the relaunched binary has exited.
            log = data / "update.log"
            if log.exists() and "Installed Scan64 " in log.read_text(encoding="utf-8"):
                return value
        time.sleep(0.1)
    raise HarnessFailure(
        f"Installer/relaunched frozen-app probe did not finish; see {data}/update.log"
    )


def exercise_scenario(
    root: Path,
    *,
    scenario: str,
    old_app: Path,
    old_version: str,
    new_version: str,
    new_digest: str,
    release_url: str,
    public_key: str,
    timeout: float,
) -> None:
    case = root if scenario == "valid" else root / scenario
    scratch = case / "scratch"
    home = case / "home"
    data = home / "Library/Application Support/Scan64"
    app = case / "Applications/Scan64.app"
    for directory in (scratch, data, app.parent):
        directory.mkdir(parents=True)
    env = environment(home, scratch)
    run_command(
        ["/usr/bin/ditto", str(old_app), str(app)],
        env=env,
        cwd=scratch,
        timeout=timeout,
        log=scratch / "copy.log",
    )
    database = data / "scan64.db"
    seed_database(database, env=env, cwd=scratch, timeout=timeout)
    initial_probe = invoke_app(
        app,
        action="probe",
        release_url=release_url,
        public_key=public_key,
        home=home,
        scratch=scratch,
        marker=scratch / "initial-probe.json",
        timeout=timeout,
    )
    require(
        initial_probe.get("startup") == "ok"
        and initial_probe.get("played_games") == 1
        and initial_probe.get("completed_sessions") == 1,
        f"Old frozen app cannot initialize/read the seeded played game: {initial_probe}",
    )
    before_database = database_dump(database)
    before_database_digest = file_digest(database)
    before_app = tree_digest(app)
    previous = app.with_name(app.name + ".previous")
    marker = scratch / "relaunched-new-app.json"
    snapshot = invoke_app(
        app,
        action="check",
        release_url=release_url,
        public_key=public_key,
        home=home,
        scratch=scratch,
        marker=marker,
        timeout=timeout,
    )
    require(
        snapshot.get("current_version") == old_version,
        f"{scenario}: check did not execute the old frozen package: {snapshot}",
    )
    require(
        snapshot.get("bundle_version") == old_version,
        f"{scenario}: check did not execute the old bundle: {snapshot}",
    )
    require(tree_digest(app) == before_app, f"{scenario}: check changed the installed app")
    require(
        file_digest(database) == before_database_digest,
        f"{scenario}: check changed the database bytes",
    )
    require(
        database_dump(database) == before_database,
        f"{scenario}: check changed the saved game/database",
    )
    require(not previous.exists(), f"{scenario}: check moved the installed app")
    require(not (data / "backups").exists(), f"{scenario}: check created an install backup")
    require(not marker.exists(), f"{scenario}: check unexpectedly relaunched the app")
    if scenario != "valid":
        require(
            snapshot.get("status") == "failed",
            f"{scenario}: unsafe release was not rejected: {snapshot}",
        )
        reason = "signature" if scenario == "bad-signature" else "digest"
        require(
            reason in str(snapshot.get("message", "")).lower(),
            f"{scenario}: rejection did not exercise {reason} verification: {snapshot}",
        )
        require(
            not list((data / "updates").glob("*/staging/Scan64.app")),
            f"{scenario}: rejected archive was staged",
        )
        print(f"PASS {scenario}: rejected; app and played-game database untouched", flush=True)
        return

    require(
        snapshot.get("status") == "ready" and snapshot.get("version") == new_version,
        f"Valid signed release did not become ready: {snapshot}",
    )
    require(
        snapshot.get("automatic_updates") is True, "Automatic updates are not enabled by default"
    )
    staged = data / "updates" / new_version / "staging/Scan64.app"
    require(bundle_identity(staged)[0] == new_version, "Wrong version staged")
    require(tree_digest(staged) == new_digest, "Staged app differs from the real newer build")
    restart = invoke_app(
        app,
        action="restart",
        release_url=release_url,
        public_key=public_key,
        home=home,
        scratch=scratch,
        marker=marker,
        timeout=timeout,
    )
    require(
        restart.get("current_version") == old_version,
        f"Restart was not initiated by the old frozen package: {restart}",
    )
    probe = wait_for_relaunch(marker, data=data, timeout=timeout)
    require(
        probe.get("startup") == "ok"
        and probe.get("played_games") == 1
        and probe.get("completed_sessions") == 1,
        f"New frozen app cannot initialize/read the preserved played game: {probe}",
    )
    require(
        probe.get("current_version") == new_version,
        f"Helper did not launch the new frozen package: {probe}",
    )
    require(
        probe.get("bundle_version") == new_version, f"Helper relaunched the wrong bundle: {probe}"
    )
    require(bundle_identity(app)[0] == new_version, "Installed app version did not advance")
    require(tree_digest(app) == new_digest, "Installed app differs from the newer build")
    require(previous.is_dir(), "Installer did not retain Scan64.app.previous")
    require(bundle_identity(previous)[0] == old_version, ".previous has the wrong version")
    require(tree_digest(previous) == before_app, ".previous is not the unchanged original app")
    require(database_dump(database) == before_database, "Install/relaunch changed saved user data")
    backups = list((data / "backups").glob(f"scan64-{old_version}-*.db"))
    require(len(backups) == 1, f"Expected exactly one pre-update database backup, found {backups}")
    require(
        database_dump(backups[0]) == before_database,
        "Database backup does not preserve the pre-update played game",
    )
    print(
        f"PASS valid: {old_version} -> {new_version}; relaunched new frozen binary, "
        "played game preserved, backup and .previous verified",
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--old-app", type=Path, required=True, help="Older actual frozen .app build"
    )
    parser.add_argument(
        "--new-app", type=Path, required=True, help="Newer actual frozen .app build"
    )
    parser.add_argument(
        "--timeout", type=float, default=300, help="Per-operation timeout in seconds"
    )
    parser.add_argument(
        "--keep", action="store_true", help="Keep the temporary tree after success too"
    )
    parser.add_argument("--root", type=Path, help="New, nonexistent test directory below /tmp")
    args = parser.parse_args(argv)
    require(sys.platform == "darwin", "This frozen-app e2e harness requires macOS")
    require(args.timeout > 0, "--timeout must be positive")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from packaging.version import Version

    old_app = args.old_app.expanduser().resolve(strict=True)
    new_app = args.new_app.expanduser().resolve(strict=True)
    require(old_app != new_app, "--old-app and --new-app must be separately built bundles")
    old_version, _ = bundle_identity(old_app)
    new_version, _ = bundle_identity(new_app)
    require(
        Version(new_version) > Version(old_version),
        f"New build {new_version} must be newer than old build {old_version}",
    )
    if args.root is None:
        root = Path(tempfile.mkdtemp(prefix="scan64-update-e2e-", dir="/private/tmp")).resolve()
    else:
        root = args.root.resolve()
        require(root.is_relative_to(Path("/tmp").resolve()), "--root must be below /tmp")
        root.mkdir(mode=0o700)
    print(f"Isolated updater e2e: {root}", flush=True)
    succeeded = False
    try:
        payload = root / "payload/Scan64.app"
        payload.parent.mkdir()
        server_root = root / "releases"
        server_root.mkdir()
        scratch = root / "preparation"
        scratch.mkdir()
        home = root / "preparation-home"
        home.mkdir()
        env = environment(home, scratch)
        run_command(
            ["/usr/bin/ditto", str(new_app), str(payload)],
            env=env,
            cwd=scratch,
            timeout=args.timeout,
            log=scratch / "copy-new.log",
        )
        archive = root / f"Scan64-{new_version}-macos-arm64.zip"
        run_command(
            [
                "/usr/bin/ditto",
                "-c",
                "-k",
                "--sequesterRsrc",
                "--keepParent",
                str(payload),
                str(archive),
            ],
            env=env,
            cwd=scratch,
            timeout=args.timeout,
            log=scratch / "archive.log",
        )
        new_digest = tree_digest(payload)
        private_key = Ed25519PrivateKey.generate()
        public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")
        with serve_release(server_root) as base_url:
            for scenario in ("bad-signature", "tampered-zip", "valid"):
                release_url = publish_release(
                    server_root / scenario,
                    base_url=base_url,
                    archive=archive,
                    version=new_version,
                    private_key=private_key,
                    scenario=scenario,
                )
                exercise_scenario(
                    root,
                    scenario=scenario,
                    old_app=old_app,
                    old_version=old_version,
                    new_version=new_version,
                    new_digest=new_digest,
                    release_url=release_url,
                    public_key=public_key,
                    timeout=args.timeout,
                )
        succeeded = True
        print("PASS all built-app update scenarios", flush=True)
        return 0
    finally:
        if succeeded and not args.keep:
            shutil.rmtree(root)
        else:
            print(f"Retained isolated apps/data/logs: {root}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HarnessFailure, OSError, ValueError, KeyError, ImportError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error

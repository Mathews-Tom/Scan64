"""Provenance of the recorded theme measurements (guardrail 6)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from scan64.providers.stockfish.identity import NETWORK_OPTIONS

REPO_ROOT = Path(__file__).parents[3]


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def code_provenance(script: Path, games: dict[str, Path], config: dict[str, Any]) -> dict[str, Any]:
    """Commit, uncommitted changes (code, games and packages) and input digests."""
    config_json = json.dumps(config, sort_keys=True).encode()
    return {
        "commit": _git("rev-parse", "HEAD"),
        "uncommitted": _git(
            "status", "--porcelain", "--", "src", "tests", "benchmarks", "pyproject.toml", "uv.lock"
        ).splitlines(),
        "script": script.resolve().relative_to(REPO_ROOT).as_posix(),
        "script_sha256": file_sha256(script),
        "games_sha256": {name: file_sha256(path) for name, path in games.items()},
        "config_sha256": hashlib.sha256(config_json).hexdigest(),
    }


def engine_manifest(identity: dict[str, Any]) -> dict[str, Any]:
    """Binary and network digests from an adapter's analysis identity."""
    files = json.loads(identity["network"]) if identity.get("network") else {}
    options = identity["options"]
    return {
        "id_name": identity["engine_name"],
        "binary_sha256": str(identity["engine_version"]).removeprefix("sha256:"),
        "network": {
            name: {"name": options[name], "sha256": files.get(name, "embedded")}
            for name in NETWORK_OPTIONS
            if options.get(name)
        },
        "options": options,
    }

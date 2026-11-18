"""Executable and effective UCI inputs for exact, persistent batch reuse."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import chess.engine

from scan64.chess.analysis.models import EngineAnalysisConfig

type ExecutableSignature = tuple[Path, int, int, int, int]


def executable_signature(binary_path: str) -> ExecutableSignature:
    executable = Path(shutil.which(binary_path) or binary_path).resolve(strict=True)
    stat = executable.stat()
    return executable, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def engine_identity(engine: chess.engine.UciProtocol, binary_path: str) -> dict[str, Any]:
    """Read executable/network content once, when this UCI process starts."""
    executable = executable_signature(binary_path)[0]
    with executable.open("rb") as stream:
        version = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    networks = {}
    for name in ("EvalFile", "EvalFileSmall"):
        value = engine.config.get(name)
        if isinstance(value, str):
            # Stockfish can use external networks relative to its working directory
            # or executable; built-in networks are already covered by the binary.
            for candidate in (Path(value), executable.parent / value):
                if candidate.is_file():
                    with candidate.open("rb") as stream:
                        networks[name] = hashlib.file_digest(stream, "sha256").hexdigest()
                    break
    return {
        "engine_name": engine.id.get("name", "Stockfish"),
        "engine_version": version,
        "network": json.dumps(networks, sort_keys=True) if networks else None,
    }


def analysis_identity(
    engine: chess.engine.UciProtocol, identity: dict[str, Any], threads: int
) -> dict[str, Any]:
    """Include defaults and overrides, not just the two user-facing options.

    python-chess manages MultiPV (keyed separately), pondering, analysis mode
    and standard-chess mode. Pool play may have changed the strength controls;
    analysis restores these exact values before searching.
    """
    options = dict(engine.config)
    options.pop("MultiPV", None)
    overrides: dict[str, Any] = {
        "Threads": threads,
        "Skill Level": 20,
        "UCI_LimitStrength": False,
        "UCI_Elo": 1320,
        "Ponder": False,
        "UCI_AnalyseMode": True,
        "UCI_Chess960": False,
    }
    options.update({name: value for name, value in overrides.items() if name in engine.options})
    return EngineAnalysisConfig(**identity, options=options).model_dump()

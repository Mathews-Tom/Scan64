"""Executable and effective UCI inputs for exact, persistent batch reuse."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from scan64.chess.analysis.models import EngineAnalysisConfig
from scan64.providers.engine.uci import Engine, is_xboard

type ExecutableSignature = tuple[Path, int, int, int, int]

# UCI string options that name a network/weights file whose content changes
# results: Stockfish/Berserk/Ethereal ``EvalFile``, Stockfish ``EvalFileSmall``,
# Lc0 ``WeightsFile``. The executable digest covers embedded networks.
NETWORK_OPTIONS = ("EvalFile", "EvalFileSmall", "WeightsFile")


def executable_signature(binary_path: str) -> ExecutableSignature:
    executable = Path(shutil.which(binary_path) or binary_path).resolve(strict=True)
    stat = executable.stat()
    return executable, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def engine_identity(engine: Engine, binary_path: str) -> dict[str, Any]:
    """Read executable/network content once, when this UCI process starts."""
    executable = executable_signature(binary_path)[0]
    with executable.open("rb") as stream:
        version = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
    networks = {}
    for name in NETWORK_OPTIONS:
        value = engine.config.get(name)
        if isinstance(value, str):
            # Engines can load external networks relative to their working
            # directory or executable; built-in networks are covered by the binary.
            for candidate in (Path(value), executable.parent / value):
                if candidate.is_file():
                    with candidate.open("rb") as stream:
                        networks[name] = hashlib.file_digest(stream, "sha256").hexdigest()
                    break
    return {
        "engine_name": engine.id.get("name", executable.stem),
        "engine_version": version,
        "network": json.dumps(networks, sort_keys=True) if networks else None,
    }


def analysis_identity(
    engine: Engine,
    identity: dict[str, Any],
    threads: int,
    node_scale: float,
    score_scale: float,
) -> dict[str, Any]:
    """Include defaults and overrides, not just the two user-facing options.

    python-chess manages MultiPV (keyed separately), pondering, analysis mode
    and standard-chess mode. Pool play may have changed the strength controls;
    analysis restores these exact values before searching. A profile's
    ``node_scale`` changes every search, so it is recorded when it is not 1;
    ``nodes`` stays the Scan64 budget, not the scaled count sent to the engine.
    ``score_scale`` is recorded on the same terms: its rows' lines carry it, so
    rows at different scales never reuse each other.
    An XBoard process is recorded as ``protocol``; UCI, the default, is not.
    """
    options = dict(engine.config)
    options.pop("MultiPV", None)
    overrides: dict[str, Any] = {
        "Threads": threads,
        "Skill Level": 20,
        "UCI_LimitStrength": False,
        # Not sent (ignored while UCI_LimitStrength is off); recorded so rows
        # stored before it was dropped keep their reuse key.
        "UCI_Elo": 1320,
        "Ponder": False,
        "UCI_AnalyseMode": True,
        "UCI_Chess960": False,
    }
    options.update({name: value for name, value in overrides.items() if name in engine.options})
    config = EngineAnalysisConfig(
        **identity,
        options=options,
        node_scale=None if node_scale == 1 else node_scale,
        score_scale=None if score_scale == 1 else score_scale,
        protocol="xboard" if is_xboard(engine) else None,
    )
    # Keys at their defaults stay absent so pre-existing rows keep their reuse key.
    return config.model_dump(
        exclude={
            key for key in ("node_scale", "score_scale", "protocol") if getattr(config, key) is None
        }
    )

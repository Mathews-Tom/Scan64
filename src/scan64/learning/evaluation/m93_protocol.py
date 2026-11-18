"""Preregistered M93 selection and revisions; immutable after first recording."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CODES = frozenset({"tactics.fork.knight", "tactics.pin", "tactics.overloaded_defender"})
PROTOCOL: dict[str, Any] = {
    "revision": "m93-motifs-v1",
    "selection": "Next 80 qualifying games after original40, M78 and M79; ordinal 201..280.",
    "qualifiers": "error-free PGN; both Elo 600..1600 inclusive; at least 20 plies",
    "source_url": "https://database.lichess.org/standard/lichess_db_standard_rated_2013-01.pgn.zst",
    "source_sha256": "aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635",
    "license": "CC0",
    "game_count": 80,
    "engine": "Stockfish 19",
    "threads": 1,
    "hash_mb": 16,
    "fast_nodes": 10_000,
    "focused_nodes": 1_000_000,
    "focused_multipv": 4,
    "deep_before_nodes": 1_000_000,
    "deep_before_multipv": 4,
    "reset": "ucinewgame before each position",
    "minimum_precision": 0.80,
    "minimum_support_tp_fp": 10,
    "codes": sorted(CODES),
    "oracle": "reference_labels (unchanged M48 deep oracle; knight-only fork)",
    "revisions": {
        "src/scan64/learning/diagnosis/detectors/tactics.py": (
            "8949ea10e6a6368f5f340eb43bbae607971d2cdbd8474479a115492201fc8f64"
        ),
        "src/scan64/learning/evidence/composer.py": (
            "92112a2bf0c1d7e3d2c5ec4ca03efa61576aa2d3d4d4ce2d9491d5faa569a628"
        ),
        "src/scan64/learning/evaluation/deep_reference.py": (
            "a48a46884eaaf3dc536a6da250c9da2f976ea9f9877ef901baf54a3c5fa7c447"
        ),
        "src/scan64/learning/diagnosis/arbitration.py": (
            "8ae40ef7cfe2e2c1071266e838ec5b6a1127690b3a0acb5dec779a5e5770f135"
        ),
    },
}


def protocol_digest() -> str:
    return hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True).encode()).hexdigest()


def validate_provenance(corpus: dict[str, Any], preregistration: str) -> None:
    provenance = corpus["provenance"]
    if (
        provenance.get("protocol") != PROTOCOL
        or provenance.get("protocol_sha256") != protocol_digest()
        or provenance.get("preregistration_commit") != preregistration
        or len(corpus["games"]) != 80
        or any(
            provenance.get(key) != PROTOCOL[key]
            for key in (
                "selection",
                "source_url",
                "source_sha256",
                "license",
                "game_count",
                "engine",
                "threads",
                "hash_mb",
                "fast_nodes",
                "focused_nodes",
                "focused_multipv",
                "deep_before_nodes",
                "deep_before_multipv",
                "reset",
            )
        )
    ):
        raise ValueError("M93 holdout provenance differs from the frozen protocol")

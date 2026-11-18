"""M94 preregistration contract. No measurement or promotion occurs at import."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chess.pgn

ROOT = Path(__file__).resolve().parents[4]
FREEZE = ROOT / "tests/fixtures/m94/source_revisions.json"
CODES = frozenset({"tactics.skewer", "tactics.discovered_attack", "tactics.back_rank"})
PROTOCOL: dict[str, Any] = {
    "revision": "m94-motifs-v1",
    "selection": "Next 80 qualifying games after original40, M78, M79 and M93; ordinal 281..360.",
    "qualifiers": "error-free PGN; both Elo 600..1600 inclusive; at least 20 plies",
    "source_url": "https://database.lichess.org/standard/lichess_db_standard_rated_2013-01.pgn.zst",
    "source_sha256": "aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635",
    "license": "CC0",
    "game_count": 80,
    "engine": "Stockfish 19",
    "threads": 1,
    "hash_mb": 16,
    "fast_nodes": 10_000,
    "fast_swing_threshold_cp": 150,
    "motif_swing_threshold_cp": 200,
    "focused_nodes": 1_000_000,
    "focused_multipv": 4,
    "deep_before_nodes": 1_000_000,
    "deep_before_multipv": 4,
    "reset": "ucinewgame before each position",
    "minimum_precision": 0.80,
    "minimum_support_tp_fp": 10,
    "codes": sorted(CODES),
    "oracle": "m94_reference.reference_labels; first independently searched deep source PV",
    "revisions": json.loads(FREEZE.read_text()),
}


def protocol_digest() -> str:
    return hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class MeasurementIdentity:
    """Expected identities pinned by the measured-data commit, not by input artifacts."""

    preregistration_commit: str
    recording_sha256: str
    pgn_sha256: str
    manifest_sha256: str
    protocol_sha256: str
    source_revisions_sha256: str


def validate_frozen_sources(root: Path = ROOT) -> None:
    """Before any new search, require the entire preregistration to be committed."""
    frozen = [
        *PROTOCOL["revisions"],
        "src/scan64/learning/evaluation/m94_protocol.py",
        "tests/fixtures/m94/source_revisions.json",
    ]
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", *frozen], cwd=root, check=True)
    for name in frozen:
        # git diff alone does not see untracked files.
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", name], cwd=root, check=True, capture_output=True
        )
    for name, digest in PROTOCOL["revisions"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Frozen M94 implementation changed: {name}")


def validate_provenance(
    corpus: dict[str, Any], manifest: dict[str, Any], expected: MeasurementIdentity
) -> None:
    provenance = corpus.get("provenance", {})
    preregistration = provenance.get("preregistration_commit", "")
    if (
        provenance.get("protocol") != PROTOCOL
        or provenance.get("protocol_sha256") != protocol_digest()
        or not isinstance(preregistration, str)
        or re.fullmatch(r"[0-9a-f]{40}", preregistration) is None
        or preregistration != expected.preregistration_commit
        or protocol_digest() != expected.protocol_sha256
        or hashlib.sha256(FREEZE.read_bytes()).hexdigest() != expected.source_revisions_sha256
        or manifest.get("preregistration_commit") != preregistration
        or manifest.get("protocol_sha256") != protocol_digest()
        or manifest.get("source_revisions_sha256") != expected.source_revisions_sha256
        or manifest.get("recording_sha256") != expected.recording_sha256
        or manifest.get("pgn_sha256") != expected.pgn_sha256
        or len(corpus.get("games", [])) != 80
        or any(
            provenance.get(key) != value
            for key, value in PROTOCOL.items()
            if key
            not in {
                "revision",
                "qualifiers",
                "codes",
                "oracle",
                "revisions",
                "minimum_precision",
                "minimum_support_tp_fp",
                "fast_swing_threshold_cp",
                "motif_swing_threshold_cp",
            }
        )
    ):
        raise ValueError("M94 holdout provenance differs from the frozen protocol")


def validate_recording(
    path: Path, prior_paths: tuple[Path, ...], expected: MeasurementIdentity
) -> dict[str, Any]:
    """Replay checks pinned artifacts without Git, engines or optional fallbacks."""
    manifest_path = path.with_name("m94_fresh80_manifest.json")
    pgn = path.with_name("m94_fresh80_real_games.pgn")
    if (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() != expected.manifest_sha256
        or hashlib.sha256(path.read_bytes()).hexdigest() != expected.recording_sha256
        or hashlib.sha256(pgn.read_bytes()).hexdigest() != expected.pgn_sha256
    ):
        raise ValueError("M94 artifact checksum differs from the pinned measurement identity")
    manifest = json.loads(manifest_path.read_text())
    corpus: dict[str, Any] = json.loads(path.read_text())
    validate_provenance(corpus, manifest, expected)
    if corpus["provenance"].get("pgn_sha256") != manifest["pgn_sha256"]:
        raise ValueError("M94 PGN checksum differs from recording provenance")
    prior_ids = {
        game["id"] for prior in prior_paths for game in json.loads(prior.read_text())["games"]
    }
    ids = [game["id"] for game in corpus["games"]]
    if len(set(ids)) != 80 or prior_ids.intersection(ids):
        raise ValueError("M94 holdout contains duplicate or previously exposed games")
    with io.StringIO(pgn.read_text()) as stream:
        selected = []
        while (game := chess.pgn.read_game(stream)) is not None:
            if (
                game.errors
                or any(
                    not game.headers.get(key, "").isdecimal()
                    or not 600 <= int(game.headers[key]) <= 1600
                    for key in ("WhiteElo", "BlackElo")
                )
                or len(list(game.mainline_moves())) < 20
            ):
                raise ValueError("M94 PGN contains a nonqualifying game")
            selected.append(game)
    if len(selected) != 80 or any(
        game.headers.get("Site") != recorded["id"]
        or list(game.mainline_moves()) != _recorded_moves(recorded)
        for game, recorded in zip(selected, corpus["games"], strict=True)
    ):
        raise ValueError("M94 PGN games differ from the recording")
    return corpus


def _recorded_moves(game: dict[str, Any]) -> list[chess.Move]:
    board = chess.Board()
    moves = []
    for san in game["moves"]:
        move = board.parse_san(san)
        moves.append(move)
        board.push(move)
    return moves

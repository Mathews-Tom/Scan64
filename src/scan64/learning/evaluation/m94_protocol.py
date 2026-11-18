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
FREEZE = ROOT / "tests/fixtures/m94/source_revisions_v2.json"
PREFIX = "m94_v2_fresh80"
CODES = frozenset({"tactics.skewer", "tactics.discovered_attack", "tactics.back_rank"})
_DEFINITION: dict[str, Any] = {
    "revision": "m94-motifs-v2",
    "selection": (
        "Next 80 qualifying games after original40, M78, M79, M93 and M94 v1; ordinal 361..440."
    ),
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
}


def _load_protocol(root: Path = ROOT) -> tuple[dict[str, Any], bytes]:
    freeze_path = FREEZE if root == ROOT else root / "tests/fixtures/m94/source_revisions_v2.json"
    freeze_bytes = freeze_path.read_bytes()
    revisions = json.loads(freeze_bytes)
    if not isinstance(revisions, dict) or not revisions:
        raise ValueError("M94 v2 source revisions must be a nonempty mapping")
    return {**_DEFINITION, "revisions": revisions}, freeze_bytes


def get_protocol(root: Path = ROOT) -> dict[str, Any]:
    """Load the v2 freeze only when an M94 operation is explicitly requested."""
    return _load_protocol(root)[0]


def protocol_digest(protocol: dict[str, Any] | None = None) -> str:
    loaded = get_protocol() if protocol is None else protocol
    return hashlib.sha256(json.dumps(loaded, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class MeasurementIdentity:
    """Expected identities pinned by the measured-data commit, not by input artifacts."""

    preregistration_commit: str
    recording_sha256: str
    pgn_sha256: str
    manifest_sha256: str
    protocol_sha256: str
    source_revisions_sha256: str


def validate_frozen_sources(preregistration_commit: str, root: Path = ROOT) -> dict[str, Any]:
    """Bind all recording sources to the caller's immutable preregistration SHA."""
    if re.fullmatch(r"[0-9a-f]{40}", preregistration_commit) is None:
        raise ValueError("M94 requires an explicit full preregistration commit SHA")
    resolved = (
        subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{preregistration_commit}^{{commit}}"], cwd=root
        )
        .decode()
        .strip()
    )
    if resolved != preregistration_commit:
        raise ValueError("M94 preregistration must identify the exact commit")
    protocol, freeze_bytes = _load_protocol(root)
    bound = {
        **protocol["revisions"],
        "src/scan64/learning/evaluation/m94_protocol.py": None,
        FREEZE.relative_to(ROOT).as_posix(): None,
    }
    for name, digest in bound.items():
        if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Invalid frozen M94 source path")
        current = (
            freeze_bytes
            if name == FREEZE.relative_to(ROOT).as_posix()
            else (root / name).read_bytes()
        )
        committed = subprocess.check_output(
            ["git", "show", f"{preregistration_commit}:{name}"], cwd=root
        )
        if current != committed or (
            digest is not None and hashlib.sha256(current).hexdigest() != digest
        ):
            raise ValueError(f"Frozen M94 implementation changed: {name}")
    return protocol


def validate_provenance(
    corpus: dict[str, Any], manifest: dict[str, Any], expected: MeasurementIdentity
) -> None:
    protocol, freeze_bytes = _load_protocol()
    digest = protocol_digest(protocol)
    provenance = corpus.get("provenance", {})
    preregistration = provenance.get("preregistration_commit", "")
    if (
        provenance.get("protocol") != protocol
        or provenance.get("protocol_sha256") != digest
        or not isinstance(preregistration, str)
        or re.fullmatch(r"[0-9a-f]{40}", preregistration) is None
        or preregistration != expected.preregistration_commit
        or digest != expected.protocol_sha256
        or hashlib.sha256(freeze_bytes).hexdigest() != expected.source_revisions_sha256
        or manifest.get("preregistration_commit") != preregistration
        or manifest.get("protocol_sha256") != digest
        or manifest.get("source_revisions_sha256") != expected.source_revisions_sha256
        or manifest.get("recording_sha256") != expected.recording_sha256
        or manifest.get("pgn_sha256") != expected.pgn_sha256
        or len(corpus.get("games", [])) != 80
        or any(
            provenance.get(key) != value
            for key, value in protocol.items()
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
    if path.name == "m94_fresh80_recorded.json":
        raise ValueError(
            "Archived M94 v1 requires immutable fa6dc9c reproduction, not v2 rescoring"
        )
    manifest_bytes = path.with_name(f"{PREFIX}_manifest.json").read_bytes()
    recording_bytes = path.read_bytes()
    pgn_bytes = path.with_name(f"{PREFIX}_real_games.pgn").read_bytes()
    if (
        hashlib.sha256(manifest_bytes).hexdigest() != expected.manifest_sha256
        or hashlib.sha256(recording_bytes).hexdigest() != expected.recording_sha256
        or hashlib.sha256(pgn_bytes).hexdigest() != expected.pgn_sha256
    ):
        raise ValueError("M94 artifact checksum differs from the pinned measurement identity")
    manifest = json.loads(manifest_bytes)
    return validate_artifacts(recording_bytes, pgn_bytes, prior_paths, expected, manifest)


def validate_artifacts(
    recording_bytes: bytes,
    pgn_bytes: bytes,
    prior_paths: tuple[Path, ...],
    expected: MeasurementIdentity,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Validate complete preserved buffers; also used by search-free finalization."""
    if (
        hashlib.sha256(recording_bytes).hexdigest() != expected.recording_sha256
        or hashlib.sha256(pgn_bytes).hexdigest() != expected.pgn_sha256
    ):
        raise ValueError("M94 preserved artifact checksum mismatch")
    corpus: dict[str, Any] = json.loads(recording_bytes)
    validate_provenance(corpus, manifest, expected)
    if corpus["provenance"].get("pgn_sha256") != manifest["pgn_sha256"]:
        raise ValueError("M94 PGN checksum differs from recording provenance")
    prior_ids = {
        game["id"] for prior in prior_paths for game in json.loads(prior.read_text())["games"]
    }
    ids = [game["id"] for game in corpus["games"]]
    if len(set(ids)) != 80 or prior_ids.intersection(ids):
        raise ValueError("M94 holdout contains duplicate or previously exposed games")
    with io.StringIO(pgn_bytes.decode("utf-8")) as stream:
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
    for game in corpus["games"]:
        if (
            len(game.get("fast", [])) != len(game["moves"]) + 1
            or any(not isinstance(raw, list) or not raw for raw in game["fast"])
            or not isinstance(game.get("focused"), dict)
            or not isinstance(game.get("deep_before"), dict)
            or game["focused"].keys() != game["deep_before"].keys()
            or any(
                not key.isdecimal()
                or not 0 <= int(key) < len(game["moves"])
                or not game["focused"][key]
                or not game["deep_before"][key]
                for key in game["focused"]
            )
        ):
            raise ValueError("M94 recording is incomplete; STOP without another search")
    return corpus


def _recorded_moves(game: dict[str, Any]) -> list[chess.Move]:
    board = chess.Board()
    moves = []
    for san in game["moves"]:
        move = board.parse_san(san)
        moves.append(move)
        board.push(move)
    return moves

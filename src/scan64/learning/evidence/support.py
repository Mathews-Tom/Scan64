"""Per-code saved-input capability, distinct from measured product trust."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import chess

from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION
from scan64.learning.evidence.motifs import CAPABILITIES, motif_payloads

# Explicit: adding a taxonomy entry must not make legacy saved input support it.
M48_SUPPORTED_CODES = frozenset(
    {
        "board_awareness.hanging_piece",
        "threat_processing.missed_check",
        "threat_processing.missed_capture",
        "threat_processing.missed_direct_threat",
        "threat_processing.allowed_mate",
        "tactics.fork.knight",
        "tactics.pin",
        "tactics.overloaded_defender",
        "calculation.stopped_too_early",
        "opening.delayed_development",
        "positional.king_safety_neglect",
    }
)


def supports_saved_diagnosis(code: str, payload: Mapping[str, Any]) -> bool:
    """Whether this saved evidence format can evaluate code, not whether it is trusted.

    Unknown codes fail closed. New codes require an exact per-code revision;
    neither global m48 nor another motif's marker grants that capability.
    """
    if code in CAPABILITIES:
        markers = payload.get("diagnosis_capabilities")
        return isinstance(markers, Mapping) and markers.get(code) == CAPABILITIES[code]
    return (
        code in M48_SUPPORTED_CODES
        and payload.get("diagnosis_rule_version") == DIAGNOSIS_RULE_VERSION
    )


def validated_motif_line(
    code: str, payload: Mapping[str, Any], played_move: str | None = None
) -> list[str] | None:
    """Reprove a saved motif for detection and rendering from the same legal moves."""
    fen, line = payload.get("before_fen"), payload.get("fast_line")
    saved_played = payload.get("played_move")
    if (
        code not in CAPABILITIES
        or not supports_saved_diagnosis(code, payload)
        or not isinstance(fen, str)
        or not isinstance(line, list)
        or not line
        or not all(isinstance(uci, str) for uci in line)
        or not isinstance(saved_played, str)
        or payload.get("best_move") != line[0]
    ):
        return None
    try:
        board = chess.Board(fen)
        if not board.is_valid():
            return None
        played = board.parse_san(saved_played)
        if not played or played not in board.legal_moves or played.uci() == line[0]:
            return None
        if played_move is not None and board.parse_san(played_move) != played:
            return None
    except ValueError:
        return None
    for proof in motif_payloads(board, line, payload.get("source_score_mate")):
        if f"tactics.{proof['tactic_type']}" == code and all(
            type(payload.get(key)) is type(value) and payload.get(key) == value
            for key, value in proof.items()
        ):
            return line
    return None

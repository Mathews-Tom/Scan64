"""Per-code saved-input capability, distinct from measured product trust."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION

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

    Unknown codes fail closed. M94 can add per-code capability markers here without
    invalidating m48 evidence or changing this call interface for M95.
    """
    return (
        code in M48_SUPPORTED_CODES
        and payload.get("diagnosis_rule_version") == DIAGNOSIS_RULE_VERSION
    )

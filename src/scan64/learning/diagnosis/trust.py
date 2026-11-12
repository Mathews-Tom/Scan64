"""Frozen allowlist measured by the M48 real-game report.

Promote only with precision >= 0.80 and support (TP + FP) >= 10. CI checks
these declared codes, rather than silently shrinking trust on a regression.
No independent human review is implied (development-plan gap G2).
"""

TRUSTED_CODES: frozenset[str] = frozenset(
    {
        "board_awareness.hanging_piece",
        "threat_processing.missed_check",
        "threat_processing.missed_capture",
        "threat_processing.missed_direct_threat",
    }
)

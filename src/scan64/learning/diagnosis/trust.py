"""Frozen allowlist measured by the M48 real-game report.

Promote only with precision >= 0.80 and support (TP + FP) >= 10. CI checks
these declared codes, rather than silently shrinking trust on a regression.
Allowed mate is gated on the fixed fresh80 holdout alone; the original four
codes retain the original40 gate. Source-side search evidence is shared by
the corrected allowed-mate detector and reference, not independently proven.
No independent human review is implied (development-plan gap G2).
"""

TRUSTED_CODES: frozenset[str] = frozenset(
    {
        "board_awareness.hanging_piece",
        "threat_processing.missed_check",
        "threat_processing.missed_capture",
        "threat_processing.missed_direct_threat",
        "threat_processing.allowed_mate",
    }
)

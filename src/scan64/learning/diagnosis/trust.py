"""Frozen allowlist measured by the real-game precision reports.

Promote only with precision >= 0.80 and support (TP + FP) >= 10. CI checks
these declared codes, rather than silently shrinking trust on a regression.
Allowed mate is gated on M78 fresh80 and missed capture on M79 fresh80 alone;
the other three established codes retain the original40 gate. Knight fork alone
cleared M93's separate fresh80 gate; pin and overloaded defender remain hidden.
Search evidence is engine-derived, not independently reviewed coaching truth.
No independent human review is implied (development-plan gap G2).
"""

TRUSTED_CODES: frozenset[str] = frozenset(
    {
        "board_awareness.hanging_piece",
        "threat_processing.missed_check",
        "threat_processing.missed_capture",
        "threat_processing.missed_direct_threat",
        "threat_processing.allowed_mate",
        "tactics.fork.knight",
    }
)

# Archive validation checks eligibility at award, not the current product policy.
# Never shrink this history on demotion: persisted awards/credits are immutable.
HISTORICALLY_TRUSTED_CODES: frozenset[str] = frozenset(
    {
        "board_awareness.hanging_piece",
        "threat_processing.missed_check",
        "threat_processing.missed_capture",
        "threat_processing.missed_direct_threat",
        "threat_processing.allowed_mate",
        "tactics.fork.knight",
    }
)

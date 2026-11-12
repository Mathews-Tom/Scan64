"""Frozen allowlist measured by the M48 real-game report.

Promote only with precision >= 0.80 and support (TP + FP) >= 10. CI checks
these declared codes, rather than silently shrinking trust on a regression.
No independent human review is implied (development-plan gap G2).
"""

# The independent deep/local-exchange reference qualifies no baseline detector.
# The hardening branch must establish at least three, including hanging_piece.
TRUSTED_CODES: frozenset[str] = frozenset()

"""
Curated tactics puzzle set.
These deliberately small v1 content items are used to evaluate learning and provide baseline data.
"""

TACTICS_PUZZLES = [
    {
        "id": "tactic_v1_001",
        "fen": "6k1/5ppp/8/8/8/8/6PP/3Q2K1 w - - 0 1",
        "solution": ["d1d8"],
        "provenance": "Original Scan64 composition (2026-07-14)",
        "licence": "CC0-1.0",
        "skill_mapping": {"tactics.back_rank_mate": 1.0},
        "difficulty_estimate": 800.0,
    },
    {
        "id": "tactic_v1_002",
        "fen": "3q3k/8/8/4N3/8/8/8/3Q2K1 w - - 0 1",
        "solution": ["e5f7"],
        "provenance": "Original Scan64 composition (2026-07-14)",
        "licence": "CC0-1.0",
        "skill_mapping": {"tactics.knight_fork": 1.0},
        "difficulty_estimate": 900.0,
    },
]

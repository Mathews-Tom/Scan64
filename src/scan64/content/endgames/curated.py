"""
Curated endgame fundamentals with Syzygy tablebase verification.
These deliberately small v1 content items are used to evaluate learning and provide baseline data.
"""

ENDGAME_PUZZLES = [
    {
        "id": "endgame_v1_001",
        "fen": "7k/8/8/8/8/8/Q3K3/8 w - - 0 1",
        "solution": ["e2e3"],
        "provenance": "Original Scan64 composition (2026-07-14)",
        "licence": "CC0-1.0",
        "skill_mapping": {"endgame.mate_with_queen": 1.0},
        "difficulty_estimate": 800.0,
    },
    {
        "id": "endgame_v1_002",
        "fen": "6k1/8/8/8/8/8/1Q2K3/8 w - - 0 1",
        "solution": ["e2d3"],
        "provenance": "Original Scan64 composition (2026-07-14)",
        "licence": "CC0-1.0",
        "skill_mapping": {"endgame.mate_with_queen": 1.0},
        "difficulty_estimate": 900.0,
    },
]

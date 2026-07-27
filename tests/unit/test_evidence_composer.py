from __future__ import annotations

import chess

from scan64.learning.evidence.composer import _opening_payload


def test_opening_payload_counts_a_minor_piece_across_squares() -> None:
    payload = _opening_payload(
        ["Nf3", "a6", "Ng1", "a5", "Nf3", "h6", "Ng1", "h5"],
        chess.WHITE,
        None,
    )

    assert payload == {
        "issue": "delayed_development",
        "tempo_loss": 2.0,
        "pawn_moves": 0,
        "repeated_piece_moves": 3,
        "minor_pieces_developed": 1,
    }

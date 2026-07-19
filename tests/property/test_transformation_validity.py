from __future__ import annotations

import chess
from hypothesis import given
from hypothesis import strategies as st

from scan64.learning.exercises.transfer import (
    mirror_and_swap_colours,
    mirror_move,
    verify_mirror_preserves_legal_moves,
)


@st.composite
def legal_board(draw):
    board = chess.Board()
    move_count = draw(st.integers(min_value=0, max_value=80))
    for _ in range(move_count):
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            break
        board.push(draw(st.sampled_from(legal_moves)))
    return board


@given(legal_board())
def test_mirror_preserves_legal_move_relationships(board: chess.Board) -> None:
    transformed_board = chess.Board(mirror_and_swap_colours(board.fen()))
    expected_moves = {mirror_move(move) for move in board.legal_moves}

    assert transformed_board.is_valid()
    assert set(transformed_board.legal_moves) == expected_moves
    verify_mirror_preserves_legal_moves(board.fen())

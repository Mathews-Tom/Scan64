"""Board construction and replay shared by the analysis paths."""

from __future__ import annotations

from collections.abc import Sequence

import chess

from scan64.chess.variants import STANDARD, VariantName, board_class


def board_from(initial_fen: str | None, variant: VariantName = STANDARD) -> chess.Board:
    """A board at ``initial_fen``, or the variant's start when it is absent.

    ``chess.Board(None)`` is an empty board and ``chess.Board("")`` raises, so
    the absent case must be spelled out rather than passed through.
    """
    cls = board_class(variant)
    return cls(initial_fen) if initial_fen else cls()


def uci_moves_to_san(uci_moves: Sequence[str], initial_fen: str | None = None) -> list[str]:
    """Replay a UCI move sequence from ``initial_fen``, returning SAN."""
    board = board_from(initial_fen)
    san_moves = []
    for uci in uci_moves:
        move = chess.Move.from_uci(uci)
        san_moves.append(board.san(move))
        board.push(move)
    return san_moves

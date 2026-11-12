"""Local move facts, not engine proofs or claims about a strategic plan."""

from typing import Literal

import chess

PrincipleKind = Literal[
    "development", "castling", "centre", "rook_file", "king_escape", "passed_pawn"
]
CENTRE = chess.SquareSet([chess.D4, chess.E4, chess.D5, chess.E5])


def _king_escapes(board: chess.Board, color: chess.Color) -> set[chess.Square]:
    probe = board.copy(stack=False)
    probe.turn = color
    probe.ep_square = None
    king = probe.king(color)
    home_rank = 0 if color else 7
    return {
        move.to_square
        for move in probe.legal_moves
        if move.from_square == king and chess.square_rank(move.to_square) != home_rank
    }


def _back_rank_mate_threat(board: chess.Board, color: chess.Color) -> bool:
    probe = board.copy(stack=False)
    probe.turn = not color
    probe.ep_square = None
    home_rank = 0 if color else 7
    for move in list(probe.legal_moves):
        if (
            probe.piece_type_at(move.from_square) not in (chess.ROOK, chess.QUEEN)
            or chess.square_rank(move.to_square) != home_rank
        ):
            continue
        probe.push(move)
        mate = probe.is_checkmate()
        probe.pop()
        if mate:
            return True
    return False


def principle_facts(
    board: chess.Board, move: chess.Move, after: chess.Board
) -> list[tuple[PrincipleKind, str, list[chess.Square]]]:
    """Describe a legal move's board predicates without evaluating its merit.

    Callers must first restrict candidates by engine tolerance. Control means
    geometric attacks by the moved piece, not a claim of a safe occupation.
    """
    color = board.turn
    piece = board.piece_at(move.from_square)
    assert piece is not None
    facts: list[tuple[PrincipleKind, str, list[chess.Square]]] = []
    home_rank = 0 if color else 7
    if board.is_castling(move):
        facts.append(("castling", "Castles the king and rook.", [move.to_square]))
    elif (
        piece.piece_type in (chess.KNIGHT, chess.BISHOP)
        and chess.square_rank(move.from_square) == home_rank
        and chess.square_rank(move.to_square) != home_rank
    ):
        facts.append(
            (
                "development",
                f"Develops a {chess.piece_name(piece.piece_type)} from the back rank.",
                [move.from_square, move.to_square],
            )
        )

    if move.to_square in CENTRE:
        facts.append(("centre", "Occupies a centre square.", [move.to_square]))
    else:
        added = (after.attacks(move.to_square) - board.attacks(move.from_square)) & CENTRE
        if added:
            facts.append(("centre", "Adds the moved piece's control of the centre.", list(added)))

    if piece.piece_type == chess.ROOK:
        file_mask = chess.BB_FILES[chess.square_file(move.to_square)]
        # A file label describes arrival on a different file, not a rook already there.
        if (
            chess.square_file(move.from_square) != chess.square_file(move.to_square)
            and not after.pawns & after.occupied_co[color] & file_mask
        ):
            label = (
                "half-open" if after.pawns & after.occupied_co[not color] & file_mask else "open"
            )
            facts.append(("rook_file", f"Puts a rook on an {label} file.", [move.to_square]))

    if piece.piece_type == chess.PAWN:
        direction = 1 if color else -1
        advances = (
            chess.square_rank(move.to_square) - chess.square_rank(move.from_square)
        ) * direction
        if advances > 0 and not board.is_capture(move) and move.promotion is None:
            ahead = range(
                chess.square_rank(move.from_square) + direction,
                8 if color else -1,
                direction,
            )
            files = range(
                max(0, chess.square_file(move.from_square) - 1),
                min(7, chess.square_file(move.from_square) + 1) + 1,
            )
            blockers = any(
                board.piece_at(chess.square(file, rank)) == chess.Piece(chess.PAWN, not color)
                for rank in ahead
                for file in files
            )
            if not blockers:
                facts.append(("passed_pawn", "Advances a passed pawn.", [move.to_square]))
        king = board.king(color)
        if (
            king is not None
            and chess.square_rank(king) == home_rank
            and not _king_escapes(board, color)
            and (escapes := _king_escapes(after, color))
            and _back_rank_mate_threat(board, color)
            and not _back_rank_mate_threat(after, color)
        ):
            facts.append(
                (
                    "king_escape",
                    "Makes a king escape square against a back-rank mate.",
                    sorted(escapes),
                )
            )
    return facts

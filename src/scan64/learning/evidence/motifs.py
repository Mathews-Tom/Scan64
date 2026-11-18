"""M94 fast-source operational proofs. Capability is not measured trust.

Only the first legal source PV is used. Definitions are preregistered in
 docs/evaluation/m94-motif-v2-protocol.md; the deep oracle is implemented separately.
"""

from __future__ import annotations

from typing import Any

import chess

CAPABILITIES = {
    "tactics.skewer": "m94-skewer-v2",
    "tactics.discovered_attack": "m94-discovered-attack-v2",
    "tactics.back_rank": "m94-back-rank-v2",
}
_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(
        value * (len(board.pieces(kind, color)) - len(board.pieces(kind, not color)))
        for kind, value in _VALUES.items()
    )


def _boards(board: chess.Board, line: list[str]) -> list[chess.Board]:
    if not board.is_valid():
        return []
    result = [board.copy(stack=False)]
    for uci in line:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return []
        if move not in result[-1].legal_moves:
            return []
        next_board = result[-1].copy(stack=False)
        next_board.push(move)
        result.append(next_board)
    return result


def _local_gain(boards: list[chess.Board], line: list[str], target: int) -> int:
    start = boards[2]
    end = start
    for index in range(2, len(line)):
        move = chess.Move.from_uci(line[index])
        if move.to_square != target or not boards[index].is_capture(move):
            break
        end = boards[index + 1]
    return _material(end, start.turn) - _material(start, start.turn)


def _mating(boards: list[chess.Board], mate: object) -> bool:
    return (
        isinstance(mate, int)
        and not isinstance(mate, bool)
        and 1 <= mate <= 3
        and len(boards) == 2 * mate
        and boards[-1].is_checkmate()
    )


def motif_payloads(board: chess.Board, line: list[str], mate: object) -> list[dict[str, Any]]:
    """Return grounded fast-PV hypotheses, never deep-reference labels."""
    boards = _boards(board, line)
    if len(boards) < 2:
        return []
    root = chess.Move.from_uci(line[0])
    if board.is_castling(root):
        return []
    after = boards[1]
    moving = board.piece_at(root.from_square)
    assert moving is not None
    gain = _material(boards[-1], board.turn) - _material(board, board.turn)
    payloads: list[dict[str, Any]] = []
    if moving.piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN) and len(boards) >= 4:
        escape = chess.Move.from_uci(line[1])
        capture = chess.Move.from_uci(line[2])
        front = after.piece_at(escape.from_square)
        rear = boards[2].piece_at(capture.to_square)
        if (
            front is not None
            and rear is not None
            and front.color != board.turn
            and rear.color == front.color
            and rear.piece_type not in (chess.PAWN, chess.KING)
            and (
                front.piece_type == chess.KING
                or _VALUES[front.piece_type] > _VALUES[rear.piece_type]
            )
            and escape.from_square in after.attacks(root.to_square)
            and escape.from_square not in board.attacks(root.from_square)
            and chess.between(root.to_square, capture.to_square)
            & chess.BB_SQUARES[escape.from_square]
            and not (
                chess.between(root.to_square, capture.to_square)
                & after.occupied
                & ~chess.BB_SQUARES[escape.from_square]
            )
            and capture.from_square == root.to_square
            and boards[2].is_capture(capture)
            and gain >= 1
            and _local_gain(boards, line, capture.to_square) >= 1
        ):
            payloads.append(
                {
                    "tactic_type": "skewer",
                    "front_square": chess.square_name(escape.from_square),
                    "front_piece": front.symbol(),
                    "target_square": chess.square_name(capture.to_square),
                    "target_piece": rear.symbol(),
                    "slider_square": chess.square_name(root.to_square),
                    "material_gain": gain,
                }
            )
    for square, slider in board.piece_map().items():
        if slider.color != board.turn or slider.piece_type not in (
            chess.BISHOP,
            chess.ROOK,
            chess.QUEEN,
        ):
            continue
        if square == root.from_square or after.piece_at(square) != slider:
            continue
        for target in after.attacks(square):
            victim = after.piece_at(target)
            if (
                victim is None
                or victim.color == board.turn
                or victim.piece_type == chess.PAWN
                or target in board.attacks(square)
                or not (chess.between(square, target) & chess.BB_SQUARES[root.from_square])
            ):
                continue
            material_win = False
            if len(boards) >= 4 and victim.piece_type != chess.KING:
                capture = chess.Move.from_uci(line[2])
                material_win = (
                    capture.from_square == square
                    and capture.to_square == target
                    and boards[2].piece_at(target) == victim
                    and boards[2].is_capture(capture)
                    and gain >= 1
                    and _local_gain(boards, line, target) >= 1
                )
            if material_win or victim.piece_type == chess.KING and _mating(boards, mate):
                payloads.append(
                    {
                        "tactic_type": "discovered_attack",
                        "slider_square": chess.square_name(square),
                        "slider_piece": slider.symbol(),
                        "blocker_square": chess.square_name(root.from_square),
                        "target_square": chess.square_name(target),
                        "target_piece": victim.symbol(),
                        "material_gain": gain,
                    }
                )
                break
    if _mating(boards, mate):
        final = boards[-1]
        king = final.king(not board.turn)
        home_rank = 7 if board.turn == chess.WHITE else 0
        forward_rank = 6 if board.turn == chess.WHITE else 1
        checker_squares = list(final.checkers())
        if king is not None and chess.square_rank(king) == home_rank:
            blockers = [
                chess.square(file, forward_rank)
                for file in range(
                    max(0, chess.square_file(king) - 1), min(8, chess.square_file(king) + 2)
                )
            ]
            checking_root = after.piece_at(root.to_square)
            if (
                checking_root is not None
                and checking_root.piece_type in (chess.ROOK, chess.QUEEN)
                and chess.square_rank(root.to_square) == home_rank
                and root.to_square in after.checkers()
                and all(
                    (piece := final.piece_at(sq)) is not None and piece.color != board.turn
                    for sq in blockers
                )
                and any(
                    (piece := final.piece_at(sq)) is not None
                    and piece.piece_type in (chess.ROOK, chess.QUEEN)
                    and chess.square_rank(sq) == home_rank
                    for sq in checker_squares
                )
            ):
                payloads.append(
                    {
                        "tactic_type": "back_rank",
                        "king_square": chess.square_name(king),
                        "blocker_squares": [chess.square_name(sq) for sq in blockers],
                        "mating_line": line,
                        "mate_in": mate,
                    }
                )
    return payloads

"""M94 operational reference over raw million-node source PVs only.

No evidence/composer/detector imports: label decisions are separately implemented.
The same engine is shared; this is not independent human-coach validation.
"""

from __future__ import annotations

from typing import Any

import chess

_WORTH = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def _balance(position: chess.Board, side: chess.Color) -> int:
    value = 0
    for piece in position.piece_map().values():
        value += _WORTH.get(piece.piece_type, 0) * (1 if piece.color == side else -1)
    return value


def reference_labels(
    before: chess.Board, after: chess.Board, deep_source: list[dict[str, Any]]
) -> set[str]:
    """Label a missed source tactic; raw deep reply evidence is not a source PV."""
    if not before.is_valid() or not deep_source:
        return set()
    raw = deep_source[0]
    pv = raw.get("pv")
    if not isinstance(pv, list) or not pv or not all(isinstance(uci, str) for uci in pv):
        return set()
    positions = [before.copy(stack=False)]
    moves: list[chess.Move] = []
    for uci in pv:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return set()
        if move not in positions[-1].legal_moves:
            return set()
        next_position = positions[-1].copy(stack=False)
        next_position.push(move)
        positions.append(next_position)
        moves.append(move)
    first = moves[0]
    if before.is_castling(first) or positions[1].fen() == after.fen():
        return set()
    side = before.turn
    root_piece = before.piece_at(first.from_square)
    assert root_piece is not None
    total = _balance(positions[-1], side) - _balance(before, side)
    mate = raw.get("score_mate")
    complete_mate = (
        isinstance(mate, int)
        and not isinstance(mate, bool)
        and 1 <= mate <= 3
        and len(moves) == 2 * mate - 1
        and positions[-1].is_checkmate()
    )
    labels: set[str] = set()
    profitable_capture = False
    if len(moves) >= 3:
        victim_position = positions[2]
        capture = moves[2]
        exchange_end = 2
        for index in range(2, len(moves)):
            if moves[index].to_square != capture.to_square or not positions[index].is_capture(
                moves[index]
            ):
                break
            exchange_end = index + 1
        profitable_capture = (
            victim_position.is_capture(capture)
            and total >= 1
            and _balance(positions[exchange_end], side) - _balance(victim_position, side) >= 1
        )
        if root_piece.piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN):
            retreat = moves[1]
            front = positions[1].piece_at(retreat.from_square)
            rear = victim_position.piece_at(capture.to_square)
            # Enumerate the ray in board coordinates, not the detector's between predicate.
            dx = chess.square_file(capture.to_square) - chess.square_file(first.to_square)
            dy = chess.square_rank(capture.to_square) - chess.square_rank(first.to_square)
            aligned = dx == 0 or dy == 0 or abs(dx) == abs(dy)
            if aligned and (dx or dy):
                step_x, step_y = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
                file, rank = (
                    chess.square_file(first.to_square) + step_x,
                    chess.square_rank(first.to_square) + step_y,
                )
                occupied = []
                while (file, rank) != (
                    chess.square_file(capture.to_square),
                    chess.square_rank(capture.to_square),
                ):
                    square = chess.square(file, rank)
                    if positions[1].piece_at(square) is not None:
                        occupied.append(square)
                    file, rank = file + step_x, rank + step_y
                if (
                    profitable_capture
                    and capture.from_square == first.to_square
                    and front is not None
                    and rear is not None
                    and front.color == rear.color == (not side)
                    and rear.piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
                    and (
                        front.piece_type == chess.KING
                        or _WORTH[front.piece_type] > _WORTH[rear.piece_type]
                    )
                    and occupied == [retreat.from_square]
                    and retreat.from_square in positions[1].attacks(first.to_square)
                    and retreat.from_square not in before.attacks(first.from_square)
                ):
                    labels.add("tactics.skewer")
    for origin, piece in before.piece_map().items():
        if (
            piece.color != side
            or piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN)
            or origin == first.from_square
            or positions[1].piece_at(origin) != piece
        ):
            continue
        newly_attacked = positions[1].attacks(origin) - before.attacks(origin)
        for target in newly_attacked:
            victim = positions[1].piece_at(target)
            if victim is None or victim.color == side or victim.piece_type == chess.PAWN:
                continue
            # The root origin must lie strictly inside this slider-target segment.
            # EP can clear a second blocker too; source-minus-origin is not the board
            # after the move. The actual new attack above proves the opened ray.
            dx = chess.square_file(target) - chess.square_file(origin)
            dy = chess.square_rank(target) - chess.square_rank(origin)
            if not (dx == 0 or dy == 0 or abs(dx) == abs(dy)):
                continue
            steps = max(abs(dx), abs(dy))
            step_x, step_y = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
            segment = {
                chess.square(
                    chess.square_file(origin) + offset * step_x,
                    chess.square_rank(origin) + offset * step_y,
                )
                for offset in range(1, steps)
            }
            if first.from_square not in segment:
                continue
            if victim.piece_type == chess.KING:
                proved = complete_mate
            else:
                proved = (
                    profitable_capture
                    and len(moves) >= 3
                    and moves[2].from_square == origin
                    and moves[2].to_square == target
                    and positions[2].piece_at(target) == victim
                )
            if proved:
                labels.add("tactics.discovered_attack")
    if complete_mate:
        final = positions[-1]
        king = final.king(not side)
        home = 7 if side else 0
        forward = home - 1 if side else home + 1
        if king is not None and chess.square_rank(king) == home:
            flights = [
                chess.square(file, forward)
                for file in range(8)
                if abs(file - chess.square_file(king)) <= 1
            ]
            root_checker = positions[1].piece_at(first.to_square)
            boxed = all(
                (occupant := final.piece_at(square)) is not None and occupant.color != side
                for square in flights
            )
            rank_mate = any(
                (checker := final.piece_at(square)) is not None
                and checker.piece_type in (chess.ROOK, chess.QUEEN)
                and chess.square_rank(square) == home
                for square in final.checkers()
            )
            if (
                boxed
                and rank_mate
                and root_checker is not None
                and root_checker.piece_type in (chess.ROOK, chess.QUEEN)
                and chess.square_rank(first.to_square) == home
                and first.to_square in positions[1].checkers()
            ):
                labels.add("tactics.back_rank")
    return labels

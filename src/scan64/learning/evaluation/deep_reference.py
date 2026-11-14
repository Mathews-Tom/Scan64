"""Frozen reference definitions using only 1M-node, MultiPV-4 recordings.

This module must not depend on evidence composition or detector implementation.
The oracle uses the first DEEP source/reply PV, never the fast detector's PV.
Material attributed to a named piece is limited to consecutive captures on its
square; gains elsewhere later in the PV cannot turn an even trade into a win.
"""

from __future__ import annotations

from typing import Any

import chess

VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def material(board: chess.Board, color: chess.Color) -> int:
    return sum(
        value * (len(board.pieces(piece, color)) - len(board.pieces(piece, not color)))
        for piece, value in VALUES.items()
    )


def replay_line(board: chess.Board, raw: list[dict[str, Any]]) -> list[chess.Board]:
    boards = [board.copy()]
    for uci in raw[0].get("pv", []) if raw else []:
        move = chess.Move.from_uci(uci)
        if move not in boards[-1].legal_moves:
            raise ValueError(f"Illegal recorded PV move {uci}: {boards[-1].fen()}")
        next_board = boards[-1].copy()
        next_board.push(move)
        boards.append(next_board)
    return boards


def exchange_gain(boards: list[chess.Board]) -> int:
    """Board-material delta at the end of the initial, target-local exchange."""
    if len(boards) < 2:
        return 0
    first = boards[1].peek()
    if not boards[0].is_capture(first):
        return 0
    target = first.to_square
    end = boards[1]
    for before, after in zip(boards[1:], boards[2:], strict=False):
        move = after.peek()
        if move.to_square != target or not before.is_capture(move):
            break
        end = after
    color = boards[0].turn
    return material(end, color) - material(boards[0], color)


def reference_labels(
    before: chess.Board,
    after: chess.Board,
    deep_before: list[dict[str, Any]],
    deep_after: list[dict[str, Any]],
) -> set[str]:
    """Operational causes supported by the first independently searched deep PV.

    Calculation/development retain explicit operational definitions; neither is
    a claim about human thought or independently reviewed coaching causality.
    """
    labels: set[str] = set()
    attack = replay_line(before, deep_before)
    defence = replay_line(after, deep_after)
    owner = before.turn
    gain = material(attack[-1], owner) - material(before, owner)
    loss = material(after, owner) - material(defence[-1], owner)
    # Mate scores belong to the side to move, not a fixed White perspective.
    # A non-positive source mate score means this mover was already forced-mated.
    source_mate = deep_before[0].get("score_mate") if deep_before else None
    reply_mate = deep_after[0].get("score_mate") if deep_after else None
    if (
        deep_before
        and reply_mate is not None
        and 1 <= reply_mate <= 3
        and (source_mate is None or source_mate > 0)
        and defence[-1].is_checkmate()
        and defence[-1].turn == owner
    ):
        labels.add("threat_processing.allowed_mate")
    if len(defence) > 1:
        reply = defence[1].peek()
        victim = after.piece_at(reply.to_square)
        if exchange_gain(defence) >= 1 and loss >= 1:
            labels.add("threat_processing.missed_direct_threat")
            if victim is not None and victim.piece_type not in (chess.PAWN, chess.KING):
                labels.add("board_awareness.hanging_piece")
        if defence[-1].is_checkmate() and defence[-1].turn == owner:
            labels.add("positional.king_safety_neglect")
        if len(defence) >= 4 and loss >= 2:
            immediate_loss = material(after, owner) - material(defence[2], owner)
            if immediate_loss < 2:
                labels.add("calculation.stopped_too_early")
    if len(attack) > 1:
        best = attack[1].peek()
        if exchange_gain(attack) >= 1 and gain >= 1:
            labels.add("threat_processing.missed_capture")
        score = deep_before[0]
        if before.gives_check(best) and (
            score.get("score_cp", 0) >= 300 or score.get("score_mate", 0) > 0
        ):
            labels.add("threat_processing.missed_check")
        piece = before.piece_at(best.from_square)
        home_rank = 0 if owner == chess.WHITE else 7
        undeveloped = sum(
            before.piece_type_at(chess.square(file, home_rank)) == kind
            and before.color_at(chess.square(file, home_rank)) == owner
            for file, kind in (
                (1, chess.KNIGHT),
                (2, chess.BISHOP),
                (5, chess.BISHOP),
                (6, chess.KNIGHT),
            )
        )
        if (
            before.fullmove_number <= 10
            and undeveloped >= 3
            and piece is not None
            and piece.piece_type in (chess.KNIGHT, chess.BISHOP)
            and chess.square_rank(best.from_square) == home_rank
            and gain >= 0
        ):
            labels.add("opening.delayed_development")
        if piece is not None and piece.piece_type == chess.KNIGHT and gain >= 1:
            targets = [
                sq
                for sq in attack[1].attacks(best.to_square)
                if (target := attack[1].piece_at(sq)) is not None
                and target.color != owner
                and (target.piece_type == chess.KING or VALUES[target.piece_type] >= 3)
            ]
            if len(targets) >= 2:
                labels.add("tactics.fork.knight")
        for square, target in attack[1].piece_map().items():
            if target.color == owner or target.piece_type == chess.KING:
                continue
            captured = any(
                b.peek().to_square == square
                and a.is_capture(b.peek())
                and a.turn == owner
                and a.piece_at(square) == target
                for a, b in zip(attack[1:], attack[2:], strict=False)
            )
            if (
                gain >= 1
                and attack[1].is_pinned(target.color, square)
                and captured
                and not before.is_pinned(target.color, square)
            ):
                labels.add("tactics.pin")
            guarded = [
                sq
                for sq in attack[1].attacks(square)
                if (p := attack[1].piece_at(sq)) is not None
                and p.color == target.color
                and p.piece_type != chess.KING
                and attack[1].is_attacked_by(owner, sq)
            ]
            lost_targets = sum(
                any(
                    a.turn == owner
                    and b.peek().to_square == sq
                    and a.is_capture(b.peek())
                    and a.piece_at(sq) == attack[1].piece_at(sq)
                    for a, b in zip(attack[1:], attack[2:], strict=False)
                )
                for sq in guarded
            )
            if gain >= 1 and len(guarded) >= 2 and lost_targets >= 2:
                labels.add("tactics.overloaded_defender")
    return labels

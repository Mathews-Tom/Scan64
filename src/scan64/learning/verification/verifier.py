from __future__ import annotations

from typing import Any

import chess
from chess_lesson_spec import (
    DrawArrowCommand,
    DrawAttackMapCommand,
    DrawDefenceMapCommand,
    HighlightPieceCommand,
    HighlightRegionCommand,
    HighlightSquareCommand,
    LessonSpec,
    ShowGhostPieceCommand,
    VisualizationCommand,
)

from scan64.chess.analysis.models import EngineAnalysis


class LessonVerificationError(Exception):
    pass


def verify_lesson(spec: LessonSpec, objective_analysis: EngineAnalysis) -> None:
    """
    Verify that a LessonSpec adheres to §7.7 rules.
    """
    # 1. FEN validity
    try:
        board = chess.Board(spec.source.fen)
    except ValueError as e:
        raise LessonVerificationError(f"Invalid FEN: {e}")

    # 2. Every accepted move must equal the engine- or tablebase-proved move.
    objective_move = _objective_move(board, objective_analysis)
    for accepted_move in spec.interaction.accepted_moves:
        try:
            submitted_move = board.parse_san(accepted_move.san)
        except ValueError:
            raise LessonVerificationError(
                f"Accepted move {accepted_move.san} is illegal in the given position"
            ) from None
        if submitted_move != objective_move:
            raise LessonVerificationError(
                f"Accepted move {accepted_move.san} does not match the "
                f"engine-confirmed objective move {board.san(objective_move)}"
            )

    spec.verification.status = "verified"

    # 4. Provenance retention
    if not spec.source.fen or not spec.diagnosis.primary:
        raise LessonVerificationError("Lesson must retain source FEN and primary diagnosis")

    # 5. Overlay square validity
    for hint in spec.hints:
        for sq in hint.squares:
            if not chess.SQUARE_NAMES.count(sq) and not sq == "":
                raise LessonVerificationError(f"Invalid square {sq} in hint")

        for vis in hint.visualizations:
            _verify_visualization(vis)

    if spec.explanation:
        for vis in spec.explanation.visualizations:
            _verify_visualization(vis)


def _objective_move(board: chess.Board, analysis: EngineAnalysis) -> chess.Move:
    if not analysis.raw_result:
        raise LessonVerificationError("Engine analysis has no principal variation")
    first_result: dict[str, Any] = analysis.raw_result[0]
    principal_variation = first_result.get("pv")
    if not isinstance(principal_variation, list) or not principal_variation:
        raise LessonVerificationError("Engine analysis has no principal variation")
    first_move = principal_variation[0]
    if not isinstance(first_move, str):
        raise LessonVerificationError("Engine analysis has an invalid principal variation")
    try:
        objective_move = chess.Move.from_uci(first_move)
    except ValueError:
        raise LessonVerificationError(
            "Engine analysis has an invalid principal variation"
        ) from None
    if objective_move not in board.legal_moves:
        raise LessonVerificationError(
            "Engine analysis objective move is illegal in the lesson position"
        )
    return objective_move


def _verify_visualization(vis: VisualizationCommand) -> None:
    squares_to_check = []

    if isinstance(vis, HighlightSquareCommand):
        squares_to_check.append(vis.square)
    elif isinstance(vis, HighlightRegionCommand):
        squares_to_check.extend(vis.squares)
    elif isinstance(vis, HighlightPieceCommand):
        squares_to_check.append(vis.square)
    elif isinstance(vis, DrawArrowCommand):
        squares_to_check.extend([vis.origin, vis.target])
    elif isinstance(vis, DrawAttackMapCommand):
        squares_to_check.append(vis.square)
    elif isinstance(vis, DrawDefenceMapCommand):
        squares_to_check.append(vis.square)
    elif isinstance(vis, ShowGhostPieceCommand):
        squares_to_check.append(vis.square)

    for sq in squares_to_check:
        if not chess.SQUARE_NAMES.count(sq) and not sq == "":
            raise LessonVerificationError(f"Invalid square {sq} in visualization command")

from __future__ import annotations

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
from scan64.content.validate import validate_solution_line
from scan64.learning.exercises.sound_moves import sound_moves


class LessonVerificationError(Exception):
    pass


def verify_lesson(spec: LessonSpec, objective_analysis: EngineAnalysis | None = None) -> None:
    """
    Verify that a LessonSpec adheres to §7.7 rules.
    """
    # 1. FEN validity
    try:
        board = chess.Board(spec.source.fen)
    except ValueError as e:
        raise LessonVerificationError(f"Invalid FEN: {e}")

    if spec.verification.status == "source_verified":
        if spec.source.kind != "external":
            raise LessonVerificationError("Source verification requires an external source")
        if spec.interaction.accepted_moves or spec.mastery is not None:
            raise LessonVerificationError(
                "Source verification cannot claim accepted moves or mastery"
            )
        if any(
            value is not None
            for value in (
                spec.verification.engine,
                spec.verification.engine_binary_digest,
                spec.verification.nodes,
                spec.verification.multipv,
            )
        ):
            raise LessonVerificationError("Source verification cannot claim engine provenance")
        error = validate_solution_line(spec.source.fen, spec.interaction.solution)
        if error is not None:
            raise LessonVerificationError(error)
        if len(spec.interaction.solution) % 2 != 1:
            raise LessonVerificationError("Solution line must end on a solver move")
    else:
        if objective_analysis is None:
            raise LessonVerificationError("Engine verification requires objective analysis")
        # Every accepted move must be engine-proved within the shared tolerance.
        if not spec.interaction.accepted_moves:
            raise LessonVerificationError("Lesson must define at least one accepted move")
        try:
            objective_moves = sound_moves(board, objective_analysis)
        except ValueError as error:
            raise LessonVerificationError(str(error)) from error
        for accepted_move in spec.interaction.accepted_moves:
            try:
                submitted_move = board.parse_san(accepted_move.san)
            except ValueError:
                raise LessonVerificationError(
                    f"Accepted move {accepted_move.san} is illegal in the given position"
                ) from None
            if submitted_move not in objective_moves:
                raise LessonVerificationError(
                    f"Accepted move {accepted_move.san} does not match "
                    "an engine-confirmed sound move"
                )

    if spec.source.kind == "player_game" and (spec.diagnosis is None or not spec.diagnosis.primary):
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

    if spec.verification.status != "source_verified":
        spec.verification.status = "verified"


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

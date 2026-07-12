import chess

from scan64.lessonspec.models import (
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


class LessonVerificationError(Exception):
    pass

def verify_lesson(spec: LessonSpec) -> None:
    """
    Verify that a LessonSpec adheres to §7.7 rules.
    """
    # 1. FEN validity
    try:
        board = chess.Board(spec.source.fen)
    except ValueError as e:
        raise LessonVerificationError(f"Invalid FEN: {e}")

    # 2. Side to move (ensure the objective/answers align; for now just parsing)
    _ = board.turn

    # 3. All accepted moves are legal
    for accepted_move in spec.interaction.accepted_moves:
        try:
            _ = board.parse_san(accepted_move.san)
        except ValueError:
            raise LessonVerificationError(
                f"Accepted move {accepted_move.san} is illegal in the given position"
            )

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

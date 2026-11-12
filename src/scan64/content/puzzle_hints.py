"""Project requested hints directly from a source-verified puzzle continuation."""

import chess
from chess_lesson_spec import DrawArrowCommand

from scan64.learning.insights import (
    HintCandidate,
    IdeaInsight,
    InsightStep,
    PositionInsight,
    extract_reasons,
    insight_step,
)


def puzzle_hint(board: chess.Board, line: list[str], step: int) -> InsightStep:
    move = chess.Move.from_uci(line[0])
    reasons = extract_reasons(board, line)
    candidate = HintCandidate(
        number=1,
        move=move.uci(),
        san=board.san(move),
        line=line,
        reasons=reasons,
        fallback=not reasons,
        visualizations=[
            DrawArrowCommand(
                origin=chess.square_name(move.from_square),
                target=chess.square_name(move.to_square),
                description=f"Source continuation: {board.san(move)}.",
            )
        ],
    )
    insight = PositionInsight(
        fen=board.fen(), candidates=[candidate], visualizations=candidate.visualizations
    )
    projected = insight_step(board, insight, step)
    if isinstance(projected, IdeaInsight) and not reasons:
        # A quiet source line has no engine evaluation or proven motif to claim.
        projected.motif = "source continuation"
        projected.text = f"Consider your {projected.piece} and look for the continuation."
    return projected

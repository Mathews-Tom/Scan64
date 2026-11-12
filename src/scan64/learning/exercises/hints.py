"""Attach source-position hints using existing accepted engine lines, without search."""

import chess
from chess_lesson_spec import AnimateLineCommand, Explanation, Hint, LessonSpec

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.insights import insight_step, position_insight


def fill_lesson_hints(spec: LessonSpec, analysis: EngineAnalysis) -> None:
    if spec.hints:
        return
    board = chess.Board(spec.source.fen)
    accepted = {board.parse_san(move.san).uci() for move in spec.interaction.accepted_moves}
    accepted_analysis = analysis.model_copy(
        update={
            "raw_result": [
                raw for raw in analysis.raw_result if raw.get("pv", [None])[0] in accepted
            ]
        }
    )
    insight = position_insight(board, accepted_analysis)
    if not insight.candidates:
        raise ValueError("No accepted engine line for lesson hints")
    for level, kind in ((1, "nudge"), (2, "idea"), (3, "moves")):
        step = insight_step(board, insight, level, accepted_moves=accepted)
        text = (
            step.text
            if step.step != 3
            else "\n".join(
                f"{candidate.number}. {candidate.san}: "
                + (
                    " ".join(reason.description for reason in candidate.reasons)
                    or "engine preference"
                )
                for candidate in insight.candidates
            )
        )
        # Round-trip into the discriminated command types used by LessonSpec.
        spec.hints.append(
            Hint.model_validate(
                {
                    "level": level,
                    "kind": kind,
                    "text": text,
                    "visualizations": [drawing.model_dump() for drawing in step.visualizations],
                }
            )
        )
    if spec.explanation is None:
        spec.explanation = Explanation(text="Review the accepted moves and their engine lines.")
    spec.explanation.visualizations = [
        drawing for hint in spec.hints if hint.level == 3 for drawing in hint.visualizations
    ]
    spec.explanation.visualizations.extend(
        AnimateLineCommand(moves=candidate.line, description=f"{candidate.san}: engine line")
        for candidate in insight.candidates
    )

"""Bounded, legal-line-backed feedback; source-line grading remains separate."""

from __future__ import annotations

from typing import Literal

import chess
from chess_lesson_spec.models import VisualizationCommand
from pydantic import BaseModel, SerializeAsAny

from scan64.learning.insights import position_insight
from scan64.learning.tolerance import review_band, review_loss, win_probability
from scan64.providers.stockfish.pool import EnginePoolManager


class PuzzleMoveFeedback(BaseModel):
    verdict: Literal["mistake", "alternative", "uncertain"]
    summary: str
    reply_line: list[str]
    visualizations: list[SerializeAsAny[VisualizationCommand]]


async def explain_puzzle_move(
    before: chess.Board, move: chess.Move, manager: EnginePoolManager | None
) -> PuzzleMoveFeedback:
    """Compare both side-to-move scores, never equate off-source with bad.

    Each of the two sequential searches uses the manager's reserved, bounded
    interactive admission. No database state or scoring is touched here.
    """
    if move not in before.legal_moves:
        raise ValueError("Illegal candidate move")
    after = before.copy()
    after.push(move)
    outcome = after.outcome(claim_draw=False)
    if outcome is not None:
        if outcome.winner is not None:
            return PuzzleMoveFeedback(
                verdict="alternative",
                summary="This move delivers checkmate. Source-line grading is unchanged.",
                reply_line=[],
                visualizations=[],
            )
        termination = outcome.termination.name.lower().replace("_", " ")
        return PuzzleMoveFeedback(
            verdict="uncertain",
            summary=f"This move ends the game in a draw ({termination}). "
            "That fact alone does not establish whether it gives up a better result.",
            reply_line=[],
            visualizations=[],
        )
    if manager is None or manager.interactive_pool.concurrency < 2:
        raise ValueError("Puzzle explanations require the reserved interactive engine pool")

    baseline = await manager.analyze_hint(before.fen())
    # position_insight validates complete witness lines, not just their first ply.
    original = position_insight(before, baseline)
    if not original.candidates:
        raise ValueError("The engine returned no legal source continuation")
    branch = await manager.analyze_hint(after.fen())
    insight = position_insight(after, branch)
    if not insight.candidates:
        raise ValueError("The engine returned no legal reply continuation")
    reply = insight.candidates[0]
    loss = review_loss(
        win_probability(baseline.raw_result[0]), win_probability(branch.raw_result[0])
    )
    band = review_band(loss)
    if band == "sound":
        verdict: Literal["mistake", "alternative", "uncertain"] = "alternative"
        summary = (
            "The bounded engine comparison finds this move within the sound-move tolerance. "
            "It is an alternative, even if the puzzle's source line expects another move."
        )
    elif band in ("mistake", "blunder"):
        verdict = "mistake"
        summary = (
            "The bounded engine comparison finds a significant loss compared with the best "
            "continuation. The shown reply is a verified legal engine line."
        )
        # These are observations proved on this exact reply line, not generated
        # claims that an off-source move must lose material or allow forced mate.
        reason = next((reason for reason in reply.reasons if reason.tier == "tactical"), None)
        if reason is not None:
            summary += " " + reason.description
    else:
        verdict = "uncertain"
        summary = (
            "This bounded search does not establish a decisive mistake. "
            "The shown reply is a legal continuation, not a proven refutation."
        )
    return PuzzleMoveFeedback(
        verdict=verdict,
        summary=summary,
        reply_line=reply.line,
        visualizations=reply.visualizations,
    )

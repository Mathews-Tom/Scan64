"""Explain the Analysis Board's engine lines for any position (issue #368).

The search is the free-analysis search: the same interactive pool, hint
admission, deadline and cache as ``POST /v1/analysis/position``, so with the
default budget the explained lines are the lines the board already shows.
``nodes`` and ``depth`` may only narrow that budget; ``multipv`` selects how many
of the engine's ranked lines are explained.
"""

from __future__ import annotations

from collections.abc import Sequence

import chess
import chess.engine
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from scan64.api.auth import require_authenticated_player
from scan64.api.insights import (
    FREE_ANALYSIS_LINES,
    PositionAnalysisMove,
    free_analysis_board,
    free_analysis_manager,
    position_analysis_line,
)
from scan64.explanations.assembly import TemplateFallbackReason
from scan64.explanations.board_lines import (
    BoardLinesExplanation,
    BoardLinesFacts,
    ExplanationSource,
    ScoredLine,
    build_board_lines_facts,
    claim_line_id,
    resolve_board_lines_explanation,
    scored_legal_lines,
)
from scan64.explanations.settings import (
    local_explanation_owner_generation,
    read_local_explanation_provider,
)
from scan64.persistence.database import get_session

router = APIRouter(tags=["insights"])

# Depth only narrows a search: the hint node and time budget and its deadline
# still bound it. Stockfish's own maximum search depth is 245.
MAX_EXPLANATION_DEPTH = 245


class AnalysisExplanationRequest(BaseModel):
    player_id: str
    fen: str
    multipv: int | None = Field(
        default=None,
        ge=1,
        description="Engine lines to explain; default 3, at most the hint MultiPV.",
    )
    nodes: int | None = Field(
        default=None, ge=1, description="Node budget; at most the hint node budget."
    )
    depth: int | None = Field(default=None, ge=1, le=MAX_EXPLANATION_DEPTH)


class AnalysisExplanationLine(BaseModel):
    line_id: str
    rank: int
    score_cp: int | None = Field(description="White's point of view.")
    mate: int | None = Field(description="White's point of view.")
    depth: int | None
    seldepth: int | None
    moves: list[PositionAnalysisMove]


class AnalysisExplanationClaim(BaseModel):
    text: str
    evidence_ref: str
    line_id: str
    moves: list[str] = Field(description="UCI prefix of the linked line the claim cites.")


class AnalysisExplanationRead(BaseModel):
    fen: str
    source: ExplanationSource
    fallback_reason: TemplateFallbackReason | None
    text: str
    lines: list[AnalysisExplanationLine]
    claims: list[AnalysisExplanationClaim]
    facts: BoardLinesFacts


def _requested_lines(body: AnalysisExplanationRequest, budget_multipv: int) -> int:
    count = body.multipv if body.multipv is not None else min(FREE_ANALYSIS_LINES, budget_multipv)
    if count > budget_multipv:
        raise HTTPException(
            status_code=422, detail=f"multipv exceeds the {budget_multipv}-line analysis limit"
        )
    return count


def _check_nodes(nodes: int | None, budget_nodes: int | None) -> None:
    if nodes is not None and budget_nodes is not None and nodes > budget_nodes:
        raise HTTPException(
            status_code=422, detail=f"nodes exceeds the {budget_nodes}-node analysis limit"
        )


@router.post("/v1/analysis/position/explanation", response_model=AnalysisExplanationRead)
async def explain_position(
    body: AnalysisExplanationRequest, request: Request, session: Session = Depends(get_session)
) -> AnalysisExplanationRead:
    player_id = require_authenticated_player(request, session)
    if player_id != body.player_id:
        raise HTTPException(status_code=404, detail="Player not found")
    board = free_analysis_board(body.fen)
    if board.is_game_over():
        raise HTTPException(
            status_code=409, detail="Position is game over; there are no engine lines to explain"
        )
    manager = free_analysis_manager(request)
    budget_nodes, _, budget_multipv = manager.hint_limits()
    count = _requested_lines(body, budget_multipv)
    _check_nodes(body.nodes, budget_nodes)
    try:
        analysis = await manager.analyze_hint(board, nodes=body.nodes, depth=body.depth)
    except TimeoutError:
        raise HTTPException(
            status_code=503, detail="Analysis capacity is busy; try again shortly"
        ) from None
    except (ValueError, chess.engine.EngineError, OSError):
        raise HTTPException(
            status_code=503,
            detail="The analysis engine could not analyze this position; try again shortly",
        ) from None
    lines = scored_legal_lines(board, analysis)[:count]
    if not lines:
        manager.invalidate_hint(board, nodes=body.nodes, depth=body.depth)
        raise HTTPException(
            status_code=503,
            detail="The analysis engine could not analyze this position; try again shortly",
        )

    facts = build_board_lines_facts(board, lines)
    owner_generation = session.exec(select(local_explanation_owner_generation(player_id))).one()
    local_provider = await read_local_explanation_provider(session, player_id, owner_generation)
    resolved = await resolve_board_lines_explanation(facts, local_provider=local_provider)

    return explanation_read(board, lines, facts, resolved)


def explanation_read(
    board: chess.Board,
    lines: Sequence[ScoredLine],
    facts: BoardLinesFacts,
    resolved: BoardLinesExplanation,
) -> AnalysisExplanationRead:
    """The response: White-view lines as the board shows them, and line-linked claims."""
    read_lines = []
    for line, line_facts in zip(lines, facts.lines, strict=True):
        white_view = position_analysis_line(board, line)
        read_lines.append(
            AnalysisExplanationLine(
                line_id=line_facts.line_id,
                rank=line_facts.rank,
                score_cp=white_view.score_cp,
                mate=white_view.mate,
                depth=line.depth,
                seldepth=line.seldepth,
                moves=white_view.moves,
            )
        )
    claims = [
        AnalysisExplanationClaim(
            text=claim.text,
            evidence_ref=claim.evidence_ref,
            line_id=claim_line_id(claim, facts),
            moves=list(claim.line),
        )
        for claim in resolved.explanation.claims
    ]
    return AnalysisExplanationRead(
        fen=board.fen(),
        source=resolved.source,
        fallback_reason=resolved.fallback_reason,
        text=resolved.explanation.text,
        lines=read_lines,
        claims=claims,
        facts=facts,
    )


__all__ = [
    "AnalysisExplanationRead",
    "AnalysisExplanationRequest",
    "explanation_read",
    "router",
]

"""Explain the Analysis Board's engine lines for any position (issue #368).

The search is the free-analysis search: the same interactive pool, hint
admission, deadline and cache as ``POST /v1/analysis/position``, so with the
default budget the explained lines are the lines the board already shows.
``nodes`` and ``depth`` may only narrow that budget; ``multipv`` selects how many
of the engine's ranked lines are explained. Within the same request deadline,
each position along the explained lines (plies 2 to 8) is scored with a small
search; plies the deadline does not reach stay ``unknown``.

The latest explanation request from a player wins: it cancels that player's
running model call, and an older request that is still in its engine search
returns 409 without starting one. So each player has at most one model call
running, and leaving a position never waits for the abandoned call to end. This
does not depend on disconnect detection, which ``Request.is_disconnected``
cannot give behind ``IdempotencyMiddleware`` (a ``BaseHTTPMiddleware``). The
engine search is not cancelled; the hint deadline bounds it. The request
releases its DB session before the engine search and the model call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

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
from scan64.chess.variants import STANDARD, VariantName
from scan64.explanations.assembly import TemplateFallbackReason
from scan64.explanations.board_lines import (
    BoardLinesExplanation,
    BoardLinesFacts,
    ExplanationSource,
    ScoredLine,
    board_line_positions,
    build_board_lines_facts,
    claim_line_id,
    line_scores,
    resolve_board_lines_explanation,
    scored_legal_lines,
)
from scan64.explanations.settings import (
    local_explanation_owner_generation,
    read_local_explanation_provider,
)
from scan64.persistence.database import get_session
from scan64.providers.stockfish.pool import HINT_DEADLINE_S, EnginePoolManager

router = APIRouter(tags=["insights"])

# Depth only narrows a search: the hint node and time budget and its deadline
# still bound it. Stockfish's own maximum search depth is 245.
MAX_EXPLANATION_DEPTH = 245

SUPERSEDED_DETAIL = "A newer explanation request from this player replaced this one"


def _superseded() -> HTTPException:
    return HTTPException(status_code=409, detail=SUPERSEDED_DETAIL)


class ExplanationCalls:
    """Each player's model call; the player's latest explanation request wins.

    In-process state on ``app.state``. ``claim`` makes a request the player's
    latest and cancels the model call the player has running. ``run`` starts a
    model call only for the latest request, so an older request still in its
    engine search never starts one.
    """

    def __init__(self) -> None:
        self._latest: dict[str, object] = {}
        self._running: dict[str, asyncio.Task[Any]] = {}

    def running(self, player_id: str) -> bool:
        return player_id in self._running

    @contextmanager
    def claim(self, player_id: str) -> Iterator[object]:
        ticket = object()
        self._latest[player_id] = ticket
        running = self._running.pop(player_id, None)
        if running is not None:
            running.cancel()
        try:
            yield ticket
        finally:
            if self._latest.get(player_id) is ticket:
                del self._latest[player_id]

    async def run[T](self, player_id: str, ticket: object, work: Coroutine[Any, Any, T]) -> T:
        """Run ``work`` as the player's model call; 409 once a newer request claims."""
        if self._latest.get(player_id) is not ticket:
            work.close()
            raise _superseded()
        task = asyncio.ensure_future(work)
        self._running[player_id] = task
        try:
            return await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            # Only ``claim`` cancels the call alone; a cancelled request re-raises.
            if task.cancelled() and current is not None and current.cancelling() == 0:
                raise _superseded() from None
            raise
        finally:
            if self._running.get(player_id) is task:
                del self._running[player_id]


def explanation_calls(request: Request) -> ExplanationCalls:
    calls: ExplanationCalls | None = getattr(request.app.state, "analysis_explanation_calls", None)
    if calls is None:
        calls = ExplanationCalls()
        request.app.state.analysis_explanation_calls = calls
    return calls


class AnalysisExplanationRequest(BaseModel):
    player_id: str
    fen: str
    # Explanations read standard-chess factor facts; any other variant is
    # refused rather than explained under standard rules.
    variant: VariantName = STANDARD
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
    if body.variant != STANDARD:
        raise HTTPException(
            status_code=422,
            detail=f"Explanations cover standard chess only, not {body.variant}",
        )
    board = free_analysis_board(body.fen)
    if board.is_game_over():
        raise HTTPException(
            status_code=409, detail="Position is game over; there are no engine lines to explain"
        )
    manager = free_analysis_manager(request)
    budget_nodes, _, budget_multipv = manager.hint_limits()
    count = _requested_lines(body, budget_multipv)
    _check_nodes(body.nodes, budget_nodes)
    calls = explanation_calls(request)
    with calls.claim(player_id) as ticket:
        owner_generation = session.exec(select(local_explanation_owner_generation(player_id))).one()
        # The provider re-reads its grant on its own connection, so the
        # request session is not needed after this point.
        local_provider = await read_local_explanation_provider(session, player_id, owner_generation)
        session.close()
        lines, facts = await _explained_lines(manager, board, body, count)
        resolved = await calls.run(
            player_id, ticket, resolve_board_lines_explanation(facts, local_provider=local_provider)
        )
    return explanation_read(board, lines, facts, resolved)


async def _explained_lines(
    manager: EnginePoolManager,
    board: chess.Board,
    body: AnalysisExplanationRequest,
    count: int,
) -> tuple[list[ScoredLine], BoardLinesFacts]:
    # One request deadline covers the line search and the per-move scoring.
    deadline = asyncio.get_running_loop().time() + HINT_DEADLINE_S
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

    positions = board_line_positions(board, lines)
    scored = await manager.analyze_hint_positions(positions, deadline=deadline)
    return lines, build_board_lines_facts(board, lines, line_scores(positions, scored))


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
    "ExplanationCalls",
    "explanation_calls",
    "explanation_read",
    "router",
]

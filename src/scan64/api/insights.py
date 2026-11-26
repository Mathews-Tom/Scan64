"""On-demand hints and free analysis share reserved interactive capacity."""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

import chess
import chess.engine
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, model_validator
from sqlmodel import Session

from scan64.api.auth import require_authenticated_player
from scan64.api.play_position import owned_play_position
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.games.assistance import (
    PlaySnapshot,
    StalePlaySnapshot,
    persist_assistance,
)
from scan64.explanations.board_lines import ScoredLine, scored_legal_lines
from scan64.learning.insights import InsightStep, insight_step, position_insight
from scan64.persistence.database import get_session
from scan64.providers.stockfish.pool import EnginePoolManager

router = APIRouter(tags=["insights"])

# Engine lines the Analysis Board shows and, by default, explains.
FREE_ANALYSIS_LINES = 3


class PositionInsightRequest(BaseModel):
    player_id: str
    step: Literal[1, 2, 3]
    play_session_id: UUID | None = None
    fen: str | None = None

    @model_validator(mode="after")
    def one_source(self) -> Self:
        if (self.play_session_id is None) == (self.fen is None):
            raise ValueError("Supply exactly one of play_session_id or fen")
        return self


def _position(
    body: PositionInsightRequest, session: Session
) -> tuple[chess.Board, PlaySnapshot | None]:
    if body.play_session_id is not None:
        return owned_play_position(session, body.play_session_id, body.player_id)
    try:
        board = chess.Board(body.fen)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid FEN") from None
    if not board.is_valid():
        raise HTTPException(status_code=422, detail="Invalid chess position")
    if board.is_game_over():
        raise HTTPException(status_code=409, detail="Position is game over")
    return board, None


@router.post("/v1/insights/position", response_model=InsightStep)
async def insight_position(
    body: PositionInsightRequest, request: Request, session: Session = Depends(get_session)
) -> InsightStep:
    player_id = require_authenticated_player(request, session)
    if player_id != body.player_id:
        raise HTTPException(status_code=404, detail="Player not found")
    board, snapshot = _position(body, session)
    manager: EnginePoolManager | None = getattr(request.app.state, "engine_pool_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Hints require the interactive engine pool")

    if manager.interactive_pool.concurrency < 2:
        raise HTTPException(
            status_code=503, detail="Hints require at least two interactive engines"
        )
    try:
        analysis = await manager.analyze_hint(board)
        response = insight_step(board, position_insight(board, analysis), body.step)
        if snapshot is not None:
            persist_assistance(session, snapshot, board, body.step)
        return response
    except StalePlaySnapshot:
        raise HTTPException(
            status_code=409, detail="PlaySession changed; request a hint for the current position"
        ) from None
    except TimeoutError:
        raise HTTPException(
            status_code=503, detail="Hint capacity is busy; try again shortly"
        ) from None
    except (ValueError, chess.engine.EngineError, OSError):
        raise HTTPException(
            status_code=503, detail="The hint engine could not analyze this position"
        ) from None


class PositionAnalysisRequest(BaseModel):
    player_id: str
    fen: str


class PositionAnalysisMove(BaseModel):
    uci: str
    san: str


class PositionAnalysisLine(BaseModel):
    score_cp: int | None
    mate: int | None
    moves: list[PositionAnalysisMove]


class PositionAnalysisRead(BaseModel):
    fen: str
    lines: list[PositionAnalysisLine]


def _analysis_lines(board: chess.Board, analysis: EngineAnalysis) -> list[PositionAnalysisLine]:
    """Validate complete PVs, retaining engine rank and converting mover scores to White."""
    lines = [
        position_analysis_line(board, line)
        for line in scored_legal_lines(board, analysis)[:FREE_ANALYSIS_LINES]
    ]
    if not lines:
        raise ValueError("The engine returned no usable continuations")
    return lines


def position_analysis_line(board: chess.Board, line: ScoredLine) -> PositionAnalysisLine:
    """One engine line in the free-analysis shape: White-view scores and contextual SAN."""
    sign = 1 if board.turn == chess.WHITE else -1
    replay = board.copy(stack=False)
    moves: list[PositionAnalysisMove] = []
    for move in line.moves:
        moves.append(PositionAnalysisMove(uci=move.uci(), san=replay.san(move)))
        replay.push(move)
    return PositionAnalysisLine(
        score_cp=line.score_cp * sign if line.score_cp is not None else None,
        mate=line.score_mate * sign if line.score_mate is not None else None,
        moves=moves,
    )


def free_analysis_board(fen: str) -> chess.Board:
    """Parse a free-analysis FEN; 422 before any engine access when it is not a position."""
    try:
        board = chess.Board(fen)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid FEN") from None
    if not board.is_valid():
        raise HTTPException(status_code=422, detail="Invalid chess position")
    return board


def free_analysis_manager(request: Request) -> EnginePoolManager:
    """The interactive pool free analysis is admitted to, or 503 when it cannot admit."""
    manager: EnginePoolManager | None = getattr(request.app.state, "engine_pool_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail="Analysis requires the interactive engine pool; try again when available",
        )
    if manager.interactive_pool.concurrency < 2:
        raise HTTPException(
            status_code=503,
            detail="Analysis requires at least two interactive engines; try again when available",
        )
    return manager


@router.post("/v1/analysis/position", response_model=PositionAnalysisRead)
async def analyze_position(
    body: PositionAnalysisRequest, request: Request, session: Session = Depends(get_session)
) -> PositionAnalysisRead:
    player_id = require_authenticated_player(request, session)
    if player_id != body.player_id:
        raise HTTPException(status_code=404, detail="Player not found")
    board = free_analysis_board(body.fen)
    fen = board.fen()
    if board.is_game_over():
        return PositionAnalysisRead(fen=fen, lines=[])
    manager = free_analysis_manager(request)
    try:
        analysis = await manager.analyze_hint(board)
        try:
            lines = _analysis_lines(board, analysis)
        except ValueError:
            # Hint consumers can use output that lacks a complete legal PV.
            # Evict only when free-analysis conversion rejects it, so Retry searches again.
            manager.invalidate_hint(board)
            raise
        return PositionAnalysisRead(fen=fen, lines=lines)
    except TimeoutError:
        raise HTTPException(
            status_code=503, detail="Analysis capacity is busy; try again shortly"
        ) from None
    except (ValueError, chess.engine.EngineError, OSError):
        raise HTTPException(
            status_code=503,
            detail="The analysis engine could not analyze this position; try again shortly",
        ) from None

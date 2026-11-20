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
from scan64.learning.insights import InsightStep, insight_step, position_insight
from scan64.persistence.database import get_session
from scan64.providers.stockfish.pool import EnginePoolManager

router = APIRouter(tags=["insights"])


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
    lines: list[PositionAnalysisLine] = []
    sign = 1 if board.turn == chess.WHITE else -1
    for result in analysis.raw_result:
        cp = result.get("score_cp")
        mate = result.get("score_mate")
        if not ((type(cp) is int and mate is None) or (cp is None and type(mate) is int)):
            continue
        pv = result.get("pv")
        if not isinstance(pv, list) or not pv:
            continue
        continuation = board.copy(stack=False)
        moves: list[PositionAnalysisMove] = []
        for uci in pv:
            if not isinstance(uci, str):
                break
            try:
                move = continuation.parse_uci(uci)
            except ValueError:
                break
            # parse_uci accepts the null move, which is not a legal continuation.
            if move not in continuation.legal_moves:
                break
            moves.append(PositionAnalysisMove(uci=move.uci(), san=continuation.san(move)))
            continuation.push(move)
        else:
            lines.append(
                PositionAnalysisLine(
                    score_cp=cp * sign if cp is not None else None,
                    mate=mate * sign if mate is not None else None,
                    moves=moves,
                )
            )
            if len(lines) == 3:
                break
    if not lines:
        raise ValueError("The engine returned no usable continuations")
    return lines


@router.post("/v1/analysis/position", response_model=PositionAnalysisRead)
async def analyze_position(
    body: PositionAnalysisRequest, request: Request, session: Session = Depends(get_session)
) -> PositionAnalysisRead:
    player_id = require_authenticated_player(request, session)
    if player_id != body.player_id:
        raise HTTPException(status_code=404, detail="Player not found")
    try:
        board = chess.Board(body.fen)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid FEN") from None
    if not board.is_valid():
        raise HTTPException(status_code=422, detail="Invalid chess position")
    fen = board.fen()
    if board.is_game_over():
        return PositionAnalysisRead(fen=fen, lines=[])
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

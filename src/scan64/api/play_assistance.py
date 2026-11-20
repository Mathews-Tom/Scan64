"""Owned, snapshot-checked Candidates and opt-in blunder-guard disclosures."""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

import chess
import chess.engine
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlmodel import Session

from scan64.api.auth import require_authenticated_player
from scan64.api.play_position import owned_play_position
from scan64.chess.games.assistance import (
    AssistanceMode,
    StalePlaySnapshot,
    assistance_modes,
    persist_assistance,
)
from scan64.learning.play_assistance import PlayMoveGrade, grade_play_moves
from scan64.persistence.database import get_session
from scan64.providers.stockfish.pool import EnginePoolManager

router = APIRouter(tags=["play-sessions"])


class PlayAssistanceRequest(BaseModel):
    player_id: str
    fen: str
    moves: list[str] = Field(min_length=1, max_length=3)
    mode: Literal["candidates", "guard"]

    @model_validator(mode="after")
    def valid_proposals(self) -> Self:
        if len(set(self.moves)) != len(self.moves):
            raise ValueError("Proposed moves must be unique")
        if self.mode == "guard" and len(self.moves) != 1:
            raise ValueError("Guard requires exactly one proposed move")
        return self


class PlayAssistanceRead(BaseModel):
    fen: str
    generation: int
    mode: Literal["candidates", "guard"]
    grades: list[PlayMoveGrade]
    assistance_modes: list[AssistanceMode]


@router.post("/v1/play-sessions/{session_id}/assistance", response_model=PlayAssistanceRead)
async def assist_play_moves(
    session_id: UUID,
    body: PlayAssistanceRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> PlayAssistanceRead:
    actor = require_authenticated_player(request, session)
    if actor != body.player_id:
        raise HTTPException(status_code=404, detail="Player not found")
    board, snapshot = owned_play_position(session, session_id, body.player_id)
    try:
        if len(body.fen.split()) != 6:
            raise ValueError("Supply a complete FEN")
        supplied = chess.Board(body.fen)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid FEN") from None
    if not supplied.is_valid():
        raise HTTPException(status_code=422, detail="Invalid chess position")
    if supplied.fen() != board.fen():
        raise HTTPException(status_code=409, detail="PlaySession position changed")
    moves: list[chess.Move] = []
    try:
        for uci in body.moves:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                raise ValueError("Illegal proposed move")
            moves.append(move)
    except ValueError:
        raise HTTPException(status_code=422, detail="Proposed moves must be legal UCI") from None
    manager: EnginePoolManager | None = getattr(request.app.state, "engine_pool_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503, detail="Play assistance requires the interactive engine pool"
        )
    try:
        grades = await grade_play_moves(board, moves, manager)
        generation = persist_assistance(session, snapshot, board, 0, mode=body.mode)
    except StalePlaySnapshot:
        raise HTTPException(
            status_code=409, detail="PlaySession changed; check the current position again"
        ) from None
    except TimeoutError:
        raise HTTPException(
            status_code=503, detail="Play assistance capacity is busy; try again shortly"
        ) from None
    except (ValueError, chess.engine.EngineError, OSError):
        raise HTTPException(
            status_code=503, detail="The assistance engine could not grade these moves"
        ) from None
    return PlayAssistanceRead(
        fen=board.fen(),
        generation=generation,
        mode=body.mode,
        grades=grades,
        assistance_modes=assistance_modes(session, session_id),
    )

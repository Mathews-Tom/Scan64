"""Position health meters for the Analysis Board and game review (#416).

Computed by the factor module alone: no engine and no model call.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from scan64.api.auth import require_authenticated_player
from scan64.api.insights import free_analysis_board
from scan64.learning.position_health import Level, MeterReading, Trend, position_health
from scan64.persistence.database import get_session

router = APIRouter()


class PositionHealthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fen: str
    learner: Literal["white", "black"]
    # The position before the last move; it gives each meter's trend.
    previous_fen: str | None = None


class TermRead(BaseModel):
    name: str
    pawns: float


class MeterRead(BaseModel):
    health: float
    level: Level
    trend: Trend
    terms: list[TermRead]

    @classmethod
    def from_reading(cls, reading: MeterReading) -> MeterRead:
        return cls(
            health=reading.health,
            level=reading.level,
            trend=reading.trend,
            terms=[TermRead(name=t.name, pawns=t.pawns) for t in reading.terms],
        )


class GroupHealthRead(BaseModel):
    key: str
    name: str
    learner: MeterRead
    opponent: MeterRead


class PositionHealthRead(BaseModel):
    fen: str
    learner: Literal["white", "black"]
    groups: list[GroupHealthRead]


@router.post("/v1/analysis/position/health", response_model=PositionHealthRead)
def read_position_health(
    body: PositionHealthRequest, request: Request, session: Session = Depends(get_session)
) -> PositionHealthRead:
    require_authenticated_player(request, session)
    board = free_analysis_board(body.fen)
    previous = None if body.previous_fen is None else free_analysis_board(body.previous_fen)
    readings = position_health(board, body.learner, previous)
    return PositionHealthRead(
        fen=board.fen(),
        learner=body.learner,
        groups=[
            GroupHealthRead(
                key=g.key,
                name=g.name,
                learner=MeterRead.from_reading(g.learner),
                opponent=MeterRead.from_reading(g.opponent),
            )
            for g in readings
        ],
    )

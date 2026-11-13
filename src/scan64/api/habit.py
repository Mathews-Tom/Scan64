"""Owned habit projections and explicit writes, without reward replay on reads."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlmodel import Session, col

from scan64.api.auth import require_authenticated_player
from scan64.api.models import Player
from scan64.learning.habit import (
    edit_goal,
    ensure_policy,
    habit_summary,
    start_target,
    target_invalidity,
)
from scan64.learning.habit_models import PatternTarget
from scan64.persistence.database import get_session

router = APIRouter(prefix="/v1/players/{player_id}/habit", tags=["habit"])


class GoalEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: int = Field(strict=True, ge=1, le=2**63 - 1)


class TargetStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1)


class TargetRefresh(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _owned(request: Request, player_id: str, session: Session, *, lock: bool = False) -> None:
    authenticated = require_authenticated_player(request, session)
    if authenticated != player_id or session.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    if lock:
        # Same owner lock used by outcome transactions: serialize goal/target snapshots.
        session.execute(
            update(Player)
            .where(col(Player.id) == player_id)
            .values(preferences=col(Player.preferences))
        )
        ensure_policy(player_id, session)


@router.get("")
def get_habit(
    player_id: str, request: Request, session: Session = Depends(get_session)
) -> dict[str, Any]:
    _owned(request, player_id, session)
    return habit_summary(player_id, session)


@router.post("/goal")
def set_goal(
    player_id: str, body: GoalEdit, request: Request, session: Session = Depends(get_session)
) -> dict[str, Any]:
    _owned(request, player_id, session, lock=True)
    edit_goal(player_id, body.goal, session)
    session.commit()
    return habit_summary(player_id, session)


@router.post("/targets")
def create_target(
    player_id: str, body: TargetStart, request: Request, session: Session = Depends(get_session)
) -> dict[str, Any]:
    _owned(request, player_id, session, lock=True)
    try:
        start_target(player_id, body.code, session)
    except ValueError as error:
        # A target write persists withdrawals even if this selection is now ineligible.
        session.commit()
        raise HTTPException(status_code=409, detail=str(error)) from error
    session.commit()
    return habit_summary(player_id, session)


@router.post("/targets/{target_id}/refresh")
def refresh_target(
    player_id: str,
    target_id: str,
    body: TargetRefresh,
    request: Request,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _owned(request, player_id, session, lock=True)
    target = session.get(PatternTarget, target_id)
    if target is None or target.player_id != player_id:
        raise HTTPException(status_code=404, detail="Pattern target not found")
    reason = target_invalidity(target, session)
    if reason is not None and target.invalidated_at is None:
        target.invalidated_at = datetime.now(UTC)
        target.invalidation_reason = reason
        session.add(target)
    session.commit()
    return habit_summary(player_id, session)

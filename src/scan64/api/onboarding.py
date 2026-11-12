"""Declaration-based orientation; puzzles do not calibrate playing strength."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, col, select

from scan64.api.auth import require_authenticated_player
from scan64.api.models import Player, PlayerOnboarding
from scan64.content.puzzle_practice_models import PuzzleDailySet, PuzzleDailySlot
from scan64.content.puzzle_runs import PuzzleRun
from scan64.persistence.database import get_session

router = APIRouter(tags=["onboarding"])
DeclaredLevel = Literal["beginner", "developing", "club", "advanced", "not_sure"]
MAPPING_VERSION = "declared-level-v1"
LEVEL_MAPPING = {"beginner": 2, "developing": 3, "club": 5, "advanced": 6, "not_sure": 1}
INTRO_TARGET = 5


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def intro_runs(
    session: Session, onboarding: PlayerOnboarding
) -> list[tuple[PuzzleRun, PuzzleDailySlot]]:
    if onboarding.intro_started_at is None:
        return []
    rows = session.exec(
        select(PuzzleRun, PuzzleDailySlot)
        .join(PuzzleDailySlot, col(PuzzleDailySlot.run_id) == PuzzleRun.id)
        .join(PuzzleDailySet, col(PuzzleDailySet.id) == PuzzleDailySlot.set_id)
        .where(
            PuzzleRun.player_id == onboarding.player_id,
            PuzzleDailySlot.player_id == onboarding.player_id,
            PuzzleDailySet.player_id == onboarding.player_id,
            PuzzleRun.item_id == PuzzleDailySlot.item_id,
            PuzzleRun.started_at >= onboarding.intro_started_at,
        )
        .order_by(col(PuzzleRun.started_at), col(PuzzleRun.id))
        .limit(INTRO_TARGET)
    ).all()
    return [(run, slot) for run, slot in rows]


def manual_bot_level(player: Player) -> int | None:
    value = player.preferences.get("manual_bot_level")
    return value if type(value) is int and 1 <= value <= 8 else None


def onboarding_for(player_id: str, session: Session) -> PlayerOnboarding:
    row = session.get(PlayerOnboarding, player_id)
    if row is None:
        # Also covers imported pre-onboarding archives and programmatic legacy players.
        row = PlayerOnboarding(player_id=player_id, status="not_offered")
        session.add(row)
        session.flush()
    return row


class OnboardingRead(BaseModel):
    player_id: str
    status: str
    declared_level: str | None
    suggested_bot_level: int | None
    mapping_version: str | None
    intro_started_at: datetime | None
    skipped_at: datetime | None
    completed_at: datetime | None
    intro_started: int
    intro_closed: int
    intro_target: int = INTRO_TARGET
    preferred_bot_level: int
    manual_bot_level: int | None

    @field_validator("intro_started_at", "skipped_at", "completed_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value is not None else None


def read_onboarding(player: Player, session: Session) -> OnboardingRead:
    row = onboarding_for(player.id, session)
    runs = intro_runs(session, row)
    closed = [slot.closed_at for _, slot in runs if slot.closed_at is not None]
    if len(closed) == INTRO_TARGET and row.status == "in_progress":
        row.status = "completed"
        row.completed_at = max(closed, key=utc)
        session.add(row)
    manual = manual_bot_level(player)
    return OnboardingRead(
        **row.model_dump(),
        intro_started=len(runs),
        intro_closed=len(closed),
        manual_bot_level=manual,
        preferred_bot_level=manual or row.suggested_bot_level or 1,
    )


def owned_player(request: Request, player_id: str, session: Session) -> Player:
    authenticated = require_authenticated_player(request, session)
    player = session.get(Player, player_id) if authenticated == player_id else None
    if player is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


class OnboardingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["declare", "skip", "continue_later"]
    declared_level: DeclaredLevel | None = None


class BotChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_level: int = Field(strict=True, ge=1, le=8)


@router.get("/v1/players/{player_id}/onboarding", response_model=OnboardingRead)
def get_onboarding(
    player_id: str, request: Request, session: Session = Depends(get_session)
) -> OnboardingRead:
    result = read_onboarding(owned_player(request, player_id, session), session)
    session.commit()
    return result


@router.post("/v1/players/{player_id}/onboarding", response_model=OnboardingRead)
def update_onboarding(
    player_id: str,
    update: OnboardingUpdate,
    request: Request,
    session: Session = Depends(get_session),
) -> OnboardingRead:
    player = owned_player(request, player_id, session)
    row = onboarding_for(player_id, session)
    if update.action == "declare":
        if update.declared_level is None:
            raise HTTPException(status_code=422, detail="A declared level is required")
        row.declared_level = update.declared_level
        row.suggested_bot_level = LEVEL_MAPPING[update.declared_level]
        row.mapping_version = MAPPING_VERSION
        row.intro_started_at = row.intro_started_at or datetime.now(UTC)
        row.status = "completed" if row.completed_at is not None else "in_progress"
        row.skipped_at = None
    elif update.declared_level is not None:
        raise HTTPException(status_code=422, detail="Only declare accepts a level")
    elif update.action == "skip":
        row.status = "skipped"
        row.skipped_at = datetime.now(UTC)
    session.add(row)
    result = read_onboarding(player, session)
    session.commit()
    return result


@router.put("/v1/players/{player_id}/bot-choice", response_model=OnboardingRead)
def set_bot_choice(
    player_id: str,
    choice: BotChoice,
    request: Request,
    session: Session = Depends(get_session),
) -> OnboardingRead:
    player = owned_player(request, player_id, session)
    player.preferences = {**player.preferences, "manual_bot_level": choice.bot_level}
    session.add(player)
    result = read_onboarding(player, session)
    session.commit()
    return result

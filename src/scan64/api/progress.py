"""Owner-confirmed learning reset; saved content and account data are untouched."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlmodel import Session, SQLModel, col, delete, select

from scan64.api.middleware import LEARNING_CACHE_PREFIXES, IdempotencyRecord
from scan64.api.models import PlayerProfile
from scan64.api.onboarding import owned_player
from scan64.content.models import ContentAttempt, LessonAttempt, StudySession
from scan64.content.puzzle_mode_models import PuzzleModeBinding
from scan64.content.puzzle_practice import lock_puzzle_player
from scan64.content.puzzle_practice_models import (
    PuzzleDailySet,
    PuzzleDailySlot,
    PuzzleRating,
    PuzzleRatingEvent,
)
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.evaluation.transfer_measurement import TransferMeasurement
from scan64.learning.habit_models import (
    HabitCredit,
    HabitDay,
    HabitPolicy,
    PatternAward,
    PatternAwardEvidence,
    PatternTarget,
)
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.progress import RESET_AT_KEY, RESET_GENERATION_KEY
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.learning.sparring import SparringAttempt
from scan64.persistence.database import get_session

router = APIRouter(tags=["progress"])

# Child rows precede parents. Historical source artifacts (including positive
# move-credit proofs and hint reveals) intentionally are not learning state.
CLEARED_MODELS: tuple[tuple[str, type[SQLModel]], ...] = (
    ("sparring_attempts", SparringAttempt),
    ("pattern_award_evidence", PatternAwardEvidence),
    ("pattern_awards", PatternAward),
    ("pattern_targets", PatternTarget),
    ("habit_credits", HabitCredit),
    ("habit_days", HabitDay),
    ("puzzle_mode_bindings", PuzzleModeBinding),
    ("puzzle_rating_events", PuzzleRatingEvent),
    ("puzzle_daily_slots", PuzzleDailySlot),
    ("puzzle_daily_sets", PuzzleDailySet),
    ("puzzle_rating", PuzzleRating),
    ("content_attempts", ContentAttempt),
    ("lesson_attempts", LessonAttempt),
    ("transfer_measurements", TransferMeasurement),
    ("puzzle_runs", PuzzleRun),
    ("study_sessions", StudySession),
    ("profile_observations", ProfileObservation),
    ("review_schedules", ReviewSchedule),
    ("skill_states", SkillState),
)


class ProgressResetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["RESET PROGRESS"]


class ProgressResetRead(BaseModel):
    player_id: str
    reset_at: datetime
    cleared: dict[str, int]


@router.post("/v1/players/{player_id}/progress/reset", response_model=ProgressResetRead)
def reset_progress(
    player_id: str,
    confirmation: ProgressResetCreate,
    request: Request,
    session: Session = Depends(get_session),
) -> ProgressResetRead:
    owned_player(request, player_id, session)
    try:
        # Same database write fence as puzzle admission and lesson outcomes.
        lock_puzzle_player(player_id, session)
        player = owned_player(request, player_id, session)
        session.refresh(player)
        reset_at = datetime.now(UTC)
        cleared: dict[str, int] = {}
        for name, model in CLEARED_MODELS:
            player_column = col(getattr(model, "player_id"))
            cleared[name] = session.exec(
                select(func.count()).select_from(model).where(player_column == player_id)
            ).one()
            session.exec(delete(model).where(player_column == player_id))

        profile = session.get(PlayerProfile, player_id)
        cleared["profile_rating"] = int(profile is not None)
        if profile is not None:
            profile.rating = PlayerProfile(player_id=player_id).rating
            session.add(profile)
        # Keep configured goals and their effective dates, but begin a fresh
        # prospective evidence window for habits and pattern awards.
        policy = session.get(HabitPolicy, player_id)
        if policy is not None:
            policy.effective_at = reset_at
            session.add(policy)
        player.preferences = {
            **player.preferences,
            RESET_AT_KEY: reset_at.isoformat(),
            RESET_GENERATION_KEY: str(uuid4()),
        }
        session.add(player)

        # Do not replay successful starts/attempts whose run was just removed.
        # Leave game-move and settings idempotency records alone.
        principal = hashlib.sha256(request.headers["Authorization"].encode()).hexdigest()
        for path in LEARNING_CACHE_PREFIXES:
            session.exec(
                delete(IdempotencyRecord).where(
                    col(IdempotencyRecord.idempotency_key).contains(f":{principal}:"),
                    col(IdempotencyRecord.idempotency_key).contains(f":{path}"),
                )
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    request.state.skip_idempotency_cache = True
    return ProgressResetRead(player_id=player_id, reset_at=reset_at, cleared=cleared)

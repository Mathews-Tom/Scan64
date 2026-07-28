from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import chess
from chess_lesson_spec import LessonSpec
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlmodel import Session, select

from scan64.api.auth import require_authenticated_player, require_player_token
from scan64.api.models import PlayerProfile
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.content.models import LessonAttempt, StudySession
from scan64.learning.profiling.profile_update import apply_lesson_attempt
from scan64.learning.scheduling.composer import SessionComposer
from scan64.learning.scheduling.priority import PriorityFactors
from scan64.learning.scheduling.session_state import load_player_session_state
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import get_session

router = APIRouter(prefix="/v1/learning", tags=["learning"])


class TrainingSessionRead(BaseModel):
    session_id: str
    lessons: list[LessonSpec]


class LessonAttemptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    lesson_id: str
    source_kind: Literal["persisted_opportunity", "opening_mission"]
    submitted_move: str | None = None
    elapsed_ms: int = Field(ge=0)
    hints_used: int = Field(ge=0)


class LessonAttemptRead(BaseModel):
    id: str
    success: bool | None
    grading_status: str
    profile_update_result: str



@router.get("/session", response_model=TrainingSessionRead)
def get_training_session(
    player_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> TrainingSessionRead:
    now = datetime.now(UTC)
    require_player_token(request, player_id, db)
    state = load_player_session_state(db, player_id, now)
    pool: list[dict[str, Any]] = []
    opportunities = db.exec(
        select(PersistedLessonOpportunity).where(
            PersistedLessonOpportunity.player_id == player_id
        )
    ).all()
    for opportunity in opportunities:
        schedule = state.active_reviews.get(str(opportunity.id))
        if schedule is None:
            continue
        spec = LessonSpec.model_validate(opportunity.lesson_spec)
        spec.lesson_id = str(opportunity.id)
        is_due = schedule.is_due(now)
        priority = PriorityFactors(
            review_due=1.0 if is_due else 0.0,
            weakness_severity=0.8,
        ).compute_priority(session_fatigue=0.0)
        pool.append({
            "id": str(opportunity.id),
            "type": "due" if is_due else "mistakes",
            "priority": priority,
            "spec": spec,
        })

    composed_session = SessionComposer().compose_session(pool, session_size=5)
    study_session = StudySession(player_id=player_id, domain="daily_training")
    db.add(study_session)
    db.commit()
    return TrainingSessionRead(
        session_id=study_session.id,
        lessons=[item["spec"] for item in composed_session],
    )


def _submitted_move_is_accepted(spec: LessonSpec, submitted_move: str) -> bool:
    board = chess.Board(spec.source.fen)
    try:
        move = chess.Move.from_uci(submitted_move)
    except ValueError:
        return False
    if move not in board.legal_moves:
        return False
    san = board.san(move)
    return any(accepted_move.san == san for accepted_move in spec.interaction.accepted_moves)


@router.post("/lesson-attempts", response_model=LessonAttemptRead)
def record_lesson_attempt(
    attempt_in: LessonAttemptCreate,
    request: Request,
    db: Session = Depends(get_session),
) -> LessonAttemptRead:
    authenticated_player_id = require_authenticated_player(request, db)
    study_session = db.get(StudySession, attempt_in.session_id)
    if study_session is None or study_session.player_id != authenticated_player_id:
        raise HTTPException(status_code=404, detail="Study session not found")
    if attempt_in.source_kind == "opening_mission":
        attempt = LessonAttempt(
            session_id=study_session.id,
            player_id=study_session.player_id,
            lesson_id=attempt_in.lesson_id,
            source_kind=attempt_in.source_kind,
            submitted_move=attempt_in.submitted_move,
            elapsed_ms=attempt_in.elapsed_ms,
            hints_used=attempt_in.hints_used,
            grading_status="ungraded",
            profile_update_result="not_applicable",
        )
        db.add(attempt)
        db.commit()
        db.refresh(attempt)
        return LessonAttemptRead(
            id=attempt.id,
            success=attempt.success,
            grading_status=attempt.grading_status,
            profile_update_result=attempt.profile_update_result,
        )

    try:
        opportunity_id = UUID(attempt_in.lesson_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Persisted lesson id must be a UUID") from error
    opportunity = db.get(PersistedLessonOpportunity, opportunity_id)
    if opportunity is None or opportunity.player_id != study_session.player_id:
        raise HTTPException(status_code=404, detail="Persisted lesson opportunity not found")
    schedule = db.get(ReviewSchedule, (study_session.player_id, str(opportunity.id)))
    if schedule is None:
        raise HTTPException(status_code=409, detail="Persisted lesson has no review schedule")
    if attempt_in.submitted_move is None:
        raise HTTPException(status_code=422, detail="Persisted lesson attempts require a move")

    spec = LessonSpec.model_validate(opportunity.lesson_spec)
    attempt_count = db.exec(
        select(func.count())
        .where(LessonAttempt.player_id == study_session.player_id)
        .where(LessonAttempt.opportunity_id == opportunity.id)
    ).one()
    if attempt_count >= spec.interaction.maximum_attempts:
        raise HTTPException(status_code=409, detail="Maximum attempts reached for persisted lesson")
    success = _submitted_move_is_accepted(spec, attempt_in.submitted_move)
    observed_at = datetime.now(UTC)
    profile_update_result = (
        "skipped_retired" if schedule.retired_at is not None else "skipped_no_skill"
    )
    if schedule.retired_at is None and schedule.skill_id is not None:
        profile = db.get(PlayerProfile, study_session.player_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Player profile not found")
        profile_update_result = apply_lesson_attempt(
            session=db,
            player_id=study_session.player_id,
            skill_id=schedule.skill_id,
            success=success,
            hint_assisted=attempt_in.hints_used > 0,
            rating=profile.rating,
            observed_at=observed_at,
        )
    if schedule.retired_at is None:
        schedule.update(success=success, current_time=observed_at)
        db.add(schedule)
    attempt = LessonAttempt(
        session_id=study_session.id,
        player_id=study_session.player_id,
        lesson_id=str(opportunity.id),
        source_kind=attempt_in.source_kind,
        opportunity_id=opportunity.id,
        submitted_move=attempt_in.submitted_move,
        elapsed_ms=attempt_in.elapsed_ms,
        hints_used=attempt_in.hints_used,
        success=success,
        grading_status="verified",
        profile_update_result=profile_update_result,
        completed_at=observed_at,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return LessonAttemptRead(
        id=attempt.id,
        success=attempt.success,
        grading_status=attempt.grading_status,
        profile_update_result=attempt.profile_update_result,
    )

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import chess
import chess.engine
from chess_lesson_spec import LessonSpec
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func
from sqlmodel import Session, col, select

from scan64.api.auth import require_authenticated_player, require_player_token
from scan64.api.models import PlayerProfile
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.content.models import LessonAttempt, StudySession
from scan64.learning.profiling.profile_update import apply_lesson_attempt
from scan64.learning.scheduling.composer import SessionComposer
from scan64.learning.scheduling.priority import (
    PriorityFactors,
    classify_priority_bucket,
    compute_recent_session_fatigue,
    compute_weakness_severity,
)
from scan64.learning.scheduling.session_state import load_player_session_state
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson
from scan64.persistence.database import get_session
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig

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


def _objective_analysis_for_lesson(
    opportunity: PersistedLessonOpportunity, db: Session
) -> EngineAnalysis:
    analysis = db.exec(
        select(EngineAnalysis)
        .where(EngineAnalysis.position_id == opportunity.source_position_id)
        .order_by(col(EngineAnalysis.created_at).desc(), col(EngineAnalysis.id).desc())
    ).first()
    if analysis is not None:
        return analysis

    try:
        analysis = asyncio.run(
            StockfishAdapter(StockfishConfig()).analyze_position(
                opportunity.lesson_spec["source"]["fen"], nodes=100_000
            )
        )
    except (OSError, chess.engine.EngineError) as error:
        raise LessonVerificationError("Objective engine analysis is unavailable") from error
    analysis.position_id = opportunity.source_position_id
    db.add(analysis)
    return analysis


def _reverify_persisted_lesson(
    opportunity: PersistedLessonOpportunity, db: Session
) -> LessonSpec | None:
    try:
        spec = LessonSpec.model_validate(opportunity.lesson_spec)
        verify_lesson(spec, _objective_analysis_for_lesson(opportunity, db))
    except (KeyError, LessonVerificationError, TypeError, ValidationError) as error:
        opportunity.verification_status = "invalid"
        opportunity.verification_error = str(error)
        return None

    opportunity.verification_status = "verified"
    opportunity.verification_error = None
    opportunity.lesson_spec = spec.model_dump(mode="json")
    return spec


@router.get("/session", response_model=TrainingSessionRead)
def get_training_session(
    player_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> TrainingSessionRead:
    now = datetime.now(UTC)
    require_player_token(request, player_id, db)
    state = load_player_session_state(db, player_id, now)
    attempt_count, error_rate = state.recent_attempt_stats()
    session_fatigue = compute_recent_session_fatigue(attempt_count, error_rate)
    pool: list[dict[str, Any]] = []
    opportunities = db.exec(
        select(PersistedLessonOpportunity).where(PersistedLessonOpportunity.player_id == player_id)
    ).all()
    for opportunity in opportunities:
        schedule = state.active_reviews.get(str(opportunity.id))
        if schedule is None:
            continue
        if opportunity.verification_status == "invalid":
            continue
        spec = _reverify_persisted_lesson(opportunity, db)
        if spec is None:
            continue
        spec.lesson_id = str(opportunity.id)
        is_due = schedule.is_due(now)
        weakness_severity = compute_weakness_severity(state.skill_for(schedule.skill_id))
        priority = PriorityFactors(
            review_due=1.0 if is_due else 0.0,
            weakness_severity=weakness_severity,
        ).compute_priority(session_fatigue=session_fatigue)
        pool.append(
            {
                "id": str(opportunity.id),
                "type": classify_priority_bucket(is_due, weakness_severity),
                "priority": priority,
                "spec": spec,
            }
        )

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
    if opportunity.verification_status == "invalid":
        raise HTTPException(status_code=409, detail="Persisted lesson is not verified")
    if opportunity.verification_status == "verified":
        spec = LessonSpec.model_validate(opportunity.lesson_spec)
    else:
        reverified_spec, _ = _reverify_persisted_lesson(opportunity, db, allow_fallback=True)
        if reverified_spec is None:
            raise HTTPException(status_code=409, detail="Persisted lesson is not verified")
        spec = reverified_spec
    if attempt_in.submitted_move is None:
        raise HTTPException(status_code=422, detail="Persisted lesson attempts require a move")
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

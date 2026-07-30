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
from scan64.chess.positions.models import Position
from scan64.content.models import LessonAttempt, StudySession
from scan64.learning.evaluation.transfer_measurement import (
    PRODUCTION_TRANSFER_LIFECYCLE_PREFIX,
    TransferMeasurement,
    TransferMeasurementReport,
    assign_production_transfer_measurements,
    build_transfer_measurement_report,
    due_transfer_measurements,
    record_transfer_measurement,
)
from scan64.learning.profiling.models import SkillState
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
    source_kind: Literal["persisted_opportunity", "opening_mission", "transfer_measurement"]
    submitted_move: str | None = None
    elapsed_ms: int = Field(ge=0)
    hints_used: int = Field(ge=0)


class LessonAttemptRead(BaseModel):
    id: str
    success: bool | None
    grading_status: str
    profile_update_result: str


class ObjectiveAnalysisUnavailable(Exception):
    pass


def _lesson_board(spec: LessonSpec) -> chess.Board:
    try:
        board = chess.Board(spec.source.fen)
    except ValueError as error:
        raise LessonVerificationError(f"Invalid FEN: {error}") from None
    if not board.is_valid():
        raise LessonVerificationError("FEN does not represent a legal chess position")
    return board


def _objective_analysis_for_lesson(
    opportunity: PersistedLessonOpportunity,
    spec: LessonSpec,
    db: Session,
    *,
    allow_fallback: bool,
) -> tuple[EngineAnalysis, bool]:
    _lesson_board(spec)
    source_position = db.get(Position, opportunity.source_position_id)
    if source_position is None or source_position.fen != spec.source.fen:
        raise LessonVerificationError("Lesson source does not match its persisted position")

    if opportunity.verification_analysis_id is not None:
        analysis = db.get(EngineAnalysis, opportunity.verification_analysis_id)
        if analysis is not None:
            return analysis, False

    existing_analyses = db.exec(
        select(EngineAnalysis)
        .where(EngineAnalysis.position_id == opportunity.source_position_id)
        .order_by(col(EngineAnalysis.created_at).desc(), col(EngineAnalysis.id).desc())
    ).all()
    for analysis in existing_analyses:
        if analysis.raw_result:
            opportunity.verification_analysis_id = analysis.id
            return analysis, False

    if not allow_fallback:
        raise ObjectiveAnalysisUnavailable("Objective engine analysis is deferred")
    try:
        analysis = asyncio.run(
            StockfishAdapter(StockfishConfig()).analyze_position(spec.source.fen, nodes=100_000)
        )
    except (OSError, chess.engine.EngineError) as error:
        raise ObjectiveAnalysisUnavailable("Objective engine analysis is unavailable") from error
    analysis.position_id = opportunity.source_position_id
    db.add(analysis)
    db.flush()
    opportunity.verification_analysis_id = analysis.id
    return analysis, True


def _reverify_persisted_lesson(
    opportunity: PersistedLessonOpportunity, db: Session, *, allow_fallback: bool
) -> tuple[LessonSpec | None, bool]:
    try:
        spec = LessonSpec.model_validate(opportunity.lesson_spec)
        analysis, used_fallback = _objective_analysis_for_lesson(
            opportunity, spec, db, allow_fallback=allow_fallback
        )
        verify_lesson(spec, analysis)
    except ObjectiveAnalysisUnavailable as error:
        opportunity.verification_status = "unavailable"
        opportunity.verification_error = str(error)
        return None, False
    except (KeyError, LessonVerificationError, TypeError, ValidationError, ValueError) as error:
        opportunity.verification_status = "invalid"
        opportunity.verification_error = str(error)
        return None, False

    opportunity.verification_status = "verified"
    opportunity.verification_error = None
    verified_spec = spec.model_dump(mode="json")
    if verified_spec != opportunity.lesson_spec:
        opportunity.lesson_spec = verified_spec
    return spec, used_fallback


def _transfer_measurement_lesson(measurement: TransferMeasurement) -> LessonSpec:
    if measurement.target_move_uci is None:
        raise HTTPException(
            status_code=500, detail="Transfer measurement is missing its target move"
        )
    board = chess.Board(measurement.target_fen)
    if not board.is_valid():
        raise HTTPException(
            status_code=500, detail="Transfer measurement has an invalid target FEN"
        )
    move = chess.Move.from_uci(measurement.target_move_uci)
    if move not in board.legal_moves:
        raise HTTPException(
            status_code=500, detail="Transfer measurement has an invalid target move"
        )
    return LessonSpec.model_validate(
        {
            "schema_version": "1.0",
            "lesson_id": measurement.id,
            "source": {"kind": "custom", "fen": measurement.target_fen},
            "diagnosis": {"primary": measurement.skill_id, "confidence": 1.0},
            "objective": {"type": "find_best_move", "instruction": "Find the best move."},
            "interaction": {
                "input": "move",
                "maximum_attempts": 1,
                "accepted_moves": [{"san": board.san(move)}],
            },
            "verification": {"status": "verified", "engine": "transfer_catalog"},
        }
    )


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
    fallbacks_remaining = 1
    for opportunity in opportunities:
        schedule = state.active_reviews.get(str(opportunity.id))
        if schedule is None:
            continue
        spec: LessonSpec
        if opportunity.verification_status == "invalid":
            continue
        if opportunity.verification_status == "verified":
            spec = LessonSpec.model_validate(opportunity.lesson_spec)
        else:
            reverified_spec, used_fallback = _reverify_persisted_lesson(
                opportunity,
                db,
                allow_fallback=fallbacks_remaining > 0,
            )
            if used_fallback:
                fallbacks_remaining -= 1
            if reverified_spec is None:
                continue
            spec = reverified_spec
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
    transfer_lessons = [
        _transfer_measurement_lesson(measurement)
        for measurement in due_transfer_measurements(db, player_id=player_id, now=now)
    ]

    composed_session = SessionComposer().compose_session(pool, session_size=5)
    study_session = StudySession(player_id=player_id, domain="daily_training")
    db.add(study_session)
    db.commit()
    return TrainingSessionRead(
        session_id=study_session.id,
        lessons=transfer_lessons + [item["spec"] for item in composed_session],
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

    if attempt_in.source_kind == "transfer_measurement":
        if attempt_in.submitted_move is None:
            raise HTTPException(status_code=422, detail="Transfer attempts require a move")
        try:
            measurement_id = UUID(attempt_in.lesson_id)
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="Transfer measurement id must be a UUID"
            ) from error
        measurement = db.get(TransferMeasurement, measurement_id)
        if measurement is None or measurement.player_id != study_session.player_id:
            raise HTTPException(status_code=404, detail="Transfer measurement not found")
        if measurement.target_move_uci is None:
            raise HTTPException(
                status_code=500, detail="Transfer measurement is missing its target move"
            )
        success = _submitted_move_is_accepted(
            _transfer_measurement_lesson(measurement), attempt_in.submitted_move
        )
        completed_measurement = record_transfer_measurement(
            db,
            measurement_id=measurement.id,
            player_id=study_session.player_id,
            succeeded=success,
            now=datetime.now(UTC),
        )
        attempt = LessonAttempt(
            session_id=study_session.id,
            player_id=study_session.player_id,
            lesson_id=str(measurement.id),
            source_kind=attempt_in.source_kind,
            submitted_move=attempt_in.submitted_move,
            elapsed_ms=attempt_in.elapsed_ms,
            hints_used=attempt_in.hints_used,
            success=completed_measurement.succeeded,
            grading_status="verified",
            profile_update_result="not_applicable",
            completed_at=completed_measurement.completed_at,
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
        skill_state = db.get(
            SkillState,
            (study_session.player_id, schedule.skill_id),
        )
        if (
            success
            and skill_state is not None
            and skill_state.retired_at is None
            and skill_state.expected_mastery >= 0.8
        ):
            assign_production_transfer_measurements(
                db,
                player_id=study_session.player_id,
                skill_id=schedule.skill_id,
                target_difficulty=profile.rating,
                now=observed_at,
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


@router.get("/transfer-report", response_model=TransferMeasurementReport)
def get_transfer_report(
    player_id: str,
    skill_id: str,
    request: Request,
    db: Session = Depends(get_session),
) -> TransferMeasurementReport:
    require_player_token(request, player_id, db)
    return build_transfer_measurement_report(
        db,
        cohort_id=f"{PRODUCTION_TRANSFER_LIFECYCLE_PREFIX}:{player_id}:{skill_id}",
        skill_id=skill_id,
    )

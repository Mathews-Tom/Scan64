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
from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview, PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import LessonAttempt, StudySession
from scan64.content.puzzle_practice import lock_puzzle_player
from scan64.learning.diagnosis.visibility import visible_game_lesson
from scan64.learning.evaluation.transfer_measurement import (
    PRODUCTION_TRANSFER_LIFECYCLE_PREFIX,
    TransferMeasurement,
    TransferMeasurementError,
    TransferMeasurementReport,
    assign_production_transfer_measurements,
    build_transfer_measurement_report,
    due_transfer_measurements,
    record_training_completion,
    record_transfer_measurement,
)
from scan64.learning.exercises.hints import fill_lesson_hints
from scan64.learning.exercises.sound_moves import analyze_lesson_source, sound_moves
from scan64.learning.habit import ensure_policy, record_lesson_credit
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
from scan64.learning.tolerance import SOUND_MOVE_WIN_PROBABILITY_LOSS, review_loss
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson
from scan64.persistence.database import get_session
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig

router = APIRouter(prefix="/v1/learning", tags=["learning"])


class SafeOrBlunderChoice(BaseModel):
    uci: str
    san: str


class TrainingSessionRead(BaseModel):
    session_id: str
    lessons: list[LessonSpec]
    move_choices: dict[str, list[SafeOrBlunderChoice]]


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
    def __init__(self, message: str, *, used_fallback: bool = False) -> None:
        super().__init__(message)
        self.used_fallback = used_fallback


class InvalidFallbackAnalysis(ValueError):
    """An invalid engine result from a search that consumed the fallback budget."""


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
        if analysis is not None and analysis.raw_result:
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
            analyze_lesson_source(
                StockfishAdapter(StockfishConfig()), spec.source.fen, nodes=100_000
            )
        )
    except (OSError, chess.engine.EngineError) as error:
        raise ObjectiveAnalysisUnavailable(
            "Objective engine analysis is unavailable", used_fallback=True
        ) from error
    except ValueError as error:
        raise InvalidFallbackAnalysis(str(error)) from error
    analysis.position_id = opportunity.source_position_id
    db.add(analysis)
    db.flush()
    opportunity.verification_analysis_id = analysis.id
    return analysis, True


def _reverify_persisted_lesson(
    opportunity: PersistedLessonOpportunity, db: Session, *, allow_fallback: bool
) -> tuple[LessonSpec | None, bool]:
    used_fallback = False
    try:
        spec = LessonSpec.model_validate(opportunity.lesson_spec)
        analysis, used_fallback = _objective_analysis_for_lesson(
            opportunity, spec, db, allow_fallback=allow_fallback
        )
        verify_lesson(spec, analysis)
        fill_lesson_hints(spec, analysis)
        verify_lesson(spec, analysis)
    except ObjectiveAnalysisUnavailable as error:
        used_fallback = error.used_fallback
        opportunity.verification_status = "unavailable"
        opportunity.verification_error = str(error)
        return None, used_fallback
    except (KeyError, LessonVerificationError, TypeError, ValidationError, ValueError) as error:
        used_fallback = used_fallback or isinstance(error, InvalidFallbackAnalysis)
        opportunity.verification_status = "invalid"
        opportunity.verification_error = str(error)
        return None, used_fallback

    opportunity.verification_status = "verified"
    opportunity.verification_error = None
    verified_spec = spec.model_dump(mode="json")
    if verified_spec != opportunity.lesson_spec:
        opportunity.lesson_spec = verified_spec
    return spec, used_fallback


def _transfer_measurement_lesson(
    measurement: TransferMeasurement,
) -> LessonSpec | None:
    if measurement.target_move_uci is None:
        return None
    try:
        board = chess.Board(measurement.target_fen)
        move = chess.Move.from_uci(measurement.target_move_uci)
    except ValueError:
        return None
    if not board.is_valid() or move not in board.legal_moves:
        return None
    return LessonSpec.model_validate(
        {
            "schema_version": "0.2.0",
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


def _safe_or_blunder_choices(
    opportunity: PersistedLessonOpportunity, spec: LessonSpec, db: Session
) -> list[SafeOrBlunderChoice] | None:
    """Pair the owned occurrence's played move with a stored sound alternative."""
    if (
        opportunity.verification_status != "verified"
        or spec.verification.status != "verified"
        or spec.source.kind != "player_game"
        or spec.diagnosis is None
        or not spec.interaction.accepted_moves
    ):
        return None
    position = db.get(Position, opportunity.source_position_id)
    game = db.get(Game, opportunity.game_id)
    if (
        position is None
        or position.game_id != opportunity.game_id
        or position.fen != spec.source.fen
        or game is None
        or game.owner_player_id != opportunity.player_id
        or game.owner_color not in {"white", "black"}
    ):
        return None
    analysis = (
        db.get(EngineAnalysis, opportunity.verification_analysis_id)
        if opportunity.verification_analysis_id is not None
        else None
    )
    if analysis is None or analysis.position_id != position.id:
        return None
    reviews = db.exec(
        select(GameMoveReview)
        .where(GameMoveReview.game_id == game.id)
        .where(GameMoveReview.source_position_id == position.id)
        .where(GameMoveReview.mover == game.owner_color)
        .limit(2)
    ).all()
    if len(reviews) != 1:
        return None
    loss = review_loss(reviews[0].before_probability, reviews[0].after_probability)
    if loss is None or loss <= SOUND_MOVE_WIN_PROBABILITY_LOSS:
        return None
    try:
        board = _lesson_board(spec)
        if ("white" if board.turn else "black") != game.owner_color:
            return None
        proved_moves = sound_moves(
            board, analysis, avoid_mate=spec.diagnosis.primary == "threat_processing.allowed_mate"
        )
        played = board.parse_uci(reviews[0].move_uci)
        sound = board.parse_san(spec.interaction.accepted_moves[0].san)
    except (LessonVerificationError, ValueError):
        return None
    if played in proved_moves or sound not in proved_moves:
        return None
    choices = [
        SafeOrBlunderChoice(uci=played.uci(), san=board.san(played)),
        SafeOrBlunderChoice(uci=sound.uci(), san=board.san(sound)),
    ]
    if opportunity.id.int % 2:
        choices.reverse()
    return choices


@router.get("/session", response_model=TrainingSessionRead)
def get_training_session(
    player_id: str,
    request: Request,
    db: Session = Depends(get_session),
    mode: Literal["review", "safe_or_blunder"] = "review",
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
    move_choices: dict[str, list[SafeOrBlunderChoice]] = {}
    attempt_counts: dict[UUID, int] = {}
    if mode == "safe_or_blunder":
        attempt_counts = {
            opportunity_id: int(count)
            for opportunity_id, count in db.exec(
                select(LessonAttempt.opportunity_id, func.count())
                .where(LessonAttempt.player_id == player_id)
                .group_by(col(LessonAttempt.opportunity_id))
            ).all()
            if opportunity_id is not None
        }
    fallbacks_remaining = 1
    for opportunity in opportunities:
        schedule = state.active_reviews.get(str(opportunity.id))
        if schedule is None:
            continue
        spec = visible_game_lesson(opportunity, db)
        if spec is None or opportunity.verification_status == "invalid":
            continue
        if mode == "safe_or_blunder":
            if attempt_counts.get(opportunity.id, 0) >= spec.interaction.maximum_attempts:
                continue
            choices = _safe_or_blunder_choices(opportunity, spec, db)
            if choices is None:
                continue
            move_choices[str(opportunity.id)] = choices
        elif opportunity.verification_status != "verified" or not spec.hints:
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
            if spec.diagnosis is None:
                continue
            spec.diagnosis.secondary = []
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
    required_transfer_ids: list[str] = []
    if mode == "review":
        for measurement in due_transfer_measurements(db, player_id=player_id, now=now):
            transfer_spec = _transfer_measurement_lesson(measurement)
            if transfer_spec is None:
                continue
            if len(required_transfer_ids) == 5:
                break
            required_transfer_ids.append(measurement.id)
            pool.append(
                {
                    "id": measurement.id,
                    "type": "transfer",
                    "priority": 1.0,
                    "spec": transfer_spec,
                }
            )

    composed_session = SessionComposer().compose_session(
        pool, session_size=5, required_item_ids=required_transfer_ids
    )
    study_session = StudySession(
        player_id=player_id,
        domain="safe_or_blunder" if mode == "safe_or_blunder" else "daily_training",
    )
    db.add(study_session)
    db.commit()
    return TrainingSessionRead(
        session_id=study_session.id,
        lessons=[item["spec"] for item in composed_session],
        move_choices={
            item["id"]: move_choices[item["id"]]
            for item in composed_session
            if item["id"] in move_choices
        },
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
    # Fence reset against both ordinary and engine-reverified lesson outcomes.
    # A caller holding a pre-reset identity-map object must not resurrect it.
    lock_puzzle_player(authenticated_player_id, db)
    study_session = db.get(StudySession, attempt_in.session_id, populate_existing=True)
    if study_session is None or study_session.player_id != authenticated_player_id:
        raise HTTPException(status_code=404, detail="Study session not found")
    if (
        study_session.domain == "safe_or_blunder"
        and attempt_in.source_kind != "persisted_opportunity"
    ):
        raise HTTPException(status_code=422, detail="This drill requires a persisted lesson")
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
            measurement_id = str(UUID(attempt_in.lesson_id))
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="Transfer measurement id must be a UUID"
            ) from error
        measurement = db.get(TransferMeasurement, measurement_id)
        if measurement is None or measurement.player_id != study_session.player_id:
            raise HTTPException(status_code=404, detail="Transfer measurement not found")
        transfer_spec = _transfer_measurement_lesson(measurement)
        if transfer_spec is None:
            raise HTTPException(
                status_code=409,
                detail="Transfer measurement cannot produce a lesson",
            )
        success = _submitted_move_is_accepted(transfer_spec, attempt_in.submitted_move)
        completed_at = datetime.now(UTC)
        try:
            completed_measurement = record_transfer_measurement(
                db,
                measurement_id=measurement.id,
                player_id=study_session.player_id,
                succeeded=success,
                now=completed_at,
                commit=False,
            )
            if completed_measurement.measurement_point.value == "pre_test":
                record_training_completion(
                    db,
                    cohort_id=completed_measurement.cohort_id,
                    player_id=study_session.player_id,
                    skill_id=completed_measurement.skill_id,
                    completed_at=completed_at,
                    commit=False,
                )
        except TransferMeasurementError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
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
    if opportunity.verification_status == "invalid" or (
        study_session.domain == "safe_or_blunder" and opportunity.verification_status != "verified"
    ):
        raise HTTPException(status_code=409, detail="Persisted lesson is not verified")
    if opportunity.verification_status == "verified":
        spec = LessonSpec.model_validate(opportunity.lesson_spec)
    else:
        reverified_spec, _ = _reverify_persisted_lesson(opportunity, db, allow_fallback=True)
        if reverified_spec is None:
            db.add(opportunity)
            db.commit()
            raise HTTPException(status_code=409, detail="Persisted lesson is not verified")
        spec = reverified_spec
    if attempt_in.submitted_move is None:
        raise HTTPException(status_code=422, detail="Persisted lesson attempts require a move")
    if study_session.domain == "safe_or_blunder":
        visible_spec = visible_game_lesson(opportunity, db)
        choices = (
            _safe_or_blunder_choices(opportunity, visible_spec, db)
            if visible_spec is not None and schedule.retired_at is None
            else None
        )
        if choices is None:
            raise HTTPException(status_code=409, detail="This drill is no longer available")
        if attempt_in.submitted_move not in {choice.uci for choice in choices}:
            raise HTTPException(status_code=422, detail="Choose one of the two drill moves")
    # Serialize first-attempt admission with every other outcome for this owner.
    # Policy initialization precedes the server completion timestamp.
    ensure_policy(study_session.player_id, db)
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
    record_lesson_credit(attempt, db)
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
    try:
        return build_transfer_measurement_report(
            db,
            cohort_id=f"{PRODUCTION_TRANSFER_LIFECYCLE_PREFIX}:{player_id}:{skill_id}",
            skill_id=skill_id,
        )
    except TransferMeasurementError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

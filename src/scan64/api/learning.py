from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import chess
from chess_lesson_spec import (
    AcceptedMove,
    Diagnosis,
    Interaction,
    LessonSpec,
    Objective,
    Source,
    Verification,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from scan64.api.auth import require_player_token
from scan64.api.models import PlayerProfile
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game, PlaySession
from scan64.content.endgames.curated import ENDGAME_PUZZLES
from scan64.content.famous_games.curated import FAMOUS_GAMES
from scan64.content.famous_games.models import FamousGameDecision, FamousGameDefinition
from scan64.content.models import ContentItem, LessonAttempt, StudySession
from scan64.content.openings.curated import OPENING_FAMILIES
from scan64.content.openings.models import OpeningFamilyPayload
from scan64.learning.profiling.profile_update import apply_lesson_attempt
from scan64.learning.scheduling.composer import SessionComposer
from scan64.learning.scheduling.opening_rotation import (
    OpeningRotationPlanner,
    classify_opening_family,
)
from scan64.learning.scheduling.priority import PriorityFactors
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

def make_endgame_spec(puzzle: dict[str, Any]) -> LessonSpec:
    import chess
    board = chess.Board(puzzle["fen"])
    san_moves = []
    for uci in puzzle["solution"]:
        move = chess.Move.from_uci(uci)
        san_moves.append(board.san(move))
        board.push(move)
    return LessonSpec(
        schema_version="1.0",
        lesson_id=puzzle["id"],
        source=Source(kind="custom", fen=puzzle["fen"]),
        diagnosis=Diagnosis(primary="endgame", confidence=1.0),
        objective=Objective(type="find_best_move", instruction="Win the endgame."),
        interaction=Interaction(
            input="click",
            maximum_attempts=3,
            accepted_moves=[AcceptedMove(san=m) for m in san_moves]
        ),
        verification=Verification(status="verified", engine="syzygy")
    )

def make_opening_spec(family_item: ContentItem) -> LessonSpec:
    payload = OpeningFamilyPayload.model_validate(family_item.payload)
    name = payload.name
    moves = payload.moves

    if not moves:
        raise ValueError(f"Opening {name} has no moves defined")

    import chess
    board = chess.Board()
    for m in moves[:-1]:
        board.push_san(m)
    fen = board.fen()
    last_move = moves[-1]

    return LessonSpec(
        schema_version="1.0",
        lesson_id=f"opening_{name.replace(' ', '_').lower()}",
        source=Source(
            kind="custom", fen=fen
        ),
        diagnosis=Diagnosis(primary="opening", confidence=1.0),
        objective=Objective(type="find_best_move", instruction=f"Play the {name}."),
        interaction=Interaction(
            input="click",
            maximum_attempts=3,
            accepted_moves=[AcceptedMove(san=last_move)]
        ),
        verification=Verification(status="verified", engine="expert")
    )

def make_famous_game_spec(game: FamousGameDefinition, decision: FamousGameDecision) -> LessonSpec:
    return LessonSpec(
        schema_version="1.0",
        lesson_id=f"{game.id}_{decision.id}",
        source=Source(kind="custom", fen=decision.fen),
        diagnosis=Diagnosis(primary="tactics", confidence=1.0),
        objective=Objective(type="find_best_move", instruction=decision.prompt),
        interaction=Interaction(
            input="click",
            maximum_attempts=3,
            accepted_moves=[AcceptedMove(san=m) for m in decision.accepted_moves]
        ),
        verification=Verification(status="verified", engine="expert")
    )

def _recent_opening_family_ids(
    player_id: str,
    db: Session,
    opening_families: list[OpeningFamilyPayload],
    history_window: int,
) -> list[str]:
    recent_games = db.exec(
        select(Game)
        .join(PlaySession, PlaySession.game_id == Game.id)  # type: ignore[arg-type]
        .where(PlaySession.player_id == player_id)
        .order_by(col(Game.created_at).desc())
        .limit(history_window)
    ).all()
    return list(reversed([
        family_id
        for game in recent_games
        if (family_id := classify_opening_family(game.moves, opening_families)) is not None
    ]))


@router.get("/session", response_model=TrainingSessionRead)
def get_training_session(player_id: str, db: Session = Depends(get_session)) -> TrainingSessionRead:
    now = datetime.now(UTC)

    # 1. Gather all potential items
    pool: list[dict[str, Any]] = []

    # Endgames
    for eg in ENDGAME_PUZZLES:
        pool.append({
            "id": eg["id"],
            "source": "m15_tablebase",
            "content_type": "endgame",
            "spec": make_endgame_spec(eg),
            "base_priority": 0.5
        })

    # Openings
    opening_payloads = [
        OpeningFamilyPayload.model_validate(family_item.payload)
        for family_item in OPENING_FAMILIES
    ]
    opening_lesson_ids: dict[str, str] = {}
    for family_item, payload in zip(OPENING_FAMILIES, opening_payloads, strict=True):
        spec = make_opening_spec(family_item)
        opening_lesson_ids[payload.family_id] = spec.lesson_id
        pool.append({
            "id": spec.lesson_id,
            "source": "m16_opening",
            "content_type": "opening",
            "opening_family_id": payload.family_id,
            "spec": spec,
            "base_priority": 0.5,
        })

    # Famous Games
    for fg in FAMOUS_GAMES:
        for dec in fg.payload.decisions:
            spec = make_famous_game_spec(fg, dec)
            pool.append({
                "id": spec.lesson_id,
                "source": "m17_famous_game",
                "content_type": "famous_game",
                "spec": spec,
                "base_priority": 0.6
            })

    # Persisted M9 Opportunities
    opportunities = db.exec(
        select(PersistedLessonOpportunity).where(PersistedLessonOpportunity.player_id == player_id)
    ).all()
    for opportunity in opportunities:
        spec = LessonSpec.model_validate(opportunity.lesson_spec)
        spec.lesson_id = str(opportunity.id)
        pool.append({
            "id": str(opportunity.id),
            "source": "m9_exercise",
            "content_type": "exercise",
            "spec": spec,
            "base_priority": 0.9,
        })

    rotation_planner = OpeningRotationPlanner()
    rotation_plan = rotation_planner.plan(
        opening_payloads,
        _recent_opening_family_ids(
            player_id,
            db,
            opening_payloads,
            rotation_planner.history_window,
        ),
    )

    # 2. Attach scheduling metadata (ReviewSchedule)
    familiar_family_id = rotation_plan.familiar_family_id
    response_review_family_id = rotation_plan.response_review_family_id

    for item in pool:
        schedule = db.get(ReviewSchedule, (player_id, item["id"]))
        if schedule:
            is_due = schedule.is_due(now)
            if is_due:
                item["type"] = "due"
            else:
                item["type"] = (
                    "transfer" if item["content_type"] == "famous_game" else "exploration"
                )
            pf = PriorityFactors(
                review_due=1.0 if is_due else 0.0,
                weakness_severity=0.8 if item["content_type"] == "exercise" else 0.0,
                user_interest=0.5 if item["content_type"] == "famous_game" else 0.0
            )
            item["priority"] = pf.compute_priority(session_fatigue=0.0)
        else:
            item["type"] = (
                "mistakes" if item["content_type"] == "exercise"
                else ("transfer" if item["content_type"] == "famous_game" else "exploration")
            )
            pf = PriorityFactors(
                review_due=0.0,
                weakness_severity=0.8 if item["content_type"] == "exercise" else 0.0,
                user_interest=0.5 if item["content_type"] == "famous_game" else 0.0,
                curriculum_relevance=0.8 if item["content_type"] in ("endgame", "opening") else 0.0
            )
            item["priority"] = pf.compute_priority(session_fatigue=0.0)
        if (
            familiar_family_id is not None
            and item.get("opening_family_id") == familiar_family_id
        ):
            item["priority"] += 0.05
        if (
            response_review_family_id is not None
            and item.get("opening_family_id") == response_review_family_id
        ):
            item["priority"] += 0.05


    required_rotation_item_ids: tuple[str, ...] = ()
    if rotation_plan.required_family_id is not None:
        required_rotation_item_ids = (
            opening_lesson_ids[rotation_plan.required_family_id],
        )

    # 3. Compose session
    composer = SessionComposer()
    composed_session = composer.compose_session(
        pool,
        session_size=5,
        required_item_ids=required_rotation_item_ids,
    )

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
        san = board.san(chess.Move.from_uci(submitted_move))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Submitted move is not legal") from error
    return any(move.san == san for move in spec.interaction.accepted_moves)


@router.post("/lesson-attempts", response_model=LessonAttemptRead)
def record_lesson_attempt(
    attempt_in: LessonAttemptCreate,
    request: Request,
    db: Session = Depends(get_session),
) -> LessonAttemptRead:
    study_session = db.get(StudySession, attempt_in.session_id)
    if study_session is None:
        raise HTTPException(status_code=404, detail="Study session not found")
    require_player_token(request, study_session.player_id, db)
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

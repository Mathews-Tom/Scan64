"""Bounded, read-only mistake discovery and owned lesson retry admission."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from chess_lesson_spec import LessonSpec
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func
from sqlmodel import Session, col, select

from scan64.api.auth import require_authenticated_player
from scan64.api.game_review import ReviewKeyMomentRead
from scan64.api.learning import _reverify_persisted_lesson
from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview, PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import LessonAttempt, StudySession
from scan64.content.puzzle_practice import lock_puzzle_player
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.diagnosis.visibility import visible_game_lesson
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import get_session

router = APIRouter(prefix="/v1/puzzles/mistakes", tags=["puzzles"])
ExclusionReason = Literal["no_review_schedule", "retired", "untrusted", "exhausted"]


class PuzzleMistakeRead(BaseModel):
    lesson_id: str
    game_id: str
    ply: int
    attempts_remaining: int


class PuzzleMistakeExclusionsRead(BaseModel):
    no_review_schedule: int = 0
    retired: int = 0
    untrusted: int = 0
    exhausted: int = 0


class PuzzleMistakesRead(BaseModel):
    items: list[PuzzleMistakeRead]
    excluded: PuzzleMistakeExclusionsRead = Field(
        description="Reason counts for this page of owned lesson candidates only."
    )
    offset: int
    limit: int
    has_more: bool = Field(
        description="More candidates exist; advance offset by limit, even for an empty items page."
    )


class PuzzleMistakeStartRead(BaseModel):
    moment: ReviewKeyMomentRead
    session_id: str


@dataclass(frozen=True)
class _EligibleMistake:
    spec: LessonSpec
    ply: int
    attempts_used: int


def _eligible_mistake(
    opportunity: PersistedLessonOpportunity, game: Game, player_id: str, session: Session
) -> _EligibleMistake | ExclusionReason:
    schedule = session.get(ReviewSchedule, (player_id, str(opportunity.id)))
    if schedule is None:
        return "no_review_schedule"
    if schedule.retired_at is not None:
        return "retired"
    if (
        opportunity.verification_status != "verified"
        or opportunity.verification_analysis_id is None
    ):
        return "untrusted"
    try:
        spec = visible_game_lesson(opportunity, session)
    except (TypeError, ValidationError, ValueError):
        return "untrusted"
    if spec is None:
        return "untrusted"
    position = session.get(Position, opportunity.source_position_id)
    analysis = session.get(EngineAnalysis, opportunity.verification_analysis_id)
    if (
        position is None
        or position.game_id != game.id
        or position.fen != spec.source.fen
        or analysis is None
        or analysis.position_id != position.id
        or not analysis.raw_result
    ):
        return "untrusted"
    # The source-position join identifies an occurrence, not an arbitrary matching
    # FEN or the position reached after the mistake. Never offer opponent moves.
    move = session.exec(
        select(GameMoveReview)
        .where(
            GameMoveReview.game_id == game.id,
            GameMoveReview.source_position_id == position.id,
            GameMoveReview.mover == game.owner_color,
        )
        .order_by(col(GameMoveReview.ply))
        .limit(1)
    ).first()
    if (
        move is None
        or move.ply < 1
        or move.ply > len(game.moves)
        or game.moves[move.ply - 1] != move.move_uci
    ):
        return "untrusted"
    attempts = session.exec(
        select(func.count())
        .select_from(LessonAttempt)
        .where(
            LessonAttempt.player_id == player_id,
            LessonAttempt.opportunity_id == opportunity.id,
        )
    ).one()
    if attempts >= spec.interaction.maximum_attempts:
        return "exhausted"
    return _EligibleMistake(spec=spec, ply=move.ply, attempts_used=attempts)


@router.get("", response_model=PuzzleMistakesRead)
def read_puzzle_mistakes(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=50),
    session: Session = Depends(get_session),
) -> PuzzleMistakesRead:
    player_id = require_authenticated_player(request, session)
    # Page candidates before visibility checks: work and hidden-content reads are
    # bounded even when an owner's entire lesson history has become ineligible.
    candidates = session.exec(
        select(PersistedLessonOpportunity, Game)
        .join(Game, col(Game.id) == col(PersistedLessonOpportunity.game_id))
        .where(
            PersistedLessonOpportunity.player_id == player_id,
            Game.owner_player_id == player_id,
        )
        .order_by(
            col(PersistedLessonOpportunity.created_at).desc(), col(PersistedLessonOpportunity.id)
        )
        .offset(offset)
        .limit(limit + 1)
    ).all()
    excluded: dict[ExclusionReason, int] = {
        "no_review_schedule": 0,
        "retired": 0,
        "untrusted": 0,
        "exhausted": 0,
    }
    items = []
    for opportunity, game in candidates[:limit]:
        candidate = _eligible_mistake(opportunity, game, player_id, session)
        if isinstance(candidate, str):
            excluded[candidate] += 1
            continue
        items.append(
            PuzzleMistakeRead(
                lesson_id=str(opportunity.id),
                game_id=str(game.id),
                ply=candidate.ply,
                attempts_remaining=candidate.spec.interaction.maximum_attempts
                - candidate.attempts_used,
            )
        )
    return PuzzleMistakesRead(
        items=items,
        excluded=PuzzleMistakeExclusionsRead(**excluded),
        offset=offset,
        limit=limit,
        has_more=len(candidates) > limit,
    )


@router.post("/{lesson_id}/start", response_model=PuzzleMistakeStartRead)
def start_puzzle_mistake(
    lesson_id: str, request: Request, session: Session = Depends(get_session)
) -> PuzzleMistakeStartRead:
    player_id = require_authenticated_player(request, session)
    not_found = HTTPException(status_code=404, detail="Persisted lesson opportunity not found")
    try:
        opportunity_id = UUID(lesson_id)
    except ValueError:
        raise not_found from None
    lock_puzzle_player(player_id, session)
    owned = session.exec(
        select(PersistedLessonOpportunity, Game)
        .join(Game, col(Game.id) == col(PersistedLessonOpportunity.game_id))
        .where(
            PersistedLessonOpportunity.id == opportunity_id,
            PersistedLessonOpportunity.player_id == player_id,
            Game.owner_player_id == player_id,
        )
    ).one_or_none()
    if owned is None:
        session.rollback()
        raise not_found
    opportunity, game = owned
    active = session.exec(
        select(PuzzleRun.id).where(PuzzleRun.player_id == player_id, PuzzleRun.state == "active")
    ).first()
    if active is not None:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Finish or abandon the active puzzle before practicing a mistake",
        )
    candidate = _eligible_mistake(opportunity, game, player_id, session)
    if isinstance(candidate, str):
        details = {
            "no_review_schedule": "Persisted lesson has no review schedule",
            "retired": "Persisted lesson is retired",
            "untrusted": "Persisted lesson is not available for practice",
            "exhausted": "Maximum attempts reached for persisted lesson",
        }
        session.rollback()
        raise HTTPException(status_code=409, detail=details[candidate])
    # Admission checks pin a usable source analysis first, preventing the helper
    # from choosing a newer analysis or searching when its stored proof is gone.
    analysis_id = opportunity.verification_analysis_id
    spec, _ = _reverify_persisted_lesson(opportunity, session, allow_fallback=False)
    session.add(opportunity)
    if (
        spec is None
        or spec.diagnosis is None
        or opportunity.verification_analysis_id != analysis_id
    ):
        session.commit()
        raise HTTPException(status_code=409, detail="Persisted lesson is not verified")
    spec.diagnosis.secondary = []
    spec.lesson_id = str(opportunity.id)
    study = session.exec(
        select(StudySession)
        .where(
            StudySession.player_id == player_id,
            StudySession.domain == f"game_analysis:{game.id}",
        )
        .order_by(col(StudySession.started_at).desc())
    ).first()
    if study is None:
        study = StudySession(player_id=player_id, domain=f"game_analysis:{game.id}")
        session.add(study)
    session.commit()
    return PuzzleMistakeStartRead(
        moment=ReviewKeyMomentRead(
            ply=candidate.ply,
            lesson_id=str(opportunity.id),
            lesson=spec,
            attempts_used=candidate.attempts_used,
            attempts_remaining=max(0, spec.interaction.maximum_attempts - candidate.attempts_used),
        ),
        session_id=study.id,
    )

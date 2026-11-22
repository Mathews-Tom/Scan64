"""Owner-scoped repertoire playback, coverage and server-verified decision drills."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlmodel import Session, col, select

from scan64.api.auth import require_authenticated_player
from scan64.content.puzzle_practice import lock_puzzle_player
from scan64.learning.repertoire import (
    MAX_PGN_BYTES,
    Repertoire,
    RepertoireAttempt,
    RepertoireTree,
    parse_repertoire_pgn,
    repertoire_item_id,
    verify_repertoire_move,
)
from scan64.learning.repertoire_archive import repertoire_review_time
from scan64.learning.repertoire_deviations import RepertoireDeviationReport, compare_repertoire
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule, ScheduleReviewObservation
from scan64.persistence.database import get_session

router = APIRouter(prefix="/v1/repertoires", tags=["repertoires"])


class RepertoireImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pgn: str = Field(min_length=1, max_length=MAX_PGN_BYTES)


class DecisionCoverage(BaseModel):
    node_id: int
    reviewed: bool
    due: bool
    next_review_at: datetime | None


class RepertoireRead(BaseModel):
    id: UUID
    title: str
    tree: RepertoireTree
    decisions: list[DecisionCoverage]
    total_decisions: int
    reviewed_decisions: int
    due_decisions: int


class RepertoireAttemptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # JSON UUIDs need normal UUID parsing, while node/move input remains strict.
    attempt_id: str = Field(min_length=36, max_length=36)
    node_id: int = Field(ge=0)
    submitted_move: str = Field(min_length=4, max_length=5)


class RepertoireAttemptRead(BaseModel):
    attempt_id: UUID
    success: bool
    rating: int
    next_review_at: datetime
    repertoire: RepertoireRead


def _owned(repertoire_id: UUID, player_id: str, session: Session) -> Repertoire:
    repertoire = session.exec(
        select(Repertoire).where(
            Repertoire.id == repertoire_id,
            Repertoire.player_id == player_id,
        )
    ).one_or_none()
    if repertoire is None:
        raise HTTPException(status_code=404, detail="Repertoire not found")
    return repertoire


def _read(repertoire: Repertoire, session: Session) -> RepertoireRead:
    now = datetime.now(UTC)
    tree = RepertoireTree.model_validate(repertoire.tree)
    # Exact identities, never an unscoped item-id or browser-owned schedule.
    item_ids = [repertoire_item_id(repertoire.id, node.id) for node in tree.nodes if node.children]
    schedules = {
        schedule.item_id: schedule
        for schedule in session.exec(
            select(ReviewSchedule).where(
                ReviewSchedule.player_id == repertoire.player_id,
                col(ReviewSchedule.item_id).in_(item_ids),
            )
        ).all()
    }
    decisions: list[DecisionCoverage] = []
    for node in tree.nodes:
        if not node.children:
            continue
        schedule = schedules.get(repertoire_item_id(repertoire.id, node.id))
        decisions.append(
            DecisionCoverage(
                node_id=node.id,
                reviewed=schedule is not None and schedule.last_reviewed_at is not None,
                due=schedule is None or schedule.is_due(now),
                # Stored instants are UTC; SQLite returns them without tzinfo.
                next_review_at=schedule.next_review_at.replace(tzinfo=UTC) if schedule else None,
            )
        )
    return RepertoireRead(
        id=repertoire.id,
        title=repertoire.title,
        tree=tree,
        decisions=decisions,
        total_decisions=len(decisions),
        reviewed_decisions=sum(decision.reviewed for decision in decisions),
        due_decisions=sum(decision.due for decision in decisions),
    )


@router.get("", response_model=list[RepertoireRead])
def list_repertoires(
    request: Request, session: Session = Depends(get_session)
) -> list[RepertoireRead]:
    player_id = require_authenticated_player(request, session)
    repertoires = session.exec(
        select(Repertoire)
        .where(
            Repertoire.player_id == player_id,
        )
        .order_by(col(Repertoire.created_at), col(Repertoire.id))
    ).all()
    return [_read(repertoire, session) for repertoire in repertoires]


@router.post("", response_model=list[RepertoireRead], status_code=201)
def import_repertoire(
    body: RepertoireImport,
    request: Request,
    session: Session = Depends(get_session),
) -> list[RepertoireRead]:
    player_id = require_authenticated_player(request, session)
    try:
        parsed = parse_repertoire_pgn(body.pgn)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    lock_puzzle_player(player_id, session)
    require_authenticated_player(request, session)
    repertoires = [
        Repertoire(player_id=player_id, title=title, pgn=pgn, tree=tree.model_dump())
        for title, pgn, tree in parsed
    ]
    session.add_all(repertoires)
    response = [_read(repertoire, session) for repertoire in repertoires]
    session.commit()
    return response


@router.get("/{repertoire_id}", response_model=RepertoireRead)
def read_repertoire(
    repertoire_id: UUID,
    request: Request,
    session: Session = Depends(get_session),
) -> RepertoireRead:
    player_id = require_authenticated_player(request, session)
    return _read(_owned(repertoire_id, player_id, session), session)


@router.get("/{repertoire_id}/deviations", response_model=RepertoireDeviationReport)
def read_repertoire_deviations(
    repertoire_id: UUID,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> RepertoireDeviationReport:
    player_id = require_authenticated_player(request, session)
    response.headers["Cache-Control"] = "no-store"
    return compare_repertoire(_owned(repertoire_id, player_id, session), session)


@router.post("/{repertoire_id}/attempts", response_model=RepertoireAttemptRead)
def review_repertoire(
    repertoire_id: UUID,
    body: RepertoireAttemptCreate,
    request: Request,
    session: Session = Depends(get_session),
) -> RepertoireAttemptRead:
    player_id = require_authenticated_player(request, session)
    try:
        attempt_id = UUID(body.attempt_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    # Same fence as progress reset/owner deletion; re-read mutable state only
    # after acquiring it, so concurrent attempts cannot lose an FSRS update.
    lock_puzzle_player(player_id, session)
    session.expire_all()
    if require_authenticated_player(request, session) != player_id:
        raise HTTPException(status_code=403, detail="Player bearer ownership changed")
    repertoire = _owned(repertoire_id, player_id, session)
    try:
        success = verify_repertoire_move(repertoire, body.node_id, body.submitted_move)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    existing = session.get(RepertoireAttempt, attempt_id, populate_existing=True)
    item_id = repertoire_item_id(repertoire_id, body.node_id)
    schedule = session.get(ReviewSchedule, (player_id, item_id), populate_existing=True)
    if existing is not None:
        if (
            existing.player_id,
            existing.repertoire_id,
            existing.node_id,
            existing.submitted_move,
        ) != (
            player_id,
            repertoire_id,
            body.node_id,
            body.submitted_move,
        ):
            raise HTTPException(
                status_code=409, detail="Attempt identity already has different input"
            )
        if schedule is None:
            raise HTTPException(status_code=409, detail="Attempt schedule is unavailable")
        observation = session.exec(
            select(ScheduleReviewObservation).where(
                ScheduleReviewObservation.source_kind == "repertoire",
                ScheduleReviewObservation.source_id == str(attempt_id),
                ScheduleReviewObservation.player_id == player_id,
            )
        ).one()
        rating = observation.rating
        success = existing.success
    else:
        try:
            # Accepted restored timestamps may be slightly ahead of this clock;
            # wall-clock rollback must not reverse a decision's replay order.
            now = max(
                repertoire_review_time(datetime.now(UTC)),
                repertoire_review_time(repertoire.created_at),
                repertoire_review_time(schedule.last_reviewed_at)
                if schedule is not None and schedule.last_reviewed_at is not None
                else repertoire_review_time(repertoire.created_at),
            )
            if schedule is None:
                schedule = ReviewSchedule(player_id=player_id, item_id=item_id, next_review_at=now)
            observation = schedule.update(
                success,
                now,
                hints_used=None,
                elapsed_ms=None,
                source_kind="repertoire",
                source_id=str(attempt_id),
            )
        except (ValueError, OverflowError) as error:
            raise HTTPException(
                status_code=422, detail="Unsupported repertoire review time"
            ) from error
        session.add(
            RepertoireAttempt(
                id=attempt_id,
                player_id=player_id,
                repertoire_id=repertoire_id,
                node_id=body.node_id,
                submitted_move=body.submitted_move,
                sequence=1
                + session.exec(
                    select(func.count())
                    .select_from(RepertoireAttempt)
                    .where(
                        RepertoireAttempt.player_id == player_id,
                        RepertoireAttempt.repertoire_id == repertoire_id,
                        RepertoireAttempt.node_id == body.node_id,
                    )
                ).one(),
                success=success,
                completed_at=now,
            )
        )
        session.add(schedule)
        session.flush()
        session.add(observation)
        rating = observation.rating
    response = RepertoireAttemptRead(
        attempt_id=attempt_id,
        success=success,
        rating=rating,
        next_review_at=schedule.next_review_at.replace(tzinfo=UTC),
        repertoire=_read(repertoire, session),
    )
    session.commit()
    return response

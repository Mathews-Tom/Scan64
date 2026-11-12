"""Owned, revision-checked puzzle runs; complete solution specs stay server-side."""

from datetime import UTC, datetime
from typing import Literal, cast

import chess
from chess_lesson_spec import LessonSpec
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from scan64.api.auth import require_authenticated_player
from scan64.content.models import ContentAttempt, ContentItem, StudySession
from scan64.content.puzzle_hints import puzzle_hint
from scan64.content.puzzle_practice import daily_set, daily_summary, finish_practice, slots_for
from scan64.content.puzzle_practice_models import PuzzleDailySlot
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.insights import InsightStep
from scan64.learning.verification.verifier import verify_lesson
from scan64.persistence.database import get_session

router = APIRouter(prefix="/v1/puzzles", tags=["puzzles"])
MAX_WRONG_ATTEMPTS = 3
RunState = Literal["active", "solved", "failed", "abandoned"]


class PuzzleDailyRead(BaseModel):
    utc_day: str
    rating: int
    rated_count: int
    provisional: bool
    overdue_count: int
    total: int
    completed: int
    remaining: int
    actual_mix: dict[str, int]
    message: str


class PuzzleRunRead(BaseModel):
    run_id: str
    puzzle_id: str
    fen: str
    setup: str
    played_moves: list[str]
    cursor: int
    revision: int
    wrong_attempts: int
    maximum_wrong_attempts: int = MAX_WRONG_ATTEMPTS
    state: RunState
    hint_step: int
    hints: list[InsightStep]
    selection_reason: str | None
    widening: str | None
    rated: bool
    daily: PuzzleDailyRead


class PuzzleStartRead(BaseModel):
    status: Literal["ready", "empty", "exhausted"]
    run: PuzzleRunRead | None = None
    message: str
    daily: PuzzleDailyRead


class ExpectedPuzzleState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_cursor: int = Field(ge=0)
    expected_revision: int = Field(ge=0)


class PuzzleMoveCreate(ExpectedPuzzleState):
    move: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")

    @field_validator("move")
    @classmethod
    def parse_uci(cls, value: str) -> str:
        chess.Move.from_uci(value)
        return value


class PuzzleHintCreate(ExpectedPuzzleState):
    step: int = Field(ge=1, le=3)


def _item_and_lesson(run: PuzzleRun, session: Session) -> tuple[ContentItem, LessonSpec]:
    item = session.get(ContentItem, run.item_id)
    if item is None or item.domain != "puzzles":
        raise HTTPException(status_code=404, detail="Puzzle run not found")
    lesson = LessonSpec.model_validate(item.payload["lesson"])
    if (
        lesson.schema_version != "0.2.0"
        or lesson.source.kind != "external"
        or lesson.verification.status != "source_verified"
    ):
        raise ValueError("Imported puzzles require a source-verified external 0.2.0 lesson")
    verify_lesson(lesson)
    return item, lesson


def _safe_run(run: PuzzleRun, session: Session) -> PuzzleRunRead:
    item, lesson = _item_and_lesson(run, session)
    step = run.hint_steps.get(str(run.cursor), 0) if run.state == "active" else 0
    hints = [
        puzzle_hint(chess.Board(run.fen), lesson.interaction.solution[run.cursor :], level)
        for level in range(1, step + 1)
    ]
    slot = session.exec(
        select(PuzzleDailySlot).where(PuzzleDailySlot.run_id == run.id)
    ).one_or_none()
    return PuzzleRunRead(
        run_id=run.id,
        puzzle_id=item.id,
        fen=run.fen,
        setup=str(item.payload["setup"]),
        played_moves=run.played_moves,
        cursor=run.cursor,
        revision=run.revision,
        wrong_attempts=run.wrong_attempts,
        state=cast(RunState, run.state),
        hint_step=step,
        hints=hints,
        selection_reason=slot.reason if slot else None,
        widening=slot.widening if slot else None,
        rated=bool(slot and slot.first_exposure),
        daily=PuzzleDailyRead(**daily_summary(run.player_id, session)),
    )


def _owned_run(run_id: str, request: Request, session: Session) -> PuzzleRun:
    player_id = require_authenticated_player(request, session)
    run = session.exec(
        select(PuzzleRun).where(PuzzleRun.id == run_id, PuzzleRun.player_id == player_id)
    ).one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Puzzle run not found")
    return run


def _conflict(run: PuzzleRun, session: Session) -> None:
    session.rollback()
    session.refresh(run)
    raise HTTPException(
        status_code=409,
        detail={
            "message": "Puzzle state changed. Try again.",
            "run": _safe_run(run, session).model_dump(mode="json"),
        },
    )


def _expect_active(run: PuzzleRun, expected: ExpectedPuzzleState, session: Session) -> None:
    if (
        run.state != "active"
        or run.cursor != expected.expected_cursor
        or run.revision != expected.expected_revision
    ):
        _conflict(run, session)


def _active_run(player_id: str, session: Session) -> PuzzleRun | None:
    return session.exec(
        select(PuzzleRun).where(PuzzleRun.player_id == player_id, PuzzleRun.state == "active")
    ).one_or_none()


@router.get("/daily", response_model=PuzzleDailyRead)
def read_puzzle_daily(request: Request, session: Session = Depends(get_session)) -> PuzzleDailyRead:
    player_id = require_authenticated_player(request, session)
    return PuzzleDailyRead(**daily_summary(player_id, session))


@router.post("/runs", response_model=PuzzleStartRead)
def start_puzzle_run(request: Request, session: Session = Depends(get_session)) -> PuzzleStartRead:
    player_id = require_authenticated_player(request, session)
    active = _active_run(player_id, session)
    if active is not None:
        safe = _safe_run(active, session)
        return PuzzleStartRead(status="ready", run=safe, message="Run resumed.", daily=safe.daily)
    day = daily_set(player_id, session)
    slot = next((entry for entry in slots_for(day, session) if entry.closed_at is None), None)
    if slot is None:
        daily = PuzzleDailyRead(**daily_summary(player_id, session))
        has_catalog = (
            session.exec(
                select(ContentItem.id).where(ContentItem.domain == "puzzles").limit(1)
            ).first()
            is not None
        )
        return PuzzleStartRead(
            status="exhausted" if has_catalog else "empty",
            daily=daily,
            message=daily.message
            if has_catalog
            else "No puzzles imported. Run scan64-cli puzzles import --download locally, "
            "or docker exec <container> scan64-cli puzzles import --download. "
            "An already created daily set resets at 00:00 UTC.",
        )
    item = session.get(ContentItem, slot.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Puzzle run not found")
    lesson = LessonSpec.model_validate(item.payload["lesson"])
    study = StudySession(player_id=player_id, domain="puzzles")
    first_exposure = (
        session.exec(
            select(PuzzleRun.id)
            .where(PuzzleRun.player_id == player_id, PuzzleRun.item_id == item.id)
            .limit(1)
        ).first()
        is None
    )
    first_exposure = (
        first_exposure
        and session.exec(
            select(ContentAttempt.id)
            .where(ContentAttempt.player_id == player_id, ContentAttempt.item_id == item.id)
            .limit(1)
        ).first()
        is None
    )
    run = PuzzleRun(
        player_id=player_id, item_id=item.id, session_id=study.id, fen=lesson.source.fen
    )
    _item_and_lesson(run, session)
    try:
        session.add(study)
        session.flush()
        session.add(run)
        session.flush()
        bound = session.execute(
            update(PuzzleDailySlot)
            .where(
                col(PuzzleDailySlot.id) == slot.id,
                col(PuzzleDailySlot.run_id).is_(None),
                col(PuzzleDailySlot.closed_at).is_(None),
            )
            .values(run_id=run.id, first_exposure=first_exposure)
            .returning(col(PuzzleDailySlot.id))
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if bound is None:
            session.rollback()
            raise HTTPException(status_code=409, detail="Puzzle state changed. Try again.")
        session.commit()
    except IntegrityError:
        session.rollback()
        active = _active_run(player_id, session)
        if active is None:
            raise
        safe = _safe_run(active, session)
        return PuzzleStartRead(status="ready", run=safe, message="Run resumed.", daily=safe.daily)
    session.refresh(run)
    safe = _safe_run(run, session)
    return PuzzleStartRead(status="ready", run=safe, message="Puzzle ready.", daily=safe.daily)


@router.get("/runs/{run_id}", response_model=PuzzleRunRead)
def read_puzzle_run(
    run_id: str, request: Request, session: Session = Depends(get_session)
) -> PuzzleRunRead:
    return _safe_run(_owned_run(run_id, request, session), session)


def _update_run(
    run: PuzzleRun,
    expected: ExpectedPuzzleState,
    session: Session,
    *,
    fen: str,
    cursor: int,
    played_moves: list[str],
    wrong_attempts: int,
    state: str,
    hint_steps: dict[str, int],
) -> PuzzleRunRead:
    completed_at = datetime.now(UTC) if state != "active" else None
    changed = session.execute(
        update(PuzzleRun)
        .where(
            col(PuzzleRun.id) == run.id,
            col(PuzzleRun.cursor) == expected.expected_cursor,
            col(PuzzleRun.revision) == expected.expected_revision,
            col(PuzzleRun.state) == "active",
        )
        .values(
            fen=fen,
            cursor=cursor,
            played_moves=played_moves,
            wrong_attempts=wrong_attempts,
            state=state,
            hint_steps=hint_steps,
            revision=expected.expected_revision + 1,
            completed_at=completed_at,
        )
        .returning(col(PuzzleRun.id))
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if changed is None:
        _conflict(run, session)
    if state in ("solved", "failed"):
        session.add(
            ContentAttempt(
                id=run.id,
                session_id=run.session_id,
                item_id=run.item_id,
                player_id=run.player_id,
                started_at=run.started_at,
                completed_at=completed_at,
                success=state == "solved",
                hint_assisted=bool(hint_steps),
                response_payload={"run_id": run.id, "wrong_attempts": wrong_attempts},
            )
        )
    if completed_at is not None:
        study = session.get(StudySession, run.session_id)
        if study is not None:
            study.completed_at = completed_at
            session.add(study)
        if not finish_practice(run, state, hint_steps, completed_at, session):
            _conflict(run, session)
    session.commit()
    session.refresh(run)
    return _safe_run(run, session)


@router.post("/runs/{run_id}/moves", response_model=PuzzleRunRead)
def submit_puzzle_move(
    run_id: str,
    body: PuzzleMoveCreate,
    request: Request,
    session: Session = Depends(get_session),
) -> PuzzleRunRead:
    run = _owned_run(run_id, request, session)
    _expect_active(run, body, session)
    _, lesson = _item_and_lesson(run, session)
    line = lesson.interaction.solution
    board = chess.Board(run.fen)
    move = chess.Move.from_uci(body.move)
    legal = move in board.legal_moves
    if legal:
        board.push(move)
    mates = legal and board.is_checkmate()
    accepted = legal and body.move == line[run.cursor]
    cursor = run.cursor
    played = list(run.played_moves)
    wrong = run.wrong_attempts
    state = "active"
    if mates or accepted:
        played.append(body.move)
        cursor += 1
        if not mates and cursor < len(line):
            reply = line[cursor]
            board.push_uci(reply)
            played.append(reply)
            cursor += 1
        if mates or cursor == len(line):
            state = "solved"
        fen = board.fen()
    else:
        wrong += 1
        state = "failed" if wrong >= MAX_WRONG_ATTEMPTS else "active"
        fen = run.fen
    return _update_run(
        run,
        body,
        session,
        fen=fen,
        cursor=cursor,
        played_moves=played,
        wrong_attempts=wrong,
        state=state,
        hint_steps=run.hint_steps,
    )


@router.post("/runs/{run_id}/hints", response_model=PuzzleRunRead)
def reveal_puzzle_hint(
    run_id: str,
    body: PuzzleHintCreate,
    request: Request,
    session: Session = Depends(get_session),
) -> PuzzleRunRead:
    run = _owned_run(run_id, request, session)
    _expect_active(run, body, session)
    hints = {
        **run.hint_steps,
        str(run.cursor): max(body.step, run.hint_steps.get(str(run.cursor), 0)),
    }
    return _update_run(
        run,
        body,
        session,
        fen=run.fen,
        cursor=run.cursor,
        played_moves=run.played_moves,
        wrong_attempts=run.wrong_attempts,
        state="active",
        hint_steps=hints,
    )


@router.post("/runs/{run_id}/abandon", response_model=PuzzleRunRead)
def abandon_puzzle_run(
    run_id: str,
    body: ExpectedPuzzleState,
    request: Request,
    session: Session = Depends(get_session),
) -> PuzzleRunRead:
    run = _owned_run(run_id, request, session)
    _expect_active(run, body, session)
    return _update_run(
        run,
        body,
        session,
        fen=run.fen,
        cursor=run.cursor,
        played_moves=run.played_moves,
        wrong_attempts=run.wrong_attempts,
        state="abandoned",
        hint_steps=run.hint_steps,
    )

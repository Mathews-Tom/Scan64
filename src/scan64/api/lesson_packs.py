"""Untrusted community lessons: owner-scoped, locally verified, never learning evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import UUID

import chess
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, delete, select

from scan64.api.auth import require_authenticated_player
from scan64.content.puzzle_practice import lock_puzzle_player
from scan64.lesson_packs.models import LessonPack, PackLesson
from scan64.lesson_packs.schema import PackLessonDocument, parse_pack
from scan64.lesson_packs.service import verify_pack
from scan64.persistence.database import get_session
from scan64.providers.stockfish.pool import EnginePoolManager

router = APIRouter(prefix="/v1/lesson-packs", tags=["lesson-packs"])
MAX_INSTALLED_PACKS = 20


class LessonStateRead(BaseModel):
    id: str
    title: str
    state: str
    failing_ply: int | None
    reason: str | None


class PackRead(BaseModel):
    id: str
    title: str
    author: str
    licence: str
    version: str
    description: str | None
    source: str | None
    sha256: str
    installed_at: datetime
    state: str
    reason: str | None
    lessons: list[LessonStateRead]


class LessonRead(BaseModel):
    id: str
    title: str
    pack_id: str
    pack_title: str
    author: str
    fen: str
    objective: str
    themes: list[str]


class MoveCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ply: int = Field(ge=0, le=4)
    move: str | None = Field(default=None, min_length=4, max_length=5)
    hint: bool = False

    @model_validator(mode="after")
    def one_action(self) -> MoveCheck:
        if (self.move is None) != self.hint:
            raise ValueError("Supply either a move or a hint request")
        if self.ply % 2:
            raise ValueError("Only solver plies can be checked")
        return self


class MoveRead(BaseModel):
    accepted: bool
    fen: str
    ply: int
    reply: str | None
    complete: bool
    hints: list[str]
    explanation: str | None


def _owned(pack_id: str, player_id: str, session: Session) -> LessonPack:
    pack = session.exec(
        select(LessonPack).where(LessonPack.pack_id == pack_id, LessonPack.player_id == player_id)
    ).one_or_none()
    if pack is None:
        raise HTTPException(status_code=404, detail="Lesson pack not found")
    return pack


def _lessons(pack: LessonPack, session: Session) -> list[PackLesson]:
    return list(session.exec(select(PackLesson).where(PackLesson.pack_id == pack.id)).all())


def _read(pack: LessonPack, session: Session) -> PackRead:
    return PackRead(
        id=pack.pack_id,
        title=pack.title,
        author=pack.author,
        licence=pack.licence,
        version=pack.version,
        description=pack.description,
        source=pack.source,
        sha256=pack.sha256,
        installed_at=pack.installed_at.replace(tzinfo=UTC),
        state=pack.state,
        reason=pack.reason,
        lessons=[
            LessonStateRead(
                id=lesson.lesson_id,
                title=lesson.title,
                state=lesson.state,
                failing_ply=lesson.failing_ply,
                reason=lesson.reason,
            )
            for lesson in _lessons(pack, session)
            if pack.state != "unavailable"
        ],
    )


def _enqueue(request: Request, engine: Engine, pack_id: UUID) -> None:
    tasks = cast(dict[UUID, asyncio.Task[None]], request.app.state.lesson_pack_tasks)
    lock = cast(asyncio.Lock, request.app.state.lesson_pack_lock)
    manager = cast(EnginePoolManager | None, request.app.state.engine_pool_manager)

    async def run() -> None:
        # Packs share one batch worker, leaving other batch engines to game analysis.
        async with lock:
            await verify_pack(engine, pack_id, manager)

    task = asyncio.create_task(run())
    tasks[pack_id] = task

    def finished(done: asyncio.Task[None]) -> None:
        if tasks.get(pack_id) is done:
            del tasks[pack_id]
        if not done.cancelled():
            done.result()

    task.add_done_callback(finished)


async def cancel_pack_verifications(request: Request, pack_ids: Sequence[UUID]) -> None:
    """Release this owner's verification leases after the deletion transaction."""
    tasks = cast(dict[UUID, asyncio.Task[None]], request.app.state.lesson_pack_tasks)
    cancelled = [tasks[pack_id] for pack_id in pack_ids if pack_id in tasks]
    for task in cancelled:
        task.cancel()
    await asyncio.gather(*cancelled, return_exceptions=True)


def _write_owner(request: Request, session: Session) -> str:
    player_id = require_authenticated_player(request, session)
    lock_puzzle_player(player_id, session)
    session.expire_all()
    return require_authenticated_player(request, session)


@router.post("", response_model=PackRead, status_code=201)
async def install_pack(request: Request, session: Session = Depends(get_session)) -> PackRead:
    # The route-specific middleware bounds bytes before JSON parsing.
    require_authenticated_player(request, session)
    session.rollback()
    raw = await request.body()
    try:
        document = parse_pack(raw)
    except (ValueError, ValidationError) as error:
        detail = str(error).encode("utf-8", errors="backslashreplace").decode("utf-8")
        raise HTTPException(status_code=422, detail=detail) from None
    player_id = _write_owner(request, session)
    if (
        session.exec(
            select(LessonPack.id).where(
                LessonPack.player_id == player_id, LessonPack.pack_id == document.id
            )
        ).first()
        is not None
    ):
        raise HTTPException(status_code=409, detail="Pack id already installed; uninstall it first")
    installed = session.exec(
        select(func.count()).select_from(LessonPack).where(LessonPack.player_id == player_id)
    ).one()
    if installed >= MAX_INSTALLED_PACKS:
        raise HTTPException(
            status_code=409,
            detail="At most 20 lesson packs can be installed per player; uninstall a pack first",
        )
    pack = LessonPack(
        player_id=player_id,
        pack_id=document.id,
        title=document.title,
        author=document.author,
        licence=document.licence,
        version=document.version,
        description=document.description,
        source=document.source,
        sha256=sha256(raw).hexdigest(),
    )
    session.add(pack)
    session.flush()
    for lesson in document.lessons:
        session.add(
            PackLesson(
                pack_id=pack.id,
                lesson_id=lesson.id,
                title=lesson.title,
                payload=lesson.model_dump(),
            )
        )
    installed_id = pack.id
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="Pack id already installed; uninstall it first"
        ) from None
    result = _read(pack, session)
    engine = session.get_bind()
    session.commit()
    _enqueue(request, cast(Engine, engine), installed_id)
    return result


@router.get("", response_model=list[PackRead])
def list_packs(request: Request, session: Session = Depends(get_session)) -> list[PackRead]:
    player_id = require_authenticated_player(request, session)
    packs = session.exec(
        select(LessonPack)
        .where(LessonPack.player_id == player_id)
        .order_by(col(LessonPack.installed_at))
    ).all()
    return [_read(pack, session) for pack in packs]


@router.get("/{pack_id}", response_model=PackRead)
def read_pack(pack_id: str, request: Request, session: Session = Depends(get_session)) -> PackRead:
    return _read(_owned(pack_id, require_authenticated_player(request, session), session), session)


@router.post("/{pack_id}/retry", response_model=PackRead)
async def retry_pack(
    pack_id: str, request: Request, session: Session = Depends(get_session)
) -> PackRead:
    pack = _owned(pack_id, _write_owner(request, session), session)
    if pack.state not in {"failed", "unavailable"}:
        raise HTTPException(
            status_code=409, detail="Only failed or unavailable verification can retry"
        )
    pack.state, pack.reason = "pending", None
    session.add(pack)
    for lesson in _lessons(pack, session):
        lesson.state, lesson.failing_ply, lesson.reason = "pending", None, None
        session.add(lesson)
    session.commit()
    result = _read(pack, session)
    engine = session.get_bind()
    installed_id = pack.id
    session.commit()
    _enqueue(request, cast(Engine, engine), installed_id)
    return result


@router.delete("/{pack_id}", status_code=204)
async def uninstall_pack(
    pack_id: str, request: Request, session: Session = Depends(get_session)
) -> Response:
    pack = _owned(pack_id, _write_owner(request, session), session)
    installed_id = pack.id
    session.exec(delete(PackLesson).where(col(PackLesson.pack_id) == pack.id))
    session.delete(pack)
    session.commit()
    await cancel_pack_verifications(request, [installed_id])
    return Response(status_code=204)


def _playable(
    pack_id: str, lesson_id: str, request: Request, session: Session
) -> tuple[LessonPack, PackLessonDocument]:
    pack = _owned(pack_id, require_authenticated_player(request, session), session)
    lesson = session.exec(
        select(PackLesson).where(PackLesson.pack_id == pack.id, PackLesson.lesson_id == lesson_id)
    ).one_or_none()
    if lesson is None or pack.state != "verified" or lesson.state != "verified":
        raise HTTPException(status_code=404, detail="Verified pack lesson not found")
    return pack, PackLessonDocument.model_validate(lesson.payload)


@router.get("/{pack_id}/lessons/{lesson_id}", response_model=LessonRead)
def get_lesson(
    pack_id: str, lesson_id: str, request: Request, session: Session = Depends(get_session)
) -> LessonRead:
    pack, lesson = _playable(pack_id, lesson_id, request, session)
    return LessonRead(
        id=lesson.id,
        title=lesson.title,
        pack_id=pack.pack_id,
        pack_title=pack.title,
        author=pack.author,
        fen=lesson.fen,
        objective=lesson.objective,
        themes=lesson.themes,
    )


@router.post("/{pack_id}/lessons/{lesson_id}/check", response_model=MoveRead)
def check_move(
    pack_id: str,
    lesson_id: str,
    body: MoveCheck,
    request: Request,
    session: Session = Depends(get_session),
) -> MoveRead:
    _, lesson = _playable(pack_id, lesson_id, request, session)
    if body.ply >= len(lesson.solution):
        raise HTTPException(status_code=422, detail="Solver ply is outside the lesson")
    board = chess.Board(lesson.fen)
    for move in lesson.solution[: body.ply]:
        board.push_uci(move)
    if body.hint:
        return MoveRead(
            accepted=False,
            fen=board.fen(),
            ply=body.ply,
            reply=None,
            complete=False,
            hints=lesson.hints,
            explanation=None,
        )
    if body.move != lesson.solution[body.ply]:
        return MoveRead(
            accepted=False,
            fen=board.fen(),
            ply=body.ply,
            reply=None,
            complete=False,
            hints=[],
            explanation=None,
        )
    board.push_uci(body.move)
    next_ply = body.ply + 1
    reply = lesson.solution[next_ply] if next_ply < len(lesson.solution) else None
    if reply is not None:
        board.push_uci(reply)
        next_ply += 1
    complete = next_ply == len(lesson.solution)
    return MoveRead(
        accepted=True,
        fen=board.fen(),
        ply=next_ply,
        reply=reply,
        complete=complete,
        hints=[],
        explanation=lesson.explanation if complete else None,
    )

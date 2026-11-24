"""Verify a pack with one borrowed engine, without writing trusted learning data."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import chess
from sqlalchemy import Engine, update
from sqlmodel import Session, col, select

from scan64.content.validate import validate_solution_line
from scan64.learning.exercises.sound_moves import analyze_lesson_source, sound_moves
from scan64.lesson_packs.models import LessonPack, LessonState, PackLesson, PackState
from scan64.lesson_packs.schema import PackLessonDocument
from scan64.providers.stockfish.pool import EnginePoolManager


def _reset_lessons(session: Session, pack_id: UUID) -> None:
    session.execute(
        update(PackLesson)
        .where(col(PackLesson.pack_id) == pack_id)
        .values(state="pending", failing_ply=None, reason=None)
    )


def fail_interrupted_packs(engine: Engine) -> None:
    """Startup never makes a partly verified pack playable, even after a crash."""
    with Session(engine) as session:
        interrupted = session.exec(
            select(LessonPack).where(col(LessonPack.state).in_(("pending", "verifying")))
        ).all()
        for pack in interrupted:
            _reset_lessons(session, pack.id)
            pack.state = "failed"
            pack.reason = "Verification interrupted by restart. Retry to verify this pack."
        session.commit()


def _start_pack(engine: Engine, pack_id: UUID) -> list[tuple[UUID, dict[str, Any]]] | None:
    with Session(engine) as session:
        pack = session.get(LessonPack, pack_id)
        if pack is None or pack.state != "pending":
            return None
        pack.state = "verifying"
        pack.reason = None
        _reset_lessons(session, pack_id)
        lessons = [
            (lesson.id, dict(lesson.payload))
            for lesson in session.exec(
                select(PackLesson)
                .where(col(PackLesson.pack_id) == pack_id)
                .order_by(PackLesson.lesson_id)
            ).all()
        ]
        session.commit()
        return lessons


def _is_verifying(engine: Engine, pack_id: UUID) -> bool:
    with Session(engine) as session:
        pack = session.get(LessonPack, pack_id)
        return pack is not None and pack.state == "verifying"


def _store_result(
    engine: Engine,
    pack_id: UUID,
    lesson_id: UUID,
    state: LessonState,
    failing_ply: int | None = None,
    reason: str | None = None,
) -> bool:
    with Session(engine) as session:
        # Fetch fresh after every engine await. UPDATE only: an uninstall must
        # never be undone by adding a stale, detached object back to the session.
        pack = session.get(LessonPack, pack_id)
        if pack is None or pack.state != "verifying":
            return False
        session.execute(
            update(PackLesson)
            .where(col(PackLesson.id) == lesson_id, col(PackLesson.pack_id) == pack_id)
            .values(state=state, failing_ply=failing_ply, reason=reason)
        )
        session.commit()
        return True


def _finish_pack(
    engine: Engine, pack_id: UUID, state: PackState, reason: str | None = None
) -> None:
    with Session(engine) as session:
        session.execute(
            update(LessonPack)
            .where(col(LessonPack.id) == pack_id, col(LessonPack.state) == "verifying")
            .values(state=state, reason=reason)
        )
        session.commit()


def _line_error(lesson: PackLessonDocument) -> tuple[int | None, str | None]:
    error = validate_solution_line(lesson.fen, lesson.solution)
    if error is not None:
        board = chess.Board(lesson.fen)
        for ply, uci in enumerate(lesson.solution, start=1):
            try:
                move = chess.Move.from_uci(uci)
            except ValueError:
                return ply, error
            if move not in board.legal_moves:
                return ply, error
            board.push(move)
        return None, error
    if len(lesson.solution) % 2 != 1:
        return len(lesson.solution), "Solution line must end on a solver move"
    return None, None


async def verify_pack(engine: Engine, pack_id: UUID, manager: EnginePoolManager | None) -> None:
    """Caller serializes pack jobs; no DB session is retained across an await.

    The public batch_adapter facade leases per search, so acquire the underlying
    batch pool once instead: MultiPV widening and every solver ply share one
    borrowed engine while all other batch engines remain available to analysis.
    """
    lessons = _start_pack(engine, pack_id)
    if lessons is None:
        return
    if manager is None:
        _finish_pack(engine, pack_id, "unavailable", "Verification unavailable: no engine pool.")
        return
    acquired = False
    try:
        async with manager.batch_pool.acquire() as adapter:
            acquired = True
            for lesson_id, payload in lessons:
                if not _is_verifying(engine, pack_id):
                    return
                lesson = PackLessonDocument.model_validate(payload)
                failing_ply, reason = _line_error(lesson)
                if reason is not None:
                    if not _store_result(
                        engine, pack_id, lesson_id, "rejected", failing_ply, reason
                    ):
                        return
                    continue
                board = chess.Board(lesson.fen)
                for ply, uci in enumerate(lesson.solution):
                    move = chess.Move.from_uci(uci)
                    if ply % 2 == 0:
                        if not _is_verifying(engine, pack_id):
                            return
                        analysis = await analyze_lesson_source(adapter, board.fen())
                        if move not in sound_moves(board, analysis):
                            failing_ply = ply + 1
                            reason = "Pack move is not an engine-confirmed sound move"
                            break
                    board.push(move)
                if not _store_result(
                    engine,
                    pack_id,
                    lesson_id,
                    "verified" if reason is None else "rejected",
                    failing_ply,
                    reason,
                ):
                    return
        _finish_pack(engine, pack_id, "verified")
    except asyncio.CancelledError:
        _finish_pack(engine, pack_id, "failed", "Verification interrupted. Retry this pack.")
        raise
    except Exception as error:
        state: PackState = "unavailable" if not acquired else "failed"
        label = "Verification unavailable" if state == "unavailable" else "Verification failed"
        _finish_pack(engine, pack_id, state, f"{label}: {type(error).__name__}: {error}"[:2000])

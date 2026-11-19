"""Owned review-position practice and transactional terminal observations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import chess
from pydantic import BaseModel, StrictInt
from sqlalchemy import CheckConstraint, String, UniqueConstraint
from sqlmodel import Field, Session, SQLModel, col, select

from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview
from scan64.chess.boards import board_from
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule, _as_utc

WINNING_CENTIPAWNS = 300


class SparringAttempt(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("play_session_id", name="uq_sparring_play_session"),
        CheckConstraint("source_ply >= 0", name="ck_sparring_source_ply"),
        CheckConstraint(
            "verification_status IN ('verified', 'historical')",
            name="ck_sparring_verification",
        ),
        CheckConstraint(
            "(completed_at IS NULL AND result IS NULL AND outcome IS NULL) OR "
            "(completed_at IS NOT NULL AND result IS NOT NULL AND outcome IS NOT NULL "
            "AND result IN ('1-0', '0-1', '1/2-1/2') "
            "AND outcome IN ('win', 'draw', 'loss'))",
            name="ck_sparring_completion",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    player_id: str = Field(index=True)
    source_review_id: UUID = Field(foreign_key="gamemovereview.id", index=True)
    source_position_id: UUID = Field(foreign_key="position.id")
    source_analysis_id: UUID = Field(foreign_key="engineanalysis.id")
    play_session_id: UUID = Field(foreign_key="playsession.id")
    source_ply: StrictInt = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    result: str | None = None
    outcome: Literal["win", "draw", "loss"] | None = Field(default=None, sa_type=String)
    verification_status: Literal["verified", "historical"] = Field(
        default="verified", sa_type=String
    )


class SparringSourceRequest(BaseModel):
    game_id: UUID
    ply: StrictInt = Field(ge=0)


class SparringAttemptRead(BaseModel):
    id: UUID
    play_session_id: UUID
    source_game_id: UUID
    source_review_id: UUID
    source_position_id: UUID
    source_analysis_id: UUID
    source_ply: int
    source_fen: str
    player_color: Literal["white", "black"]
    seed: str
    completed_at: datetime | None
    result: str | None
    outcome: Literal["win", "draw", "loss"] | None
    verification_status: Literal["verified", "historical"]
    next_review_at: datetime | None


class SparringSourceNotFound(LookupError):
    pass


class IneligibleSparringSource(ValueError):
    pass


@dataclass(frozen=True)
class SparringSource:
    game: Game
    review: GameMoveReview
    position: Position
    analysis: EngineAnalysis
    ply: int
    board: chess.Board


def source_item_id(review_id: UUID, position_id: UUID) -> str:
    return f"sparring:{review_id}:{position_id}"


def _legal_move(board: chess.Board, uci: str) -> chess.Move:
    move = board.parse_uci(uci)
    if not move:
        raise ValueError("Null moves are not legal sparring provenance")
    return move


def winning_position(
    game: Game, board: chess.Board, position: Position | None, analysis: EngineAnalysis | None
) -> bool:
    """Scores are side-to-move POV, not the reviewed move's mover POV."""
    if (
        game.owner_player_id is None
        or game.owner_color not in {"white", "black"}
        or position is None
        or analysis is None
        or position.game_id != game.id
        or position.fen != board.fen()
        or analysis.position_id != position.id
        or not analysis.raw_result
    ):
        return False
    line = analysis.raw_result[0]
    if not isinstance(line, dict):
        return False
    direction = 1 if board.turn == (game.owner_color == "white") else -1
    mate = line.get("score_mate")
    score = line.get("score_cp")
    if mate is not None:
        winning = type(mate) is int and direction * mate > 0
    else:
        winning = (
            isinstance(score, (int, float))
            and not isinstance(score, bool)
            and (isinstance(score, int) or math.isfinite(score))
            and direction * score >= WINNING_CENTIPAWNS
        )
    if not winning or not board.is_valid() or board.is_game_over(claim_draw=False):
        return False
    pv = line.get("pv")
    if not isinstance(pv, list) or not pv or not isinstance(pv[0], str):
        return False
    try:
        _legal_move(board, pv[0])
    except ValueError:
        return False
    return True


def load_source(session: Session, player_id: str, request: SparringSourceRequest) -> SparringSource:
    game = session.get(Game, request.game_id)
    if game is None or game.owner_player_id != player_id:
        raise SparringSourceNotFound("Review game not found")
    row = session.exec(
        select(GameMoveReview).where(
            GameMoveReview.game_id == game.id, GameMoveReview.ply == max(1, request.ply)
        )
    ).first()
    if row is None or not game.moves or request.ply > len(game.moves):
        raise IneligibleSparringSource("Stored review position is unavailable")
    source = session.get(Position, row.source_position_id)
    reached = session.get(Position, row.reached_position_id)
    try:
        board = board_from(game.headers.get("FEN"))
        for uci in game.moves[: row.ply - 1]:
            board.push(_legal_move(board, uci))
        move = _legal_move(board, game.moves[row.ply - 1])
        if (
            source is None
            or reached is None
            or source.game_id != game.id
            or reached.game_id != game.id
            or source.fen != board.fen()
            or row.move_uci != move.uci()
            or row.san != board.san(move)
            or row.mover != ("white" if board.turn else "black")
        ):
            raise IneligibleSparringSource("Review does not match its owned replay")
        before = board.copy()
        board.push(move)
        if reached.fen != board.fen():
            raise IneligibleSparringSource("Review reached position does not match its replay")
    except (ValueError, IndexError) as error:
        raise IneligibleSparringSource("Review does not match its owned replay") from error
    position = source if request.ply == 0 else reached
    analysis_id = row.before_analysis_id if request.ply == 0 else row.after_analysis_id
    analysis = session.get(EngineAnalysis, analysis_id)
    if request.ply == 0:
        board = before
    if not winning_position(game, board, position, analysis):
        raise IneligibleSparringSource("Position has no stored winning evaluation for its owner")
    assert analysis is not None
    return SparringSource(game, row, position, analysis, request.ply, board)


def complete_sparring(session: Session, play_session: PlaySession, game: Game, result: str) -> None:
    """Called only inside the successful conditional PlaySession completion write."""
    attempt = session.exec(
        select(SparringAttempt).where(SparringAttempt.play_session_id == play_session.id)
    ).first()
    # Reset deliberately deletes this link while preserving the ordinary play session.
    if attempt is None or attempt.completed_at is not None:
        return
    if (
        attempt.player_id != play_session.player_id
        or game.owner_player_id != attempt.player_id
        or game.owner_color not in {"white", "black"}
        or result not in {"1-0", "0-1", "1/2-1/2"}
    ):
        raise ValueError("Sparring terminal ownership or result is invalid")
    owner_result = "1-0" if game.owner_color == "white" else "0-1"
    outcome: Literal["win", "draw", "loss"] = (
        "draw" if result == "1/2-1/2" else "win" if result == owner_result else "loss"
    )
    now = datetime.now(UTC)
    attempt.completed_at = now
    attempt.result = result
    attempt.outcome = outcome
    session.add(attempt)
    if attempt.verification_status != "verified":
        return
    item_id = source_item_id(attempt.source_review_id, attempt.source_position_id)
    schedule = session.get(ReviewSchedule, (attempt.player_id, item_id))
    if schedule is None:
        schedule = ReviewSchedule(player_id=attempt.player_id, item_id=item_id, next_review_at=now)
    schedule.update(outcome == "win", now)
    session.add(schedule)


def _read_attempt(
    attempt: SparringAttempt,
    play_session: PlaySession,
    review: GameMoveReview,
    position: Position,
    game: Game,
    schedule: ReviewSchedule | None,
) -> SparringAttemptRead:
    if game.owner_color not in {"white", "black"}:
        raise ValueError("Sparring source owner colour is unavailable")
    return SparringAttemptRead(
        id=attempt.id,
        play_session_id=play_session.id,
        source_game_id=review.game_id,
        source_review_id=review.id,
        source_position_id=position.id,
        source_analysis_id=attempt.source_analysis_id,
        source_ply=attempt.source_ply,
        source_fen=position.fen,
        player_color="white" if game.owner_color == "white" else "black",
        seed=play_session.opponent_config["seed"],
        completed_at=_as_utc(attempt.completed_at) if attempt.completed_at else None,
        result=attempt.result,
        outcome=attempt.outcome,
        verification_status=attempt.verification_status,
        next_review_at=_as_utc(schedule.next_review_at) if schedule else None,
    )


def read_sparring(session: Session, play_session: PlaySession) -> SparringAttemptRead | None:
    attempt = session.exec(
        select(SparringAttempt).where(SparringAttempt.play_session_id == play_session.id)
    ).first()
    if attempt is None:
        return None
    review = session.get(GameMoveReview, attempt.source_review_id)
    position = session.get(Position, attempt.source_position_id)
    game = session.get(Game, review.game_id) if review else None
    if review is None or position is None or game is None:
        raise ValueError("Sparring source linkage is incomplete")
    schedule = session.get(
        ReviewSchedule,
        (attempt.player_id, source_item_id(attempt.source_review_id, attempt.source_position_id)),
    )
    return _read_attempt(attempt, play_session, review, position, game, schedule)


def project_sparring(
    session: Session, game: Game, rows: dict[int, GameMoveReview], positions: dict[UUID, Position]
) -> dict[int, SparringAttemptRead]:
    attempts = session.exec(
        select(SparringAttempt)
        .join(GameMoveReview, col(SparringAttempt.source_review_id) == col(GameMoveReview.id))
        .where(GameMoveReview.game_id == game.id, SparringAttempt.player_id == game.owner_player_id)
        .order_by(col(SparringAttempt.created_at).desc(), col(SparringAttempt.id).desc())
    ).all()
    latest: dict[int, SparringAttempt] = {}
    for attempt in attempts:
        latest.setdefault(attempt.source_ply, attempt)
    if not latest:
        return {}
    sessions = {
        item.id: item
        for item in session.exec(
            select(PlaySession).where(
                col(PlaySession.id).in_([attempt.play_session_id for attempt in latest.values()])
            )
        ).all()
    }
    schedules = {
        item.item_id: item
        for item in session.exec(
            select(ReviewSchedule).where(
                ReviewSchedule.player_id == game.owner_player_id,
                col(ReviewSchedule.item_id).in_(
                    [
                        source_item_id(a.source_review_id, a.source_position_id)
                        for a in latest.values()
                    ]
                ),
            )
        ).all()
    }
    reviews = {row.id: row for row in rows.values()}
    projected = {}
    for ply, attempt in latest.items():
        play_session = sessions.get(attempt.play_session_id)
        review = reviews.get(attempt.source_review_id)
        position = positions.get(attempt.source_position_id)
        if play_session is None or review is None or position is None:
            raise ValueError("Sparring source linkage is incomplete")
        projected[ply] = _read_attempt(
            attempt,
            play_session,
            review,
            position,
            game,
            schedules.get(source_item_id(attempt.source_review_id, attempt.source_position_id)),
        )
    return projected

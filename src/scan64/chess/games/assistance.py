"""Durable hint provenance and shared play-session snapshot serialization."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

import chess
from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import JSON, Column, Field, Session, SQLModel, col, select

from scan64.chess.games.models import Game, PlaySession


class SessionHintReveal(SQLModel, table=True):
    session_id: UUID = Field(primary_key=True, foreign_key="playsession.id")
    player_id: str = Field(index=True)
    prefix_hash: str = Field(primary_key=True)
    canonical_id: str
    ply: int
    initial_fen: str
    moves: list[str] = Field(sa_column=Column(JSON, nullable=False))
    highest_step: int


def prefix_hash(initial_fen: str, moves: list[str]) -> str:
    """Hash the full initial position and ordered UCI prefix, including counters."""
    encoded = json.dumps([initial_fen, moves], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


class StalePlaySnapshot(ValueError):
    """A concurrent play edit invalidated work computed from a snapshot."""


@dataclass(frozen=True)
class PlaySnapshot:
    session_id: UUID
    player_id: str
    game_id: UUID | None
    config: dict[str, str]
    initial_fen: str
    headers: dict[str, str]
    moves: list[str]

    @classmethod
    def capture(cls, play: PlaySession, game: Game | None) -> "PlaySnapshot":
        return cls(
            play.id, play.player_id, play.game_id, dict(play.opponent_config),
            game.headers.get("FEN", chess.STARTING_FEN) if game else chess.STARTING_FEN,
            dict(game.headers) if game else {}, list(game.moves) if game else [],
        )


def claim_snapshot(session: Session, snapshot: PlaySnapshot) -> dict[str, str]:
    """Acquire the session write lock and CAS the complete source snapshot.

    The server-only generation prevents an ABA takeback/replay from making an
    old engine response current again. All move and hint writes use this lock;
    completion writes the same session row.
    """
    config = dict(snapshot.config)
    config["assistance_generation"] = str(int(config.get("assistance_generation", "0")) + 1)
    statement = (
        update(PlaySession)
        .where(col(PlaySession.id) == snapshot.session_id)
        .where(col(PlaySession.player_id) == snapshot.player_id)
        .where(col(PlaySession.status) == "active")
        .where(col(PlaySession.opponent_config) == snapshot.config)
    )
    if snapshot.game_id is None:
        statement = statement.where(col(PlaySession.game_id).is_(None))
    else:
        statement = statement.where(col(PlaySession.game_id) == snapshot.game_id).where(
            col(PlaySession.game_id).in_(
                select(Game.id)
                .where(col(Game.id) == snapshot.game_id)
                .where(col(Game.result) == "*")
                .where(col(Game.headers) == snapshot.headers)
                .where(col(Game.moves) == snapshot.moves)
            )
        )
    with session.no_autoflush:
        changed = session.exec(statement.values(opponent_config=config).execution_options(
            synchronize_session=False
        ))
    if changed.rowcount != 1:
        session.rollback()
        raise StalePlaySnapshot("PlaySession changed while generating the hint or move")
    play = session.get(PlaySession, snapshot.session_id)
    if play is not None:
        set_committed_value(play, "opponent_config", config)
    return config


def persist_hint_reveal(
    session: Session, snapshot: PlaySnapshot, board: chess.Board, step: int
) -> None:
    """Commit provenance before any generated hint is disclosed."""
    claim_snapshot(session, snapshot)
    digest = prefix_hash(snapshot.initial_fen, snapshot.moves)
    reveal = session.get(SessionHintReveal, (snapshot.session_id, digest))
    if reveal is None:
        reveal = SessionHintReveal(
            session_id=snapshot.session_id, player_id=snapshot.player_id,
            prefix_hash=digest, canonical_id=board.epd(), ply=len(snapshot.moves) + 1,
            initial_fen=snapshot.initial_fen, moves=snapshot.moves, highest_step=step,
        )
    else:
        reveal.highest_step = max(reveal.highest_step, step)
    session.add(reveal)
    session.commit()

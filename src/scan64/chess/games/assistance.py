"""Durable engine-help provenance and shared Play snapshot serialization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import chess
from pydantic import StrictBool
from sqlalchemy import Boolean, func, update
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import JSON, Column, Field, Session, SQLModel, col, select

from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.clocks import is_expired, utc_now
from scan64.chess.games.models import Game, PlaySession

AssistanceMode = Literal["hint", "candidates", "guard"]


class SessionHintReveal(SQLModel, table=True):
    """Per-prefix hint steps and explicit Play disclosures, retained across takebacks."""

    session_id: UUID = Field(primary_key=True, foreign_key="playsession.id")
    player_id: str = Field(index=True)
    prefix_hash: str = Field(primary_key=True)
    canonical_id: str
    ply: int
    initial_fen: str
    moves: list[str] = Field(sa_column=Column(JSON, nullable=False))
    highest_step: int
    candidate_used: StrictBool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default="0")
    )
    guard_used: StrictBool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default="0")
    )

    @property
    def effective_step(self) -> int:
        """Candidate grading and guard verdicts disclose full engine assessments."""
        return 3 if self.candidate_used or self.guard_used else self.highest_step


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
    def capture(cls, play: PlaySession, game: Game | None) -> PlaySnapshot:
        return cls(
            play.id,
            play.player_id,
            play.game_id,
            dict(play.opponent_config),
            game.headers.get("FEN", chess.STARTING_FEN) if game else chess.STARTING_FEN,
            dict(game.headers) if game else {},
            list(game.moves) if game else [],
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
        changed = session.exec(
            statement.values(opponent_config=config).execution_options(synchronize_session=False)
        )
    if changed.rowcount != 1:
        session.rollback()
        raise StalePlaySnapshot("PlaySession changed while generating the hint or move")
    play = session.get(PlaySession, snapshot.session_id)
    if play is not None:
        set_committed_value(play, "opponent_config", config)
    return config


def persist_assistance(
    session: Session,
    snapshot: PlaySnapshot,
    board: chess.Board,
    step: int,
    *,
    mode: AssistanceMode = "hint",
) -> int:
    """Commit exact-prefix provenance before disclosing help; return its generation."""
    if (mode == "hint" and step not in (1, 2, 3)) or (
        mode != "hint" and (mode not in ("candidates", "guard") or step != 0)
    ):
        raise ValueError("Invalid assistance disclosure")
    config = claim_snapshot(session, snapshot)
    clock = session.get(PlayClock, snapshot.session_id)
    if clock is not None and is_expired(clock, utc_now()):
        session.rollback()
        raise StalePlaySnapshot("PlaySession clock expired while generating assistance")
    digest = prefix_hash(snapshot.initial_fen, snapshot.moves)
    reveal = session.get(SessionHintReveal, (snapshot.session_id, digest))
    if reveal is None:
        reveal = SessionHintReveal(
            session_id=snapshot.session_id,
            player_id=snapshot.player_id,
            prefix_hash=digest,
            canonical_id=board.epd(),
            ply=len(snapshot.moves) + 1,
            initial_fen=snapshot.initial_fen,
            moves=snapshot.moves,
            highest_step=step,
            candidate_used=mode == "candidates",
            guard_used=mode == "guard",
        )
    else:
        reveal.highest_step = max(reveal.highest_step, step)
        reveal.candidate_used = reveal.candidate_used or mode == "candidates"
        reveal.guard_used = reveal.guard_used or mode == "guard"
    session.add(reveal)
    session.commit()
    return int(config["assistance_generation"])


def assistance_modes(session: Session, session_id: UUID) -> list[AssistanceMode]:
    """Project persisted source flags without loading each prefix's move history."""
    hint_step, candidate_used, guard_used = session.exec(
        select(
            func.max(SessionHintReveal.highest_step),
            func.max(SessionHintReveal.candidate_used),
            func.max(SessionHintReveal.guard_used),
        ).where(col(SessionHintReveal.session_id) == session_id)
    ).one()
    modes: list[AssistanceMode] = []
    if hint_step:
        modes.append("hint")
    if candidate_used:
        modes.append("candidates")
    if guard_used:
        modes.append("guard")
    return modes

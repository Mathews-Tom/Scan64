from __future__ import annotations

import math
import uuid

from sqlalchemy import Index, func
from sqlmodel import Field, Session, SQLModel, col, select


class TransferPosition(SQLModel, table=True):
    """A curated position retrievable as transfer practice for one skill."""

    __table_args__ = (Index("ix_transfer_position_skill_difficulty", "skill_id", "difficulty"),)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    skill_id: str
    difficulty: float
    fen: str
    opening: str
    board_side: str
    attacking_piece: str
    material_count: int
    move_number: int


class TransferRetrievalError(ValueError):
    """Raised when a transfer-position retrieval request is invalid."""


def retrieve_positions_by_motif_and_difficulty(
    session: Session,
    skill_id: str,
    target_difficulty: float,
    difficulty_tolerance: float,
    limit: int = 20,
) -> list[TransferPosition]:
    """Return closest matching positions for a skill within a difficulty window."""
    _validate_retrieval_request(skill_id, target_difficulty, difficulty_tolerance, limit)

    minimum_difficulty = target_difficulty - difficulty_tolerance
    maximum_difficulty = target_difficulty + difficulty_tolerance
    statement = (
        select(TransferPosition)
        .where(TransferPosition.skill_id == skill_id)
        .where(TransferPosition.difficulty >= minimum_difficulty)
        .where(TransferPosition.difficulty <= maximum_difficulty)
        .order_by(
            func.abs(col(TransferPosition.difficulty) - target_difficulty),
            col(TransferPosition.difficulty),
            col(TransferPosition.id),
        )
        .limit(limit)
    )
    return list(session.exec(statement))


def _validate_retrieval_request(
    skill_id: str,
    target_difficulty: float,
    difficulty_tolerance: float,
    limit: int,
) -> None:
    if not skill_id.strip():
        raise TransferRetrievalError("skill_id must not be empty")
    if not math.isfinite(target_difficulty):
        raise TransferRetrievalError("target_difficulty must be finite")
    if not math.isfinite(difficulty_tolerance) or difficulty_tolerance < 0:
        raise TransferRetrievalError("difficulty_tolerance must be finite and non-negative")
    if limit < 1:
        raise TransferRetrievalError("limit must be positive")

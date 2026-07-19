from __future__ import annotations

import math
import uuid

from chess import Board, Move, square_mirror
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


class PositionTransformationError(ValueError):
    """Raised when a position cannot support a verified transformation."""


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


def mirror_and_swap_colours(fen: str) -> str:
    """Mirror ranks and swap colours while retaining a legal chess position."""
    return _validated_board(fen).mirror().fen()


def mirror_move(move: Move) -> Move:
    """Map a move into the rank-mirrored, colour-swapped position."""
    return Move(
        square_mirror(move.from_square),
        square_mirror(move.to_square),
        promotion=move.promotion,
        drop=move.drop,
    )


def verify_mirror_preserves_legal_moves(fen: str) -> None:
    """Raise unless rank mirroring and colour swap preserve every legal move."""
    source_board = _validated_board(fen)
    transformed_board = Board(mirror_and_swap_colours(fen))
    expected_moves = {mirror_move(move) for move in source_board.legal_moves}
    transformed_moves = set(transformed_board.legal_moves)
    if expected_moves != transformed_moves:
        raise PositionTransformationError(
            "Mirrored position does not preserve the source legal-move relationship"
        )


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


def _validated_board(fen: str) -> Board:
    try:
        board = Board(fen)
    except ValueError as error:
        raise PositionTransformationError(f"Invalid FEN: {error}") from error

    if not board.is_valid():
        raise PositionTransformationError("FEN does not represent a legal chess position")
    return board

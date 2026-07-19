from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from enum import StrEnum

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


class TransferClassificationError(ValueError):
    """Raised when transfer metadata does not satisfy the requested classification."""


class TransferKind(StrEnum):
    """The pedagogical distance between a source and target exercise."""

    NEAR = "near"
    FAR = "far"


class TransferVariation(StrEnum):
    """A superficial feature intentionally varied for a far-transfer exercise."""

    OPENING = "opening"
    BOARD_SIDE = "board_side"
    ATTACKING_PIECE = "attacking_piece"
    MATERIAL_COUNT = "material_count"
    MOVE_NUMBER = "move_number"


@dataclass(frozen=True)
class TransferExercise:
    """A generated transfer exercise with auditable distance metadata."""

    skill_id: str
    source_position_id: str
    target_position_id: str | None
    source_fen: str
    target_fen: str
    transfer_kind: TransferKind
    variations: frozenset[TransferVariation]


def generate_near_transfer_exercise(source: TransferPosition) -> TransferExercise:
    """Generate a verified mirror-based near-transfer exercise."""
    verify_mirror_preserves_legal_moves(source.fen)
    return TransferExercise(
        skill_id=source.skill_id,
        source_position_id=source.id,
        target_position_id=None,
        source_fen=source.fen,
        target_fen=mirror_and_swap_colours(source.fen),
        transfer_kind=TransferKind.NEAR,
        variations=frozenset(),
    )


def generate_far_transfer_exercise(
    skill_id: str,
    source: TransferPosition,
    target: TransferPosition,
) -> TransferExercise:
    """Generate a far-transfer exercise only when two listed features differ."""
    if not skill_id.strip():
        raise TransferClassificationError("skill_id must not be empty")
    if source.skill_id != skill_id or target.skill_id != skill_id:
        raise TransferClassificationError("Both positions must match skill_id")
    _validate_far_position(source, "source")
    _validate_far_position(target, "target")
    if source.fen == target.fen:
        raise TransferClassificationError("Far transfer requires a distinct target position")

    variations = _transfer_variations(source, target)
    if len(variations) < 2:
        raise TransferClassificationError(
            "Far transfer requires at least two varied superficial characteristics"
        )

    return TransferExercise(
        skill_id=skill_id,
        source_position_id=source.id,
        target_position_id=target.id,
        source_fen=source.fen,
        target_fen=target.fen,
        transfer_kind=TransferKind.FAR,
        variations=variations,
    )


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


def _validate_far_position(position: TransferPosition, role: str) -> None:
    try:
        board = _validated_board(position.fen)
    except PositionTransformationError as error:
        raise TransferClassificationError(f"{role} position FEN must be legal") from error

    if position.material_count != len(board.piece_map()):
        raise TransferClassificationError(
            f"{role} material_count must match the FEN piece count"
        )
    if position.move_number != board.fullmove_number:
        raise TransferClassificationError(
            f"{role} move_number must match the FEN full-move number"
        )


def _transfer_variations(
    source: TransferPosition,
    target: TransferPosition,
) -> frozenset[TransferVariation]:
    variations: set[TransferVariation] = set()
    if source.opening != target.opening:
        variations.add(TransferVariation.OPENING)
    if source.board_side != target.board_side:
        variations.add(TransferVariation.BOARD_SIDE)
    if source.attacking_piece != target.attacking_piece:
        variations.add(TransferVariation.ATTACKING_PIECE)
    if source.material_count != target.material_count:
        variations.add(TransferVariation.MATERIAL_COUNT)
    if source.move_number != target.move_number:
        variations.add(TransferVariation.MOVE_NUMBER)
    return frozenset(variations)

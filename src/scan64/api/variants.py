"""Variant rules for the web board, from python-chess variant boards.

The web client plays standard chess with chess.js; for a variant it asks this
endpoint for the position after a move list, so legality, FEN, SAN, pockets and
check counts have one source: the same python-chess boards the server uses.
"""

from __future__ import annotations

from typing import Literal

import chess
import chess.variant
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from scan64.api.auth import require_authenticated_player
from scan64.chess.variants import VariantName, board_class, pgn_san
from scan64.persistence.database import get_session

router = APIRouter()

# A position request replays at most this many moves; a full game fits.
MAX_MOVES = 1000
PIECE_LETTERS = ("p", "n", "b", "r", "q", "k")


class VariantPositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: VariantName
    # The starting position when absent (the variant's own: horde, racing kings).
    fen: str | None = None
    # UCI moves from ``fen``; drops are ``P@e5``.
    moves: list[str] = Field(default_factory=list, max_length=MAX_MOVES)


class Pockets(BaseModel):
    white: dict[str, int]
    black: dict[str, int]


class RemainingChecks(BaseModel):
    white: int
    black: int


class VariantOutcome(BaseModel):
    result: Literal["1-0", "0-1", "1/2-1/2"]
    termination: str


class VariantPositionRead(BaseModel):
    variant: VariantName
    fen: str
    turn: Literal["white", "black"]
    # SAN of each requested move, in order.
    san: list[str]
    # Every legal move in UCI, drops included.
    legal_moves: list[str]
    check: bool
    # Crazyhouse only: pieces in hand, by lower-case letter.
    pockets: Pockets | None
    # Three-check only: checks each side still has to give.
    remaining_checks: RemainingChecks | None
    outcome: VariantOutcome | None


def variant_position(request: VariantPositionRequest) -> VariantPositionRead:
    """Replay ``moves`` on the variant board; raise ``ValueError`` for an illegal one."""
    cls = board_class(request.variant)
    board = cls() if request.fen is None else cls(request.fen)
    if not board.is_valid():
        raise ValueError(f"Invalid {request.variant} position: {board.status()!r}")
    sans = []
    for index, uci in enumerate(request.moves):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            raise ValueError(f"Move {index + 1} {uci!r} is not UCI") from None
        if not board.is_legal(move):
            raise ValueError(f"Move {index + 1} {uci} is illegal in {board.fen()}")
        sans.append(pgn_san(board, move))
        board.push(move)
    outcome = board.outcome()
    return VariantPositionRead(
        variant=request.variant,
        fen=board.fen(),
        turn="white" if board.turn == chess.WHITE else "black",
        san=sans,
        legal_moves=[move.uci() for move in board.legal_moves],
        check=board.is_check(),
        pockets=_pockets(board),
        remaining_checks=_remaining_checks(board),
        outcome=None
        if outcome is None
        else VariantOutcome(
            result=outcome.result(),  # type: ignore[arg-type]
            termination=outcome.termination.name.lower(),
        ),
    )


def _pockets(board: chess.Board) -> Pockets | None:
    if not isinstance(board, chess.variant.CrazyhouseBoard):
        return None

    def hand(color: chess.Color) -> dict[str, int]:
        pocket = board.pockets[color]
        return {
            letter: pocket.count(piece_type)
            for letter, piece_type in zip(PIECE_LETTERS, chess.PIECE_TYPES, strict=True)
            if pocket.count(piece_type)
        }

    return Pockets(white=hand(chess.WHITE), black=hand(chess.BLACK))


def _remaining_checks(board: chess.Board) -> RemainingChecks | None:
    if not isinstance(board, chess.variant.ThreeCheckBoard):
        return None
    return RemainingChecks(
        white=board.remaining_checks[chess.WHITE], black=board.remaining_checks[chess.BLACK]
    )


@router.post("/v1/variants/position", response_model=VariantPositionRead)
def read_variant_position(
    body: VariantPositionRequest, request: Request, session: Session = Depends(get_session)
) -> VariantPositionRead:
    require_authenticated_player(request, session)
    try:
        return variant_position(body)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None

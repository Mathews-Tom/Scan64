import dataclasses
from typing import Protocol

from scan64.chess.positions.models import Position


@dataclasses.dataclass
class OpponentContext:
    strength_setting: int  # Example: 0-20 for Stockfish skill level
    time_remaining_ms: int | None = None
    # Other context can be added


@dataclasses.dataclass
class MoveDecision:
    uci_move: str
    score: int | None = None  # Centipawn score if available
    time_taken_ms: int | None = None


class OpponentPolicy(Protocol):
    async def choose_move(
        self,
        position: Position,
        context: OpponentContext,
    ) -> MoveDecision: ...

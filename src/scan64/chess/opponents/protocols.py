import dataclasses
from typing import Protocol, runtime_checkable

from scan64.chess.positions.models import Position


@dataclasses.dataclass
class OpponentContext:
    strength_setting: int  # Example: 0-20 for Stockfish skill level
    time_remaining_ms: int | None = None
    bot_level: int | None = None
    seed: int = 0
    ply: int = 0
    nodes: int | None = None


@dataclasses.dataclass
class MoveDecision:
    uci_move: str
    score: int | None = None  # Centipawn score if available
    time_taken_ms: int | None = None


@runtime_checkable
class OpponentPolicy(Protocol):
    async def choose_move(
        self,
        position: Position,
        context: OpponentContext,
    ) -> MoveDecision: ...

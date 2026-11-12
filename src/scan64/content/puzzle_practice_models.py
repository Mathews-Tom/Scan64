"""Player-owned daily membership and an append-only, separate puzzle Elo ledger."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

POLICY_VERSION = "puzzle-elo-v1"
THEME_MAP_VERSION = "puzzle-theme-heuristic-v1"
INITIAL_RATING = 1200.0
K_FACTOR = 32.0


class PuzzleRating(SQLModel, table=True):
    player_id: str = Field(primary_key=True, foreign_key="player.id")
    rating: float = INITIAL_RATING
    rated_count: int = 0


class PuzzleDailySet(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("player_id", "utc_day"),)
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    player_id: str = Field(foreign_key="player.id", index=True)
    utc_day: str
    policy_version: str = POLICY_VERSION
    theme_map_version: str = THEME_MAP_VERSION
    player_rating: float = INITIAL_RATING
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PuzzleDailySlot(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("set_id", "position"),
        UniqueConstraint("set_id", "item_id"),
        UniqueConstraint("run_id"),
    )
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    set_id: str = Field(foreign_key="puzzledailyset.id", index=True)
    player_id: str = Field(foreign_key="player.id", index=True)
    position: int
    item_id: str = Field(foreign_key="contentitem.id", index=True)
    item_rating: float
    reason: str
    widening: str
    first_exposure: bool
    run_id: str | None = Field(default=None, foreign_key="puzzlerun.id")
    closed_at: datetime | None = None


class PuzzleRatingEvent(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("player_id", "sequence"),)
    run_id: str = Field(primary_key=True, foreign_key="puzzlerun.id")
    slot_id: str = Field(unique=True, foreign_key="puzzledailyslot.id")
    player_id: str = Field(foreign_key="player.id", index=True)
    item_id: str = Field(foreign_key="contentitem.id")
    sequence: int
    policy_version: str = POLICY_VERSION
    item_rating: float
    before: float
    after: float
    score: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def elo_after(before: float, item_rating: float, score: int) -> float:
    # Saturation avoids overflow for a long-lived rating while retaining Elo precision.
    exponent = (item_rating - before) / 400.0
    expected = 0.0 if exponent > 300 else 1.0 / (1.0 + 10.0**exponent)
    return before + K_FACTOR * (score - expected)

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlmodel import JSON, Column, Field, SQLModel


class Game(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    pgn: str
    headers: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    moves: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    white: str = "Unknown"
    black: str = "Unknown"
    result: str = "*"
    date: str | None = None


class PlaySession(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    player_id: str
    game_id: UUID | None = Field(default=None, foreign_key="game.id")
    opponent_config: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    clock_config: dict[str, str] | None = Field(default=None, sa_column=Column(JSON))
    status: str = "active"

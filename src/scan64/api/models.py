from datetime import UTC, datetime
from typing import Any

from sqlmodel import JSON, Field, SQLModel


class Player(SQLModel, table=True):
    id: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    preferences: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)


class PlayerProfile(SQLModel, table=True):
    player_id: str = Field(primary_key=True, foreign_key="player.id")
    rating: int = 1500
    display_name: str | None = None

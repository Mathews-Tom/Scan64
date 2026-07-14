from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
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


class PlayerCredential(SQLModel, table=True):
    player_id: str = Field(primary_key=True, foreign_key="player.id")
    token_hash: str = Field(index=True)


def issue_player_token() -> tuple[str, str]:
    token = token_urlsafe(32)
    return token, player_token_hash(token)


def player_token_hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


class DeletionAudit(SQLModel, table=True):
    id: str = Field(primary_key=True)
    player_id: str = Field(index=True)
    deleted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    affected_rows: dict[str, int] = Field(default_factory=dict, sa_type=JSON)

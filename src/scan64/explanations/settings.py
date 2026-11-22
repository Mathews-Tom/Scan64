"""Installation-local consent: never a player preference imported from an archive."""

from __future__ import annotations

import asyncio
from os import environ
from uuid import uuid4

from sqlalchemy import Connection, Engine
from sqlmodel import Field, Session, SQLModel

from scan64.providers.llm.local_ollama import LocalOllamaExplanationProvider

OPERATOR_LLM_CONFIG = "SCAN64_LLM_CONFIG"


class OwnerExplanationSettings(SQLModel, table=True):
    player_id: str = Field(primary_key=True, foreign_key="player.id")
    model: str = Field(min_length=1, max_length=128)
    revision: str = Field(default_factory=lambda: uuid4().hex, max_length=32)


def operator_manages_explanations() -> bool:
    return OPERATOR_LLM_CONFIG in environ


def _read_committed_consent(engine: Engine, player_id: str) -> tuple[str, str] | None:
    with Session(engine) as settings_session:
        row = settings_session.get(OwnerExplanationSettings, player_id)
        return (row.model, row.revision) if row is not None else None


async def read_local_explanation_provider(
    session: Session, player_id: str
) -> LocalOllamaExplanationProvider | None:
    """Bind one generation to the owner's current grant, not to the job's snapshot."""
    if operator_manages_explanations():
        return None
    bind = session.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    consent = await asyncio.to_thread(_read_committed_consent, engine, player_id)
    if consent is None:
        return None

    async def still_authorized() -> bool:
        current = await asyncio.to_thread(_read_committed_consent, engine, player_id)
        return current == consent

    return LocalOllamaExplanationProvider(consent[0], still_authorized)

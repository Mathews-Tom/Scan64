"""Prospective learning boundary without changing preserved historical game facts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

from scan64.api.models import Player
from scan64.chess.games.models import Game

RESET_AT_KEY = "learning_progress_reset_at"
RESET_GENERATION_KEY = "learning_progress_generation"


def progress_generation(player_id: str, session: Session) -> str | None:
    """Read the current cache epoch after the caller takes the owner's fence."""
    player = session.get(Player, player_id, populate_existing=True)
    if player is None:
        return None
    generation = player.preferences.get(RESET_GENERATION_KEY)
    if isinstance(generation, str):
        return generation
    # Preserve the boundary of players reset before cache epochs were added.
    boundary = player.preferences.get(RESET_AT_KEY)
    return boundary if isinstance(boundary, str) else None


def progress_reset_at(player_id: str, session: Session) -> datetime | None:
    # A reset may have committed while an engine search held an older Player object.
    player = session.get(Player, player_id, populate_existing=True)
    if player is None:
        return None
    value = player.preferences.get(RESET_AT_KEY)
    if not isinstance(value, str):
        return None
    boundary = datetime.fromisoformat(value)
    return boundary.replace(tzinfo=UTC) if boundary.tzinfo is None else boundary.astimezone(UTC)


def game_counts_for_progress(game: Game, player_id: str, session: Session) -> bool:
    """Old saved games remain reviewable, but cannot refill reset learning state.

    Creation time, not analysis/completion time, also excludes a game already in
    flight at reset. New learning attempts against preserved content are allowed.
    """
    boundary = progress_reset_at(player_id, session)
    created_at = game.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return boundary is None or created_at >= boundary

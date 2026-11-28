"""Stored variant games: standard-only features skip or refuse them.

Game analysis, review, diagnosis, profile insights and bot play replay a game
on a standard board; a variant game's moves (a crazyhouse drop, an atomic
capture) are not legal there.
"""

from __future__ import annotations

from fastapi import HTTPException

from scan64.chess.games.models import Game
from scan64.chess.variants import (
    NOT_COVERED,
    STANDARD,
    SUPPORTED_VARIANTS,
    display_name,
    stored_variant,
)


def is_standard(game: Game) -> bool:
    return stored_variant(game.headers) == STANDARD


def require_standard(game: Game, feature: str) -> None:
    """409 ``variant_not_covered`` for a variant game."""
    variant = stored_variant(game.headers)
    if variant != STANDARD:
        name = display_name(variant) if variant in SUPPORTED_VARIANTS else variant
        raise HTTPException(
            status_code=409,
            detail={
                "code": "variant_not_covered",
                "variant": variant,
                "message": NOT_COVERED.format(name=name, feature=feature),
            },
        )

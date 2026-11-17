"""Maintain the searchable projection in the catalogue writer's transaction."""

from collections.abc import Sequence

from sqlalchemy import delete, insert
from sqlmodel import Session, col

from scan64.content.models import ContentItem, PuzzleTheme


def replace_puzzle_themes(session: Session, items: Sequence[ContentItem]) -> None:
    """Replace a bounded batch; flush parents but never commit independently."""
    if not items:
        return
    session.flush()
    session.execute(
        delete(PuzzleTheme).where(col(PuzzleTheme.item_id).in_([item.id for item in items]))
    )
    rows = [
        {"item_id": item.id, "theme": theme}
        for item in items
        if item.domain == "puzzles" and isinstance(item.payload.get("themes"), list)
        for theme in sorted(
            {theme for theme in item.payload["themes"] if isinstance(theme, str) and theme}
        )
    ]
    if rows:
        session.execute(insert(PuzzleTheme), rows)

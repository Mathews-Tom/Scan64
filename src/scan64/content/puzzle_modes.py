"""Unrated unseen selection and streaks derived solely from terminal mode runs."""

import json
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import String, cast, func
from sqlmodel import Session, col, select

from scan64.content.models import ContentAttempt, ContentItem
from scan64.content.puzzle_mode_models import PuzzleModeBinding
from scan64.content.puzzle_practice import rating_for
from scan64.content.puzzle_practice_models import PuzzleDailySet, PuzzleDailySlot
from scan64.content.puzzle_runs import PuzzleRun

PracticeMode = Literal["daily", "theme", "streak"]


class PuzzleStreakRead(BaseModel):
    current: int = 0
    best: int = 0
    reset_reason: str | None = None


def streak_for(player_id: str, session: Session) -> PuzzleStreakRead:
    """No mutable counter: terminal CAS and immutable bindings are the only ledger."""
    result = PuzzleStreakRead()
    rows = session.exec(
        select(PuzzleRun.state, PuzzleRun.hint_steps)
        .join(PuzzleModeBinding, col(PuzzleModeBinding.run_id) == col(PuzzleRun.id))
        .where(
            PuzzleRun.player_id == player_id,
            PuzzleModeBinding.player_id == player_id,
            PuzzleModeBinding.mode == "streak",
            PuzzleRun.state != "active",
        )
        .order_by(col(PuzzleRun.started_at), col(PuzzleRun.id))
        .execution_options(yield_per=500)
    )
    for state, hints in rows:
        if state == "solved" and not hints:
            result.current += 1
            result.best = max(result.best, result.current)
        else:
            result.current = 0
            result.reset_reason = "hint" if hints else state
    return result


def source_themes(session: Session) -> list[str]:
    """Stream metadata only, once per request; never deserialize lesson specs."""
    rows = session.exec(
        select(col(ContentItem.payload)["themes"])
        .where(ContentItem.domain == "puzzles")
        .execution_options(yield_per=500)
    )
    themes: set[str] = set()
    for metadata in rows:
        if isinstance(metadata, list):
            themes.update(theme for theme in metadata if isinstance(theme, str) and theme)
    return sorted(themes)


def select_mode_item(
    player_id: str, day: PuzzleDailySet, theme: str | None, session: Session
) -> tuple[ContentItem, str] | None:
    """Caller holds the player's reservation lock across selection and run insertion."""
    seen = select(PuzzleRun.item_id).where(PuzzleRun.player_id == player_id)
    attempted = select(ContentAttempt.item_id).where(ContentAttempt.player_id == player_id)
    reserved = select(PuzzleDailySlot.item_id).where(
        PuzzleDailySlot.set_id == day.id, col(PuzzleDailySlot.closed_at).is_(None)
    )
    rating = rating_for(player_id, session).rating
    for width in (200, 400, None):
        statement = select(ContentItem).where(
            ContentItem.domain == "puzzles",
            ~col(ContentItem.id).in_(seen),
            ~col(ContentItem.id).in_(attempted),
            ~col(ContentItem.id).in_(reserved),
        )
        if theme is not None:
            metadata = func.coalesce(cast(col(ContentItem.payload)["themes"], String), "")
            statement = statement.where(metadata.contains(json.dumps(theme), autoescape=True))
        if width is not None:
            statement = statement.where(
                ContentItem.difficulty_estimate >= rating - width,
                ContentItem.difficulty_estimate <= rating + width,
            )
        item = session.exec(
            statement.order_by(
                func.abs(ContentItem.difficulty_estimate - rating),
                col(ContentItem.payload)["import_sequence"].as_integer(),
                ContentItem.id,
            ).limit(1)
        ).first()
        if item is not None:
            return item, str(width) if width is not None else "catalog"
    return None

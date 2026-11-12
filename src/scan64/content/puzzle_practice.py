"""Bounded, stable daily selection and transactional puzzle-only rating effects."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, cast, func, or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from scan64.content.models import ContentAttempt, ContentItem
from scan64.content.puzzle_practice_models import (
    PuzzleDailySet,
    PuzzleDailySlot,
    PuzzleRating,
    PuzzleRatingEvent,
    elo_after,
)
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.composer import SessionComposer
from scan64.learning.scheduling.priority import (
    PriorityFactors,
    classify_priority_bucket,
    compute_weakness_severity,
)
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule

# Related recognition practice only: hangingPiece does not measure prevention of own blunders.
THEME_SKILLS: dict[str, frozenset[str]] = {
    "hangingPiece": frozenset(
        {"board_awareness.hanging_piece", "threat_processing.missed_capture"}
    ),
    "mateIn1": frozenset({"threat_processing.missed_check"}),
    "defensiveMove": frozenset({"threat_processing.missed_direct_threat"}),
}
CANDIDATE_LIMIT = 30
DAILY_SIZE = 10


def utc_now() -> datetime:
    return datetime.now(UTC)


def rating_for(player_id: str, session: Session) -> PuzzleRating:
    rating = session.get(PuzzleRating, player_id)
    if rating is None:
        rating = PuzzleRating(player_id=player_id)
        session.add(rating)
        session.flush()
    return rating


def slots_for(day: PuzzleDailySet, session: Session) -> list[PuzzleDailySlot]:
    return list(
        session.exec(
            select(PuzzleDailySlot)
            .where(PuzzleDailySlot.set_id == day.id)
            .order_by(col(PuzzleDailySlot.position))
        ).all()
    )


def _pool(player_id: str, rating: float, now: datetime, session: Session) -> list[dict[str, Any]]:
    attempted = select(ContentAttempt.item_id).where(ContentAttempt.player_id == player_id)
    seen = select(PuzzleRun.item_id).where(PuzzleRun.player_id == player_id)
    active_items = select(PuzzleRun.item_id).where(
        PuzzleRun.player_id == player_id, PuzzleRun.state == "active"
    )
    due = session.exec(
        select(ContentItem)
        .join(ReviewSchedule, col(ReviewSchedule.item_id) == col(ContentItem.id))
        .where(
            ContentItem.domain == "puzzles",
            ReviewSchedule.player_id == player_id,
            col(ReviewSchedule.retired_at).is_(None),
            ReviewSchedule.next_review_at <= now,
            col(ContentItem.id).in_(seen),
            ~col(ContentItem.id).in_(active_items),
        )
        .order_by(
            col(ReviewSchedule.next_review_at),
            col(ContentItem.payload)["import_sequence"].as_integer(),
            ContentItem.id,
        )
        .limit(CANDIDATE_LIMIT)
    ).all()
    pool: list[dict[str, Any]] = [
        {
            "id": item.id,
            "item": item,
            "type": "due",
            "reason": "due",
            "widening": "due",
            "priority": 4.0 + PriorityFactors(review_due=1.0).compute_priority(),
        }
        for item in due
    ]
    skills = session.exec(
        select(SkillState).where(
            SkillState.player_id == player_id,
            col(SkillState.retired_at).is_(None),
            col(SkillState.concept_code).in_(TRUSTED_CODES),
        )
    ).all()
    severity = {skill.concept_code: compute_weakness_severity(skill) for skill in skills}
    weak_themes = {
        theme: max((severity.get(code, 0.5) for code in codes & TRUSTED_CODES), default=0.5)
        for theme, codes in THEME_SKILLS.items()
    }
    weak_themes = {
        theme: value
        for theme, value in weak_themes.items()
        if classify_priority_bucket(False, value) == "mistakes"
    }
    theme_column = func.coalesce(cast(col(ContentItem.payload)["themes"], String), "")
    weak_filter = or_(False, *(theme_column.contains(f'"{theme}"') for theme in weak_themes))
    for bucket in ("mistakes", "exploration"):
        selected: set[str] = set()
        for width in (200, 400, None):
            statement = select(ContentItem).where(
                ContentItem.domain == "puzzles",
                ~col(ContentItem.id).in_(seen),
                ~col(ContentItem.id).in_(attempted),
            )
            if bucket == "mistakes":
                if not weak_themes:
                    break
                statement = statement.where(weak_filter)
            elif weak_themes:
                statement = statement.where(~weak_filter)
            if width is not None:
                statement = statement.where(
                    ContentItem.difficulty_estimate >= rating - width,
                    ContentItem.difficulty_estimate <= rating + width,
                )
            if selected:
                statement = statement.where(~col(ContentItem.id).in_(selected))
            items = session.exec(
                statement.order_by(
                    func.abs(ContentItem.difficulty_estimate - rating),
                    col(ContentItem.payload)["import_sequence"].as_integer(),
                    ContentItem.id,
                ).limit(CANDIDATE_LIMIT - len(selected))
            ).all()
            for item in items:
                selected.add(item.id)
                weakness = max(
                    (weak_themes.get(theme, 0.0) for theme in item.payload.get("themes", [])),
                    default=0.0,
                )
                priority = (2.0 if bucket == "mistakes" else 0.0) + PriorityFactors(
                    weakness_severity=weakness if bucket == "mistakes" else 0.0,
                    user_interest=0.1 if bucket == "exploration" else 0.0,
                ).compute_priority()
                pool.append(
                    {
                        "id": item.id,
                        "item": item,
                        "type": bucket,
                        "reason": "weak" if bucket == "mistakes" else "exploration",
                        "widening": str(width) if width is not None else "catalog",
                        "priority": priority,
                    }
                )
            if len(selected) >= CANDIDATE_LIMIT:
                break
    return pool


def daily_set(player_id: str, session: Session, now: datetime | None = None) -> PuzzleDailySet:
    now = now or utc_now()
    utc_day = now.astimezone(UTC).date().isoformat()
    statement = select(PuzzleDailySet).where(
        PuzzleDailySet.player_id == player_id, PuzzleDailySet.utc_day == utc_day
    )
    existing = session.exec(statement).one_or_none()
    if existing is not None:
        return existing
    try:
        rating = rating_for(player_id, session)
        day = PuzzleDailySet(
            player_id=player_id, utc_day=utc_day, player_rating=rating.rating, created_at=now
        )
        pool = _pool(player_id, rating.rating, now, session)
        selected = SessionComposer(
            {"due": 0.4, "mistakes": 0.5, "exploration": 0.1}
        ).compose_session(pool, session_size=DAILY_SIZE)
        selected.sort(
            key=lambda candidate: {"due": 0, "mistakes": 1, "exploration": 2}[candidate["type"]]
        )
        session.add(day)
        session.flush()
        for position, candidate in enumerate(selected):
            item = candidate["item"]
            session.add(
                PuzzleDailySlot(
                    set_id=day.id,
                    player_id=player_id,
                    position=position,
                    item_id=item.id,
                    item_rating=float(item.payload.get("rating", item.difficulty_estimate)),
                    reason=candidate["reason"],
                    widening=candidate["widening"],
                    first_exposure=candidate["reason"] != "due",
                )
            )
        session.commit()
        return day
    except IntegrityError:
        session.rollback()
        return session.exec(statement).one()


def daily_summary(player_id: str, session: Session) -> dict[str, Any]:
    now = utc_now()
    day = daily_set(player_id, session, now)
    rating = rating_for(player_id, session)
    slots = slots_for(day, session)
    completed = sum(slot.closed_at is not None for slot in slots)
    overdue = session.exec(
        select(func.count())
        .select_from(ReviewSchedule)
        .join(ContentItem, col(ContentItem.id) == col(ReviewSchedule.item_id))
        .where(
            ReviewSchedule.player_id == player_id,
            ContentItem.domain == "puzzles",
            col(ReviewSchedule.retired_at).is_(None),
            ReviewSchedule.next_review_at <= now,
        )
    ).one()
    total = len(slots)
    message = (
        "Daily set complete. The next set resets at 00:00 UTC."
        if total and completed == total
        else f"{total} of 10 available today; limited eligible supply. Next reset: 00:00 UTC."
        if total < DAILY_SIZE
        else "Today's stable set resets at 00:00 UTC."
    )
    if total == 0:
        message = (
            "No eligible puzzles today. Next reset: 00:00 UTC. "
            "Import puzzles if the catalog is empty."
        )
    elif not any(slot.reason == "exploration" for slot in slots):
        message += " Exploration shortfall: no eligible unseen non-weak puzzles."
    return {
        "utc_day": day.utc_day,
        "rating": round(rating.rating),
        "rated_count": rating.rated_count,
        "provisional": rating.rated_count < 20,
        "overdue_count": overdue,
        "total": total,
        "completed": completed,
        "remaining": total - completed,
        "actual_mix": {
            reason: sum(slot.reason == reason for slot in slots)
            for reason in ("due", "weak", "exploration")
        },
        "message": message,
    }


def finish_practice(
    run: PuzzleRun, state: str, hint_steps: dict[str, int], completed_at: datetime, session: Session
) -> bool:
    """Called after the terminal run CAS, inside the same transaction; no commits here."""
    slot = session.exec(
        select(PuzzleDailySlot).where(PuzzleDailySlot.run_id == run.id)
    ).one_or_none()
    if slot is None:
        return True  # Legacy runs have no rating or review effects.
    slot.closed_at = completed_at
    session.add(slot)
    independent = state == "solved" and not hint_steps
    if slot.first_exposure:
        rating = rating_for(run.player_id, session)
        before, count = rating.rating, rating.rated_count
        after = elo_after(before, slot.item_rating, int(independent))
        changed = session.execute(
            update(PuzzleRating)
            .where(
                col(PuzzleRating.player_id) == run.player_id,
                col(PuzzleRating.rating) == before,
                col(PuzzleRating.rated_count) == count,
            )
            .values(rating=after, rated_count=count + 1)
            .returning(col(PuzzleRating.player_id))
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if changed is None:
            return False
        session.add(
            PuzzleRatingEvent(
                run_id=run.id,
                slot_id=slot.id,
                player_id=run.player_id,
                item_id=run.item_id,
                sequence=count + 1,
                item_rating=slot.item_rating,
                before=before,
                after=after,
                score=int(independent),
                created_at=completed_at,
            )
        )
        session.expire(rating)
    if state == "abandoned":
        return True
    schedule = session.get(ReviewSchedule, (run.player_id, run.item_id))
    if independent:
        if not slot.first_exposure and schedule is not None:
            schedule.retired_at = completed_at
            schedule.retirement_reason = "puzzle_review_passed"
            schedule.last_reviewed_at = completed_at
            session.add(schedule)
    else:
        if schedule is None:
            schedule = ReviewSchedule(
                player_id=run.player_id,
                item_id=run.item_id,
                next_review_at=completed_at,
                skill_id=None,
            )
        schedule.retired_at = None
        schedule.retirement_reason = None
        schedule.skill_id = None
        schedule.update(False, completed_at)
        session.add(schedule)
    return True

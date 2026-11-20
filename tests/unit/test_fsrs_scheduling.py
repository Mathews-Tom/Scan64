from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

from scan64.learning.scheduling.spaced_repetition import (
    ReviewKind,
    ReviewSchedule,
    ScheduleReviewObservation,
    review_rating,
)


@pytest.mark.parametrize(
    ("source", "success", "hints", "elapsed", "expected"),
    [
        ("lesson", False, 0, 1_000, 1),
        ("lesson", False, 2, 1_000, 1),
        ("lesson", True, 1, 1_000, 2),
        ("lesson", True, 0, 30_000, 4),
        ("lesson", True, 0, 30_001, 3),
        ("lesson", True, None, 0, 3),
        ("lesson", True, 0, None, 3),
        ("lesson", True, 0, 0, 4),
        ("puzzle", True, 0, 60_000, 4),
        ("puzzle", True, 0, 60_001, 3),
        ("puzzle", True, 3, 1_000, 2),
        ("transfer", True, 0, 30_000, 4),
        ("transfer", True, None, None, 3),
        ("sparring", True, None, None, 3),
        ("sparring", False, None, None, 1),
    ],
)
def test_grade_distinguishes_failure_assistance_time_and_unavailable(
    source: ReviewKind,
    success: bool,
    hints: int | None,
    elapsed: int | None,
    expected: int,
) -> None:
    assert (
        int(
            review_rating(success=success, hints_used=hints, elapsed_ms=elapsed, source_kind=source)
        )
        == expected
    )


@pytest.mark.parametrize(("hints", "elapsed"), [(-1, 0), (0, -1)])
def test_negative_observations_are_rejected(hints: int, elapsed: int) -> None:
    with pytest.raises(ValueError):
        review_rating(success=True, hints_used=hints, elapsed_ms=elapsed, source_kind="lesson")


@pytest.mark.parametrize(("hints", "elapsed"), [(0, None), (None, 0), (1, 1_000)])
def test_sparring_rejects_invented_observations_before_rescheduling(
    hints: int | None, elapsed: int | None
) -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    schedule = ReviewSchedule(player_id="owner", item_id="sparring", next_review_at=now)
    before = schedule.model_dump()
    with pytest.raises(ValueError, match="unavailable"):
        schedule.update(
            True,
            now,
            hints_used=hints,
            elapsed_ms=elapsed,
            source_kind="sparring",
            source_id="sparring-review",
        )
    assert schedule.model_dump() == before


def test_review_continues_from_persisted_state_after_database_restart(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'restart.db'}"
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        schedule = ReviewSchedule(player_id="owner", item_id="lesson", next_review_at=now)
        session.add(schedule)
        session.flush()
        first = schedule.update(
            False,
            now,
            hints_used=0,
            elapsed_ms=15_000,
            source_kind="lesson",
            source_id="first-review",
        )
        session.add(first)
        session.commit()
        session.refresh(schedule)
        expected = ReviewSchedule.model_validate(schedule.model_dump())
        first_id = first.id
    engine.dispose()
    later = now + timedelta(days=2)
    expected.update(
        True,
        later,
        hints_used=0,
        elapsed_ms=45_000,
        source_kind="lesson",
        source_id="second-review",
    )
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            restored = session.get(ReviewSchedule, ("owner", "lesson"))
            assert restored is not None
            second = restored.update(
                True,
                later,
                hints_used=0,
                elapsed_ms=45_000,
                source_kind="lesson",
                source_id="second-review",
            )
            assert restored.next_review_at == expected.next_review_at
            assert restored.fsrs_stability == expected.fsrs_stability
            assert restored.fsrs_difficulty == expected.fsrs_difficulty
            assert restored.fsrs_state == expected.fsrs_state
            assert restored.fsrs_step == expected.fsrs_step
            session.add(second)
            session.add(restored)
            session.commit()
            second_id = second.id
        with Session(engine) as session:
            first_saved = session.get(ScheduleReviewObservation, first_id)
            second_saved = session.get(ScheduleReviewObservation, second_id)
            assert first_saved is not None and first_saved.rating == 1
            assert first_saved.hints_used == 0 and first_saved.elapsed_ms == 15_000
            assert second_saved is not None and second_saved.rating == 3
            assert second_saved.hints_used == 0 and second_saved.elapsed_ms == 45_000
    finally:
        engine.dispose()

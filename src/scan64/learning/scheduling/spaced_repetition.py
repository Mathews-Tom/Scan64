from __future__ import annotations

from datetime import UTC, datetime
from hashlib import blake2b
from math import isfinite
from typing import Literal, Self
from uuid import UUID, uuid4

from fsrs import Card, Rating, Scheduler, State
from pydantic import model_validator
from sqlalchemy import Column, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlmodel import Field, SQLModel

ReviewKind = Literal["lesson", "puzzle", "transfer"]
_SCHEDULER = Scheduler(enable_fuzzing=False)
_FAST_REVIEW_MS: dict[ReviewKind, int] = {
    "lesson": 30_000,
    "puzzle": 60_000,
    "transfer": 30_000,
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def review_rating(
    *,
    success: bool,
    hints_used: int | None,
    elapsed_ms: int | None,
    source_kind: ReviewKind,
) -> Rating:
    if hints_used is not None and hints_used < 0:
        raise ValueError("hints_used must be nonnegative or unavailable")
    if elapsed_ms is not None and elapsed_ms < 0:
        raise ValueError("elapsed_ms must be nonnegative or unavailable")
    if not success:
        return Rating.Again
    if hints_used is not None and hints_used > 0:
        return Rating.Hard
    if hints_used == 0 and elapsed_ms is not None and elapsed_ms <= _FAST_REVIEW_MS[source_kind]:
        return Rating.Easy
    return Rating.Good


class ScheduleReviewObservation(SQLModel, table=True):
    __table_args__ = (
        ForeignKeyConstraint(
            ["player_id", "item_id"],
            ["reviewschedule.player_id", "reviewschedule.item_id"],
            name="fk_schedule_review_observation",
        ),
        UniqueConstraint("source_kind", "source_id", name="uq_schedule_review_source"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    player_id: str = Field(index=True)
    item_id: str
    source_kind: ReviewKind = Field(sa_column=Column(String, nullable=False))
    source_id: str
    success: bool
    hints_used: int | None = Field(ge=0)
    elapsed_ms: int | None = Field(ge=0)
    rating: int = Field(ge=1, le=4)
    reviewed_at: datetime
    input_provenance: Literal["app_reported", "server_recorded"] = Field(
        sa_column=Column(String, nullable=False)
    )
    verification_status: Literal["verified", "historical"] = Field(
        default="verified", sa_column=Column(String, nullable=False)
    )

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        expected = "server_recorded" if self.source_kind == "puzzle" else "app_reported"
        if self.input_provenance != expected:
            raise ValueError("Review input provenance does not match its source")
        if self.rating != int(
            review_rating(
                success=self.success,
                hints_used=self.hints_used,
                elapsed_ms=self.elapsed_ms,
                source_kind=self.source_kind,
            )
        ):
            raise ValueError("Review rating does not match its recorded inputs")
        return self


class ReviewSchedule(SQLModel, table=True):
    """FSRS state and due date for a player-owned scheduled item."""

    player_id: str = Field(primary_key=True)
    item_id: str = Field(primary_key=True)
    next_review_at: datetime
    last_reviewed_at: datetime | None = None
    fsrs_state: int = Field(default=int(State.Learning), ge=1, le=3)
    # A graduated card's explicit null must not be replaced by the insert default.
    fsrs_step: int | None = Field(
        default=0, ge=0, sa_column=Column(Integer().evaluates_none(), nullable=True, default=0)
    )
    fsrs_stability: float | None = Field(default=None, gt=0)
    fsrs_difficulty: float | None = Field(default=None, ge=1, le=10)
    # Historical values support lossless downgrade before any FSRS reviews.
    legacy_interval_days: float | None = None
    legacy_ease_factor: float | None = None
    skill_id: str | None = None
    retired_at: datetime | None = None
    retirement_reason: str | None = None

    @model_validator(mode="after")
    def validate_fsrs_state(self) -> Self:
        if (self.fsrs_stability is None) != (self.fsrs_difficulty is None):
            raise ValueError("FSRS stability and difficulty must be available together")
        for value in (self.fsrs_stability, self.fsrs_difficulty):
            if value is not None and not isfinite(value):
                raise ValueError("FSRS state must be finite")
        if self.fsrs_state == int(State.Review):
            if self.fsrs_step is not None or self.fsrs_stability is None:
                raise ValueError("FSRS review state requires learned parameters and no step")
        else:
            steps = (
                _SCHEDULER.learning_steps
                if self.fsrs_state == int(State.Learning)
                else _SCHEDULER.relearning_steps
            )
            if self.fsrs_step is None or not 0 <= self.fsrs_step < len(steps):
                raise ValueError("Invalid FSRS learning step")
            if self.fsrs_state == int(State.Relearning) and self.fsrs_stability is None:
                raise ValueError("FSRS relearning requires learned parameters")
        return self

    def is_due(self, current_time: datetime) -> bool:
        return _as_utc(current_time) >= _as_utc(self.next_review_at)

    def update(
        self,
        success: bool,
        current_time: datetime,
        *,
        hints_used: int | None,
        elapsed_ms: int | None,
        source_kind: ReviewKind,
        source_id: str,
    ) -> ScheduleReviewObservation:
        if self.retired_at is not None:
            raise ValueError("Cannot review a retired schedule")
        rating = review_rating(
            success=success,
            hints_used=hints_used,
            elapsed_ms=elapsed_ms,
            source_kind=source_kind,
        )
        # Supply a stable scoped id: Card's automatic id sleeps for every creation.
        card_id = int.from_bytes(
            blake2b(f"{self.player_id}\0{self.item_id}".encode(), digest_size=8).digest(), "big"
        )
        card, log = _SCHEDULER.review_card(
            Card(
                card_id=card_id,
                state=State(self.fsrs_state),
                step=self.fsrs_step,
                stability=self.fsrs_stability,
                difficulty=self.fsrs_difficulty,
                due=_as_utc(self.next_review_at),
                last_review=(
                    _as_utc(self.last_reviewed_at) if self.last_reviewed_at is not None else None
                ),
            ),
            rating,
            review_datetime=_as_utc(current_time),
            review_duration=elapsed_ms,
        )
        self.fsrs_state = int(card.state)
        self.fsrs_step = card.step
        self.fsrs_stability = card.stability
        self.fsrs_difficulty = card.difficulty
        self.next_review_at = card.due
        self.last_reviewed_at = card.last_review
        return ScheduleReviewObservation(
            player_id=self.player_id,
            item_id=self.item_id,
            source_kind=source_kind,
            source_id=source_id,
            success=success,
            hints_used=hints_used,
            elapsed_ms=elapsed_ms,
            rating=int(log.rating),
            reviewed_at=log.review_datetime,
            input_provenance="server_recorded" if source_kind == "puzzle" else "app_reported",
        )

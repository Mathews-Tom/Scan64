from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, Session, SQLModel, col, select

from scan64.learning.exercises.transfer import (
    TransferExercise,
    TransferKind,
    generate_near_transfer_exercise,
    retrieve_positions_by_motif_and_difficulty,
)
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule


class TransferMeasurementError(ValueError):
    """Raised when a transfer-measurement lifecycle transition is invalid."""


class MeasurementPoint(StrEnum):
    """The three observations needed to distinguish transfer from memorization."""

    PRE_TEST = "pre_test"
    IMMEDIATE_POST_TEST = "immediate_post_test"
    DELAYED_TEST = "delayed_test"


MEASUREMENT_POINTS: tuple[MeasurementPoint, ...] = (
    MeasurementPoint.PRE_TEST,
    MeasurementPoint.IMMEDIATE_POST_TEST,
    MeasurementPoint.DELAYED_TEST,
)
DEFAULT_DELAYED_TEST_INTERVAL = timedelta(days=7)


class TransferMeasurement(SQLModel, table=True):
    """One assigned unseen transfer exercise for a cohort member and skill."""

    __table_args__ = (
        UniqueConstraint(
            "cohort_id",
            "player_id",
            "skill_id",
            "measurement_point",
            name="uq_transfer_measurement_cohort_player_skill_point",
        ),
        Index(
            "ix_transfer_measurement_cohort_skill_point",
            "cohort_id",
            "skill_id",
            "measurement_point",
        ),
        Index("ix_transfer_measurement_player_scheduled", "player_id", "scheduled_for"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    cohort_id: str
    player_id: str
    skill_id: str
    measurement_point: MeasurementPoint
    source_position_id: str
    target_position_id: str | None = None
    source_fen: str
    target_fen: str
    transfer_kind: TransferKind
    scheduled_for: datetime | None = None
    completed_at: datetime | None = None
    succeeded: bool | None = None


@dataclass(frozen=True)
class TransferMeasurementInstrumentation:
    """The persisted pre-test assignments created for a cohort enrolment."""

    cohort_id: str
    skill_id: str
    measurements: tuple[TransferMeasurement, ...]


def instrument_transfer_measurements(
    session: Session,
    *,
    cohort_id: str,
    player_ids: tuple[str, ...],
    skill_id: str,
    target_difficulty: float,
    difficulty_tolerance: float,
    now: datetime,
) -> TransferMeasurementInstrumentation:
    """Assign a pre-test and reserve post-tests for every cohort member.

    The immediate and delayed tests remain unscheduled until training completes.
    That prevents a participant from encountering a post-test before the
    intervention it is intended to measure.
    """
    _validate_instrumentation_request(
        cohort_id=cohort_id,
        player_ids=player_ids,
        skill_id=skill_id,
        target_difficulty=target_difficulty,
        difficulty_tolerance=difficulty_tolerance,
    )
    measurement_time = _as_utc(now)
    _ensure_uninstrumented(session, cohort_id, player_ids, skill_id)
    exercises = _transfer_exercises(
        session,
        skill_id=skill_id,
        target_difficulty=target_difficulty,
        difficulty_tolerance=difficulty_tolerance,
    )

    measurements: list[TransferMeasurement] = []
    for player_id in player_ids:
        for measurement_point, exercise in zip(MEASUREMENT_POINTS, exercises, strict=True):
            measurement = _measurement_from_exercise(
                cohort_id=cohort_id,
                player_id=player_id,
                measurement_point=measurement_point,
                exercise=exercise,
                scheduled_for=(
                    measurement_time
                    if measurement_point is MeasurementPoint.PRE_TEST
                    else None
                ),
            )
            measurements.append(measurement)
            session.add(measurement)
            if measurement_point is MeasurementPoint.PRE_TEST:
                session.add(
                    ReviewSchedule(
                        player_id=player_id,
                        item_id=measurement.id,
                        next_review_at=measurement_time,
                    )
                )

    session.commit()
    return TransferMeasurementInstrumentation(
        cohort_id=cohort_id,
        skill_id=skill_id,
        measurements=tuple(measurements),
    )


def due_transfer_measurements(
    session: Session,
    *,
    player_id: str,
    now: datetime,
) -> list[TransferMeasurement]:
    """Return this player's due, incomplete transfer measurements in time order."""
    _require_identifier("player_id", player_id)
    current_time = _as_utc(now)
    statement = (
        select(TransferMeasurement)
        .where(TransferMeasurement.player_id == player_id)
        .where(col(TransferMeasurement.scheduled_for) <= current_time)
        .where(col(TransferMeasurement.completed_at).is_(None))
        .order_by(col(TransferMeasurement.scheduled_for), col(TransferMeasurement.id))
    )
    candidates = list(session.exec(statement))
    return [
        measurement
        for measurement in candidates
        if (schedule := session.get(ReviewSchedule, (player_id, measurement.id))) is not None
        and schedule.is_due(current_time)
    ]


def record_transfer_measurement(
    session: Session,
    *,
    measurement_id: str,
    player_id: str,
    succeeded: bool,
    now: datetime,
) -> TransferMeasurement:
    """Record one completed transfer test and remove it from the review queue."""
    _require_identifier("measurement_id", measurement_id)
    _require_identifier("player_id", player_id)

    measurement = session.get(TransferMeasurement, measurement_id)
    if measurement is None:
        raise TransferMeasurementError("Transfer measurement does not exist")
    if measurement.player_id != player_id:
        raise TransferMeasurementError("Transfer measurement does not belong to player_id")
    if measurement.completed_at is not None:
        raise TransferMeasurementError("Transfer measurement has already been completed")
    if measurement.scheduled_for is None:
        raise TransferMeasurementError("Transfer measurement is not scheduled")

    completion_time = _as_utc(now)
    if completion_time < _as_utc(measurement.scheduled_for):
        raise TransferMeasurementError("Transfer measurement cannot complete before it is due")

    measurement.completed_at = completion_time
    measurement.succeeded = succeeded
    schedule = session.get(ReviewSchedule, (player_id, measurement.id))
    if schedule is None:
        raise TransferMeasurementError("Transfer measurement is missing its review schedule")
    session.delete(schedule)
    session.add(measurement)
    session.commit()
    session.refresh(measurement)
    return measurement


def record_training_completion(
    session: Session,
    *,
    cohort_id: str,
    player_id: str,
    skill_id: str,
    completed_at: datetime,
    delayed_test_interval: timedelta = DEFAULT_DELAYED_TEST_INTERVAL,
) -> tuple[TransferMeasurement, TransferMeasurement]:
    """Schedule immediate and delayed post-tests after a completed intervention."""
    _require_identifier("cohort_id", cohort_id)
    _require_identifier("player_id", player_id)
    _require_identifier("skill_id", skill_id)
    if delayed_test_interval <= timedelta():
        raise TransferMeasurementError("delayed_test_interval must be positive")

    measurements = _measurements_for_player_skill(session, cohort_id, player_id, skill_id)
    pre_test = measurements[MeasurementPoint.PRE_TEST]
    if pre_test.completed_at is None:
        raise TransferMeasurementError("Pre-test must complete before training is recorded")

    completion_time = _as_utc(completed_at)
    if completion_time < _as_utc(pre_test.completed_at):
        raise TransferMeasurementError("Training cannot complete before the pre-test")
    immediate_post_test = measurements[MeasurementPoint.IMMEDIATE_POST_TEST]
    delayed_test = measurements[MeasurementPoint.DELAYED_TEST]
    if immediate_post_test.scheduled_for is not None or delayed_test.scheduled_for is not None:
        raise TransferMeasurementError("Post-tests have already been scheduled")

    immediate_post_test.scheduled_for = completion_time
    delayed_test.scheduled_for = completion_time + delayed_test_interval
    session.add_all((immediate_post_test, delayed_test))
    session.add_all(
        (
            ReviewSchedule(
                player_id=player_id,
                item_id=immediate_post_test.id,
                next_review_at=completion_time,
            ),
            ReviewSchedule(
                player_id=player_id,
                item_id=delayed_test.id,
                next_review_at=completion_time + delayed_test_interval,
            ),
        )
    )
    session.commit()
    session.refresh(immediate_post_test)
    session.refresh(delayed_test)
    return immediate_post_test, delayed_test


def _validate_instrumentation_request(
    *,
    cohort_id: str,
    player_ids: tuple[str, ...],
    skill_id: str,
    target_difficulty: float,
    difficulty_tolerance: float,
) -> None:
    _require_identifier("cohort_id", cohort_id)
    _require_identifier("skill_id", skill_id)
    if not player_ids:
        raise TransferMeasurementError("player_ids must not be empty")
    if len(set(player_ids)) != len(player_ids):
        raise TransferMeasurementError("player_ids must be unique")
    for player_id in player_ids:
        _require_identifier("player_id", player_id)
    if not math.isfinite(target_difficulty):
        raise TransferMeasurementError("target_difficulty must be finite")
    if not math.isfinite(difficulty_tolerance) or difficulty_tolerance < 0:
        raise TransferMeasurementError("difficulty_tolerance must be finite and non-negative")


def _ensure_uninstrumented(
    session: Session,
    cohort_id: str,
    player_ids: tuple[str, ...],
    skill_id: str,
) -> None:
    existing = session.exec(
        select(TransferMeasurement)
        .where(TransferMeasurement.cohort_id == cohort_id)
        .where(TransferMeasurement.skill_id == skill_id)
        .where(col(TransferMeasurement.player_id).in_(player_ids))
    ).first()
    if existing is not None:
        raise TransferMeasurementError(
            "Transfer measurements already exist for this cohort, player, and skill"
        )


def _transfer_exercises(
    session: Session,
    *,
    skill_id: str,
    target_difficulty: float,
    difficulty_tolerance: float,
) -> tuple[TransferExercise, TransferExercise, TransferExercise]:
    positions = retrieve_positions_by_motif_and_difficulty(
        session,
        skill_id=skill_id,
        target_difficulty=target_difficulty,
        difficulty_tolerance=difficulty_tolerance,
        limit=len(MEASUREMENT_POINTS),
    )
    if len(positions) != len(MEASUREMENT_POINTS):
        raise TransferMeasurementError(
            "Three transfer positions are required for pre, immediate-post, and delayed tests"
        )
    if len({position.fen for position in positions}) != len(MEASUREMENT_POINTS):
        raise TransferMeasurementError("Transfer measurement positions must be distinct")
    first, second, third = positions
    return (
        generate_near_transfer_exercise(first),
        generate_near_transfer_exercise(second),
        generate_near_transfer_exercise(third),
    )


def _measurement_from_exercise(
    *,
    cohort_id: str,
    player_id: str,
    measurement_point: MeasurementPoint,
    exercise: TransferExercise,
    scheduled_for: datetime | None,
) -> TransferMeasurement:
    return TransferMeasurement(
        cohort_id=cohort_id,
        player_id=player_id,
        skill_id=exercise.skill_id,
        measurement_point=measurement_point,
        source_position_id=exercise.source_position_id,
        target_position_id=exercise.target_position_id,
        source_fen=exercise.source_fen,
        target_fen=exercise.target_fen,
        transfer_kind=exercise.transfer_kind,
        scheduled_for=scheduled_for,
    )


def _measurements_for_player_skill(
    session: Session,
    cohort_id: str,
    player_id: str,
    skill_id: str,
) -> dict[MeasurementPoint, TransferMeasurement]:
    measurements = list(
        session.exec(
            select(TransferMeasurement)
            .where(TransferMeasurement.cohort_id == cohort_id)
            .where(TransferMeasurement.player_id == player_id)
            .where(TransferMeasurement.skill_id == skill_id)
        )
    )
    by_point = {measurement.measurement_point: measurement for measurement in measurements}
    if set(by_point) != set(MEASUREMENT_POINTS):
        raise TransferMeasurementError("Transfer measurement lifecycle is incomplete")
    return by_point


def _require_identifier(name: str, value: str) -> None:
    if not value.strip():
        raise TransferMeasurementError(f"{name} must not be empty")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

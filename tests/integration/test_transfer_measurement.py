from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from scan64.learning.evaluation.transfer_measurement import (
    MeasurementPoint,
    TransferMeasurement,
    TransferMeasurementError,
    build_transfer_measurement_report,
    due_transfer_measurements,
    instrument_transfer_measurements,
    record_training_completion,
    record_transfer_measurement,
)
from scan64.learning.exercises.transfer import TransferPosition
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session


def add_transfer_positions(session: Session, skill_id: str) -> None:
    session.add_all(
        [
            TransferPosition(
                skill_id=skill_id,
                difficulty=1500,
                fen="r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
                opening="Sicilian Defence",
                board_side="kingside",
                attacking_piece="bishop",
                material_count=32,
                move_number=3,
            ),
            TransferPosition(
                skill_id=skill_id,
                difficulty=1550,
                fen="r1bqkb1r/pppp1ppp/2n2n2/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 3 3",
                opening="French Defence",
                board_side="queenside",
                attacking_piece="bishop",
                material_count=32,
                move_number=3,
            ),
            TransferPosition(
                skill_id=skill_id,
                difficulty=1600,
                fen="rnbqkbnr/1ppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                opening="English Opening",
                board_side="queenside",
                attacking_piece="knight",
                material_count=31,
                move_number=1,
            ),
        ]
    )
    session.commit()


def test_instrumentation_schedules_pre_then_post_transfer_tests(session: Session) -> None:
    skill_id = "tactics.pin"
    cohort_id = "cohort-july"
    first_player_id = "player-a"
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    add_transfer_positions(session, skill_id)

    instrumentation = instrument_transfer_measurements(
        session,
        cohort_id=cohort_id,
        player_ids=(first_player_id, "player-b"),
        skill_id=skill_id,
        target_difficulty=1550,
        difficulty_tolerance=100,
        now=now,
    )

    assert len(instrumentation.measurements) == 6
    measurements = list(session.exec(select(TransferMeasurement)))
    assert {measurement.measurement_point for measurement in measurements} == set(MeasurementPoint)
    assert all(measurement.target_fen != measurement.source_fen for measurement in measurements)
    assert all(measurement.transfer_kind == "near" for measurement in measurements)
    assert [
        measurement.measurement_point
        for measurement in due_transfer_measurements(session, player_id=first_player_id, now=now)
    ] == [MeasurementPoint.PRE_TEST]

    pre_test = next(
        measurement
        for measurement in measurements
        if measurement.player_id == first_player_id
        and measurement.measurement_point is MeasurementPoint.PRE_TEST
    )
    completed_pre_test = record_transfer_measurement(
        session,
        measurement_id=pre_test.id,
        player_id=first_player_id,
        succeeded=False,
        now=now,
    )
    assert completed_pre_test.succeeded is False
    with pytest.raises(TransferMeasurementError, match="already been completed"):
        record_transfer_measurement(
            session,
            measurement_id=pre_test.id,
            player_id=first_player_id,
            succeeded=True,
            now=now,
        )
    immediate_post_test, delayed_test = record_training_completion(
        session,
        cohort_id=cohort_id,
        player_id=first_player_id,
        skill_id=skill_id,
        completed_at=now + timedelta(hours=1),
        delayed_test_interval=timedelta(days=7),
    )

    assert immediate_post_test.scheduled_for is not None
    assert delayed_test.scheduled_for is not None
    assert delayed_test.scheduled_for - immediate_post_test.scheduled_for == timedelta(days=7)
    assert session.get(ReviewSchedule, (first_player_id, pre_test.id)) is None
    assert [
        measurement.measurement_point
        for measurement in due_transfer_measurements(
            session,
            player_id=first_player_id,
            now=now + timedelta(hours=1),
        )
    ] == [MeasurementPoint.IMMEDIATE_POST_TEST]
    record_transfer_measurement(
        session,
        measurement_id=immediate_post_test.id,
        player_id=first_player_id,
        succeeded=True,
        now=now + timedelta(hours=1),
    )
    assert [
        measurement.measurement_point
        for measurement in due_transfer_measurements(
            session,
            player_id=first_player_id,
            now=now + timedelta(days=7, hours=1),
        )
    ] == [MeasurementPoint.DELAYED_TEST]
    assert [
        measurement.measurement_point
        for measurement in due_transfer_measurements(
            session,
            player_id="player-b",
            now=now + timedelta(days=7, hours=1),
        )
    ] == [MeasurementPoint.PRE_TEST]
    with pytest.raises(TransferMeasurementError, match="already exist"):
        instrument_transfer_measurements(
            session,
            cohort_id=cohort_id,
            player_ids=(first_player_id,),
            skill_id=skill_id,
            target_difficulty=1550,
            difficulty_tolerance=100,
            now=now,
        )


def test_instrumentation_requires_completed_pre_test_before_post_tests(session: Session) -> None:
    skill_id = "tactics.pin"
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    add_transfer_positions(session, skill_id)
    instrument_transfer_measurements(
        session,
        cohort_id="cohort-july",
        player_ids=("player-a",),
        skill_id=skill_id,
        target_difficulty=1550,
        difficulty_tolerance=100,
        now=now,
    )

    with pytest.raises(TransferMeasurementError, match="Pre-test must complete"):
        record_training_completion(
            session,
            cohort_id="cohort-july",
            player_id="player-a",
            skill_id=skill_id,
            completed_at=now,
        )


def test_reporting_aggregates_cohort_transfer_success(session: Session) -> None:
    skill_id = "tactics.pin"
    cohort_id = "cohort-july"
    player_ids = ("player-a", "player-b")
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    add_transfer_positions(session, skill_id)
    instrument_transfer_measurements(
        session,
        cohort_id=cohort_id,
        player_ids=player_ids,
        skill_id=skill_id,
        target_difficulty=1550,
        difficulty_tolerance=100,
        now=now,
    )
    measurements = list(session.exec(select(TransferMeasurement)))
    pre_tests = {
        measurement.player_id: measurement
        for measurement in measurements
        if measurement.measurement_point is MeasurementPoint.PRE_TEST
    }
    for player_id, succeeded in (("player-a", True), ("player-b", False)):
        record_transfer_measurement(
            session,
            measurement_id=pre_tests[player_id].id,
            player_id=player_id,
            succeeded=succeeded,
            now=now,
        )

    post_tests = [
        record_training_completion(
            session,
            cohort_id=cohort_id,
            player_id=player_id,
            skill_id=skill_id,
            completed_at=now + timedelta(hours=1),
        )[0]
        for player_id in player_ids
    ]
    for measurement, succeeded in zip(post_tests, (False, True), strict=True):
        record_transfer_measurement(
            session,
            measurement_id=measurement.id,
            player_id=measurement.player_id,
            succeeded=succeeded,
            now=now + timedelta(hours=1),
        )

    report = build_transfer_measurement_report(
        session,
        cohort_id=cohort_id,
        skill_id=skill_id,
    )
    summaries = {summary.measurement_point: summary for summary in report.measurements}

    assert report.cohort_id == cohort_id
    assert report.skill_id == skill_id
    assert summaries[MeasurementPoint.PRE_TEST].assigned_count == 2
    assert summaries[MeasurementPoint.PRE_TEST].completed_count == 2
    assert summaries[MeasurementPoint.PRE_TEST].successful_count == 1
    assert summaries[MeasurementPoint.PRE_TEST].success_rate == 0.5
    assert summaries[MeasurementPoint.IMMEDIATE_POST_TEST].assigned_count == 2
    assert summaries[MeasurementPoint.IMMEDIATE_POST_TEST].completed_count == 2
    assert summaries[MeasurementPoint.IMMEDIATE_POST_TEST].successful_count == 1
    assert summaries[MeasurementPoint.IMMEDIATE_POST_TEST].success_rate == 0.5
    assert summaries[MeasurementPoint.DELAYED_TEST].assigned_count == 2
    assert summaries[MeasurementPoint.DELAYED_TEST].completed_count == 0
    assert summaries[MeasurementPoint.DELAYED_TEST].successful_count == 0
    assert summaries[MeasurementPoint.DELAYED_TEST].success_rate is None
    delayed_test = next(
        measurement
        for measurement in session.exec(select(TransferMeasurement))
        if measurement.player_id == "player-a"
        and measurement.measurement_point is MeasurementPoint.DELAYED_TEST
    )
    record_transfer_measurement(
        session,
        measurement_id=delayed_test.id,
        player_id="player-a",
        succeeded=True,
        now=now + timedelta(days=8),
    )
    partial_report = build_transfer_measurement_report(
        session,
        cohort_id=cohort_id,
        skill_id=skill_id,
    )
    partial_summaries = {
        summary.measurement_point: summary for summary in partial_report.measurements
    }
    assert partial_summaries[MeasurementPoint.DELAYED_TEST].completed_count == 1
    assert partial_summaries[MeasurementPoint.DELAYED_TEST].successful_count == 1
    assert partial_summaries[MeasurementPoint.DELAYED_TEST].success_rate is None


def test_reporting_rejects_missing_measurements(session: Session) -> None:
    with pytest.raises(TransferMeasurementError, match="do not exist"):
        build_transfer_measurement_report(
            session,
            cohort_id="cohort-july",
            skill_id="tactics.pin",
        )


def test_reporting_rejects_incomplete_cohort_lifecycle(session: Session) -> None:
    skill_id = "tactics.pin"
    add_transfer_positions(session, skill_id)
    instrument_transfer_measurements(
        session,
        cohort_id="cohort-july",
        player_ids=("player-a",),
        skill_id=skill_id,
        target_difficulty=1550,
        difficulty_tolerance=100,
        now=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
    )
    delayed_test = next(
        measurement
        for measurement in session.exec(select(TransferMeasurement))
        if measurement.measurement_point is MeasurementPoint.DELAYED_TEST
    )
    session.delete(delayed_test)
    session.commit()

    with pytest.raises(TransferMeasurementError, match="lifecycle is incomplete"):
        build_transfer_measurement_report(
            session,
            cohort_id="cohort-july",
            skill_id=skill_id,
        )

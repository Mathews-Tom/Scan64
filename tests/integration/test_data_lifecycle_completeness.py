from __future__ import annotations

from collections.abc import Iterable

import pytest
from sqlalchemy import Column, MetaData, String, Table
from sqlmodel import SQLModel

from scan64.api.data_lifecycle import ExportArchive
from scan64.api.middleware import IdempotencyRecord
from scan64.api.models import DeletionAudit, Player, PlayerCredential, PlayerProfile
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.coach.models import CoachStudentLink
from scan64.content.models import ContentAttempt, ContentItem, LessonAttempt, StudySession
from scan64.learning.evaluation.transfer_measurement import TransferMeasurement
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.transfer import TransferPosition
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule

PLAYER_LIFECYCLE_TABLES = frozenset(
    {
        Player.__tablename__,
        PlayerProfile.__tablename__,
        PlayerCredential.__tablename__,
        Game.__tablename__,
        PlaySession.__tablename__,
        Position.__tablename__,
        EngineAnalysis.__tablename__,
        AnalysisJob.__tablename__,
        PersistedLessonOpportunity.__tablename__,
        Evidence.__tablename__,
        ProfileObservation.__tablename__,
        SkillState.__tablename__,
        ReviewSchedule.__tablename__,
        StudySession.__tablename__,
        ContentAttempt.__tablename__,
        LessonAttempt.__tablename__,
        TransferMeasurement.__tablename__,
        CoachStudentLink.__tablename__,
    }
)

GLOBAL_REFERENCE_TABLES = frozenset(
    {
        ContentItem.__tablename__,
        TransferPosition.__tablename__,
    }
)

TOMBSTONE_TABLES = frozenset({DeletionAudit.__tablename__})
OPERATIONAL_TABLES = frozenset({IdempotencyRecord.__tablename__})

ARCHIVE_FIELDS = {
    Player.__tablename__: "player",
    PlayerProfile.__tablename__: "profile",
    PlaySession.__tablename__: "play_sessions",
    Game.__tablename__: "games",
    Position.__tablename__: "positions",
    EngineAnalysis.__tablename__: "engine_analyses",
    AnalysisJob.__tablename__: "analysis_jobs",
    PersistedLessonOpportunity.__tablename__: "lesson_opportunities",
    Evidence.__tablename__: "evidence",
    ProfileObservation.__tablename__: "profile_observations",
    SkillState.__tablename__: "skill_states",
    ReviewSchedule.__tablename__: "review_schedules",
    StudySession.__tablename__: "study_sessions",
    ContentAttempt.__tablename__: "content_attempts",
    LessonAttempt.__tablename__: "lesson_attempts",
    TransferMeasurement.__tablename__: "transfer_measurements",
    TransferPosition.__tablename__: "transfer_positions",
    CoachStudentLink.__tablename__: "coach_student_links",
}


# A new table with any of these columns must be assigned lifecycle semantics.
DIRECT_PLAYER_OWNERSHIP_COLUMNS = frozenset(
    {"player_id", "owner_player_id", "coach_id", "student_id"}
)


def _table_names(tables: Iterable[Table]) -> set[str]:
    return {table.name for table in tables}


def _unregistered_player_tables(tables: Iterable[Table]) -> set[str]:
    return {
        table.name
        for table in tables
        if DIRECT_PLAYER_OWNERSHIP_COLUMNS.intersection(table.columns.keys())
        and table.name not in PLAYER_LIFECYCLE_TABLES | TOMBSTONE_TABLES | OPERATIONAL_TABLES
    }


def test_all_sqlmodel_tables_have_explicit_lifecycle_semantics() -> None:
    registered = (
        PLAYER_LIFECYCLE_TABLES
        | GLOBAL_REFERENCE_TABLES
        | TOMBSTONE_TABLES
        | OPERATIONAL_TABLES
    )
    assert _table_names(SQLModel.metadata.tables.values()) == registered


def test_new_player_scoped_table_requires_lifecycle_registration() -> None:
    unregistered = Table(
        "unregistered_player_data",
        MetaData(),
        Column("id", String, primary_key=True),
        Column("player_id", String, nullable=False),
    )

    assert _unregistered_player_tables((*SQLModel.metadata.tables.values(), unregistered)) == {
        "unregistered_player_data"
    }


@pytest.mark.xfail(
    strict=True,
    reason="M42 export, import, and deletion coverage lands in the follow-on stack slices",
)
def test_export_archive_has_a_field_for_every_lifecycle_record() -> None:
    missing_fields = set(ARCHIVE_FIELDS.values()) - set(ExportArchive.model_fields)

    assert not missing_fields

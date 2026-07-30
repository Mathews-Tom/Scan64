from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256

from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table
from sqlmodel import Session, SQLModel, col, select

from scan64.api.data_lifecycle import ExportArchive
from scan64.api.middleware import IdempotencyRecord
from scan64.api.models import DeletionAudit, Player, PlayerCredential, PlayerProfile
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.coach.models import CoachStudentLink
from scan64.content.models import ContentAttempt, ContentItem, LessonAttempt, StudySession
from scan64.learning.evaluation.transfer_measurement import (
    MeasurementPoint,
    TransferMeasurement,
)
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.transfer import TransferKind, TransferPosition
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


def test_export_archive_has_a_field_for_every_lifecycle_record() -> None:
    missing_fields = set(ARCHIVE_FIELDS.values()) - set(ExportArchive.model_fields)

    assert not missing_fields


def _create_player_token(client: TestClient, player_id: str) -> str:
    response = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": "Lifecycle Player", "preferences": {}},
    )

    assert response.status_code == 200
    return str(response.json()["access_token"])


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_player_lifecycle_records(
    session: Session, player_id: str, coach_id: str
) -> dict[str, object]:
    game = Game(pgn="1. e4 e5", owner_player_id=player_id)
    session.add(game)
    session.flush()
    position = Position(
        game_id=game.id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="initial-position",
    )
    session.add(position)
    session.flush()
    analysis = EngineAnalysis(position_id=position.id)
    session.add(analysis)
    session.flush()
    opportunity = PersistedLessonOpportunity(
        game_id=game.id,
        source_position_id=position.id,
        player_id=player_id,
        lesson_spec={
            "diagnosis": {
                "primary": "tactics.hanging_piece",
                "secondary": [],
                "confidence": 0.9,
            }
        },
    )
    transfer_position = TransferPosition(
        skill_id="tactics.hanging_piece",
        difficulty=1200,
        fen=position.fen,
        opening="Open Game",
        board_side="white",
        attacking_piece="queen",
        material_count=32,
        move_number=1,
    )
    session.add_all(
        [
            PlaySession(player_id=player_id, game_id=game.id),
            AnalysisJob(game_id=game.id),
            opportunity,
            Evidence(
                evidence_id="m42-evidence",
                kind="diagnosis",
                position_id=str(position.id),
                engine_analysis_id=str(analysis.id),
                claim="tactics.hanging_piece",
                payload={"confidence": 0.9},
                producer={"name": "test", "version": "1"},
            ),
            ProfileObservation(
                player_id=player_id,
                game_id=str(game.id),
                position_id=str(position.id),
                skill_id="tactics.hanging_piece",
                observed_at=datetime.now(UTC),
            ),
            SkillState(player_id=player_id, concept_code="tactics.hanging_piece"),
            ReviewSchedule(
                player_id=player_id,
                item_id="tactics.hanging_piece",
                next_review_at=datetime.now(UTC),
            ),
            StudySession(id="m42-study-session", player_id=player_id, domain="tactics"),
            ContentItem(
                id="m42-content-item",
                domain="tactics",
                licence="CC0-1.0",
                payload={"fen": position.fen},
                provenance="test",
            ),
            transfer_position,
            CoachStudentLink(coach_id=coach_id, student_id=player_id),
        ]
    )
    session.flush()
    session.add_all(
        [
            ContentAttempt(
                session_id="m42-study-session",
                item_id="m42-content-item",
                player_id=player_id,
            ),
            LessonAttempt(
                session_id="m42-study-session",
                player_id=player_id,
                lesson_id="lesson-m42",
                source_kind="generated",
                opportunity_id=opportunity.id,
                elapsed_ms=250,
                hints_used=0,
                success=True,
                grading_status="graded",
                profile_update_result="applied",
            ),
            TransferMeasurement(
                cohort_id="m42-cohort",
                player_id=player_id,
                skill_id="tactics.hanging_piece",
                measurement_point=MeasurementPoint.PRE_TEST,
                source_position_id=transfer_position.id,
                source_fen=position.fen,
                target_fen=position.fen,
                transfer_kind=TransferKind.NEAR,
            ),
        ]
    )
    session.commit()

    return {
        "analysis_id": analysis.id,
        "game_id": game.id,
        "position_id": position.id,
        "opportunity_id": opportunity.id,
        "transfer_position_id": transfer_position.id,
    }


def test_complete_lifecycle_roundtrip_has_no_player_residual_rows(
    client: TestClient, db_session: Session
) -> None:
    player_id = "m42-lifecycle-player"
    coach_id = "m42-lifecycle-coach"
    token = _create_player_token(client, player_id)
    _create_player_token(client, coach_id)
    records = _seed_player_lifecycle_records(db_session, player_id, coach_id)
    principal = sha256(f"Bearer {token}".encode()).hexdigest()
    db_session.add(
        IdempotencyRecord(
            idempotency_key=f"POST:/v1/example:{principal}:m42-key",
            status_code=201,
            response_body="{}",
            headers="{}",
        )
    )
    db_session.commit()

    export = client.post(
        "/v1/exports",
        json={"player_id": player_id},
        headers=_authorization(token),
    )
    assert export.status_code == 200
    archive = export.json()
    for field in ARCHIVE_FIELDS.values():
        assert field in archive
    assert archive["games"][0]["owner_player_id"] == player_id
    assert archive["evidence"]
    assert archive["profile_observations"]
    assert archive["lesson_attempts"]
    assert archive["transfer_measurements"]
    assert archive["transfer_positions"]
    assert archive["coach_student_links"]

    dry_run = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": True},
        headers=_authorization(token),
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["affected_rows"] == {
        "player": 1,
        "player_credentials": 1,
        "profile": 1,
        "play_sessions": 1,
        "games": 1,
        "games_disowned": 0,
        "positions": 1,
        "engine_analyses": 1,
        "analysis_jobs": 1,
        "lesson_opportunities": 1,
        "evidence": 1,
        "profile_observations": 1,
        "lesson_attempts": 1,
        "transfer_measurements": 1,
        "skill_states": 1,
        "review_schedules": 1,
        "study_sessions": 1,
        "content_attempts": 1,
        "coach_student_links": 1,
    }

    deletion = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{player_id}"},
        headers={**_authorization(token), "Idempotency-Key": "m42-delete"},
    )
    assert deletion.status_code == 200
    assert deletion.json()["affected_rows"]["player"] == 1
    audit_id = deletion.json()["audit_id"]
    db_session.expire_all()
    assert db_session.get(DeletionAudit, audit_id) is not None

    residual_counts = {
        "player": db_session.exec(select(Player).where(Player.id == player_id)).all(),
        "profile": db_session.exec(
            select(PlayerProfile).where(PlayerProfile.player_id == player_id)
        ).all(),
        "credential": db_session.exec(
            select(PlayerCredential).where(PlayerCredential.player_id == player_id)
        ).all(),
        "game": db_session.exec(
            select(Game).where(Game.owner_player_id == player_id)
        ).all(),
        "play_session": db_session.exec(
            select(PlaySession).where(PlaySession.player_id == player_id)
        ).all(),
        "position": db_session.exec(
            select(Position).where(Position.game_id == records["game_id"])
        ).all(),
        "engine_analysis": db_session.exec(
            select(EngineAnalysis).where(EngineAnalysis.position_id == records["position_id"])
        ).all(),
        "analysis_job": db_session.exec(
            select(AnalysisJob).where(AnalysisJob.game_id == records["game_id"])
        ).all(),
        "lesson_opportunity": db_session.exec(
            select(PersistedLessonOpportunity).where(
                PersistedLessonOpportunity.player_id == player_id
            )
        ).all(),
        "evidence": db_session.exec(
            select(Evidence).where(Evidence.position_id == str(records["position_id"]))
        ).all(),
        "profile_observation": db_session.exec(
            select(ProfileObservation).where(ProfileObservation.player_id == player_id)
        ).all(),
        "skill_state": db_session.exec(
            select(SkillState).where(SkillState.player_id == player_id)
        ).all(),
        "review_schedule": db_session.exec(
            select(ReviewSchedule).where(ReviewSchedule.player_id == player_id)
        ).all(),
        "study_session": db_session.exec(
            select(StudySession).where(StudySession.player_id == player_id)
        ).all(),
        "content_attempt": db_session.exec(
            select(ContentAttempt).where(ContentAttempt.player_id == player_id)
        ).all(),
        "lesson_attempt": db_session.exec(
            select(LessonAttempt).where(LessonAttempt.player_id == player_id)
        ).all(),
        "transfer_measurement": db_session.exec(
            select(TransferMeasurement).where(TransferMeasurement.player_id == player_id)
        ).all(),
        "coach_link": db_session.exec(
            select(CoachStudentLink).where(
                (CoachStudentLink.coach_id == player_id)
                | (CoachStudentLink.student_id == player_id)
            )
        ).all(),
        "idempotency": db_session.exec(
            select(IdempotencyRecord).where(
                col(IdempotencyRecord.idempotency_key).contains(f":{principal}:")
            )
        ).all(),
    }
    assert not {
        name: len(rows) for name, rows in residual_counts.items() if rows
    }, db_session.connection().exec_driver_sql(
        "SELECT id FROM player WHERE id = :player_id", {"player_id": player_id}
    ).all()
    tampered_archive = deepcopy(archive)
    tampered_archive["transfer_positions"][0]["id"] = "untrusted-transfer-position"
    tampered_archive["transfer_measurements"][0][
        "source_position_id"
    ] = "untrusted-transfer-position"
    rejected = client.post(
        "/v1/imports", json=tampered_archive, headers=_authorization(token)
    )
    assert rejected.status_code == 400
    assert db_session.get(TransferPosition, records["transfer_position_id"]) is not None
    restored = client.post(
        "/v1/imports", json=archive, headers=_authorization(token)
    )
    assert restored.status_code == 200
    assert db_session.get(Player, player_id) is not None
    assert db_session.get(Game, records["game_id"]) is not None
    assert db_session.get(Position, records["position_id"]) is not None
    assert db_session.get(PersistedLessonOpportunity, records["opportunity_id"]) is not None


def test_deletion_preserves_shared_game_for_its_other_participant(
    client: TestClient, db_session: Session
) -> None:
    owner_id = "m42-shared-owner"
    participant_id = "m42-shared-participant"
    owner_token = _create_player_token(client, owner_id)
    participant_token = _create_player_token(client, participant_id)
    game = Game(
        pgn=f"1. {owner_id} e5",
        owner_player_id=owner_id,
        white=owner_id,
        black=participant_id,
    )
    db_session.add(game)
    db_session.flush()
    position = Position(
        game_id=game.id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="m42-shared-position",
    )
    db_session.add(position)
    db_session.flush()
    db_session.add_all(
        [
            PlaySession(player_id=owner_id, game_id=game.id),
            PlaySession(player_id=participant_id, game_id=game.id),
            PersistedLessonOpportunity(
                game_id=game.id,
                source_position_id=position.id,
                player_id=owner_id,
                lesson_spec={
                    "diagnosis": {
                        "primary": "tactics.hanging_piece",
                        "secondary": [],
                        "confidence": 0.9,
                    }
                },
            ),
        ]
    )
    db_session.commit()

    deletion = client.request(
        "DELETE",
        f"/v1/players/{owner_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{owner_id}"},
        headers=_authorization(owner_token),
    )

    assert deletion.status_code == 200
    assert deletion.json()["affected_rows"]["games_disowned"] == 1
    assert deletion.headers["Cache-Control"] == "no-store"
    db_session.expire_all()
    preserved_game = db_session.get(Game, game.id)
    assert preserved_game is not None
    assert preserved_game.owner_player_id is None
    assert owner_id not in (preserved_game.white, preserved_game.black, preserved_game.pgn)
    assert not db_session.exec(
        select(PersistedLessonOpportunity).where(
            PersistedLessonOpportunity.player_id == owner_id
        )
    ).all()
    assert (
        db_session.exec(
            select(PlaySession).where(
                PlaySession.player_id == participant_id,
                PlaySession.game_id == game.id,
            )
        ).one()
        is not None
    )

    participant_export = client.post(
        "/v1/exports",
        json={"player_id": participant_id},
        headers=_authorization(participant_token),
    )
    assert participant_export.status_code == 200
    participant_archive = participant_export.json()
    assert participant_archive["games"] == []
    assert participant_archive["play_sessions"][0]["game_id"] is None
    participant_deletion = client.request(
        "DELETE",
        f"/v1/players/{participant_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{participant_id}"},
        headers=_authorization(participant_token),
    )
    assert participant_deletion.status_code == 200
    participant_restore = client.post(
        "/v1/imports",
        json=participant_archive,
        headers=_authorization(participant_token),
    )
    assert participant_restore.status_code == 200

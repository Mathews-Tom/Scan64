from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION
from scan64.learning.evidence.models import Evidence
from scan64.learning.profiling.models import SkillState


def create_source_position(db_session: Session, game_id: UUID) -> Position:
    position = Position(
        game_id=game_id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="initial",
    )
    db_session.add(position)
    db_session.flush()
    return position


def create_report_lesson(
    db_session: Session,
    game: Game,
    diagnosis: str,
    evidence_ref: str | None = None,
    *,
    marked: bool = True,
) -> PersistedLessonOpportunity:
    source = create_source_position(db_session, game.id)
    evidence_ref = evidence_ref or f"report-proof-{source.id}"
    db_session.add(
        Evidence(
            evidence_id=evidence_ref,
            kind="missed_opportunity",
            position_id=str(source.id),
            engine_analysis_id="report-analysis",
            claim="Report-layer diagnosis fixture",
            payload={"diagnosis_rule_version": DIAGNOSIS_RULE_VERSION} if marked else {},
        )
    )
    opportunity = PersistedLessonOpportunity(
        game_id=game.id,
        player_id=game.owner_player_id,
        source_position_id=source.id,
        lesson_spec={
            "schema_version": "1.0",
            "lesson_id": str(source.id),
            "source": {"kind": "custom", "fen": source.fen},
            "diagnosis": {"primary": diagnosis, "confidence": 1.0, "evidence_refs": [evidence_ref]},
            "objective": {"type": "find_best_move", "instruction": "Play e4."},
            "interaction": {
                "input": "click",
                "maximum_attempts": 3,
                "accepted_moves": [{"san": "e4"}],
            },
            "verification": {"status": "verified", "engine": "test"},
            "hints": [],
            "explanation": {"text": "Develop the centre."},
        },
    )
    db_session.add(opportunity)
    return opportunity


def create_player_token(client: TestClient, player_id: str) -> str:
    response = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": player_id, "preferences": {}},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


def test_patterns_reports_recurring_diagnosis_from_owned_games(
    client: TestClient, db_session: Session
) -> None:
    player_id = "pattern-player"
    token = create_player_token(client, player_id)
    evidence_refs = ["evidence-1", "evidence-2", "evidence-3"]
    game_ids: list[str] = []
    for evidence_ref in evidence_refs:
        game = Game(
            pgn="1. e4 e5",
            moves=["e2e4", "e7e5"],
            owner_player_id=player_id,
        )
        db_session.add(game)
        db_session.flush()
        game_ids.append(str(game.id))
        create_report_lesson(db_session, game, "threat_processing.missed_capture", evidence_ref)
    other_game = Game(pgn="1. d4 d5", owner_player_id="another-player")
    db_session.add(other_game)
    db_session.flush()
    db_session.add(
        PersistedLessonOpportunity(
            game_id=other_game.id,
            source_position_id=create_source_position(db_session, other_game.id).id,
            lesson_spec={
                "diagnosis": {
                    "primary": "tactics.fork.knight",
                    "evidence_refs": ["foreign-evidence"],
                }
            },
        )
    )
    db_session.commit()

    response = client.get(
        f"/v1/players/{player_id}/patterns",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "player_id": player_id,
        "minimum_occurrences": 3,
        "status": "recurring_diagnosis",
        "recurring_diagnoses": [
            {
                "diagnosis": "threat_processing.missed_capture",
                "name": "Missed capture",
                "occurrence_count": 3,
                "game_ids": sorted(game_ids),
                "evidence_references": evidence_refs,
            }
        ],
    }


def test_patterns_distinguishes_insufficient_data_from_no_recurrence(
    client: TestClient, db_session: Session
) -> None:
    player_id = "sparse-player"
    token = create_player_token(client, player_id)
    for diagnosis in ("threat_processing.missed_capture", "threat_processing.missed_check"):
        game = Game(pgn="1. e4 e5", owner_player_id=player_id)
        db_session.add(game)
        db_session.flush()
        create_report_lesson(db_session, game, diagnosis)
    db_session.commit()

    sparse = client.get(
        f"/v1/players/{player_id}/patterns",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sparse.json()["status"] == "insufficient_data"

    game = Game(pgn="1. e4 e5", owner_player_id=player_id)
    db_session.add(game)
    db_session.flush()
    create_report_lesson(db_session, game, "threat_processing.missed_direct_threat")
    db_session.commit()

    no_recurrence = client.get(
        f"/v1/players/{player_id}/patterns",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert no_recurrence.json()["status"] == "no_recurring_diagnosis"


def test_openings_report_uses_owned_game_families(client: TestClient, db_session: Session) -> None:
    player_id = "opening-player"
    token = create_player_token(client, player_id)
    game = Game(
        pgn="1. e4 e5 2. Nf3 Nc6 3. Bc4",
        moves=["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"],
        owner_player_id=player_id,
        white=player_id,
        black="Opponent",
        result="1-0",
    )
    imported = Game(
        pgn="1. e4 e5 2. Nf3 Nc6 3. Bc4",
        moves=["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"],
        owner_player_id=player_id,
        white="Imported White",
        black="Imported Black",
        result="0-1",
    )
    db_session.add_all([game, imported])
    db_session.flush()
    create_report_lesson(db_session, game, "threat_processing.missed_capture")
    db_session.commit()

    response = client.get(
        f"/v1/reports/openings?player_id={player_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["openings"] == [
        {
            "family_id": "italian",
            "name": "Italian Game",
            "game_count": 2,
            "error_rate": 0.5,
            "eligible_result_count": 1,
            "excluded_result_count": 1,
            "win_rate": 1.0,
        }
    ]


def test_weekly_report_excludes_retired_mastery(client: TestClient, db_session: Session) -> None:
    player_id = "weekly-player"
    token = create_player_token(client, player_id)
    db_session.add(Game(pgn="1. e4 e5", owner_player_id=player_id))
    db_session.add(
        SkillState(player_id=player_id, concept_code="tactics.fork.knight", alpha=3, beta=1)
    )
    db_session.add(
        SkillState(
            player_id=player_id,
            concept_code="retired.concept",
            alpha=9,
            beta=1,
            retired_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    response = client.get(
        f"/v1/reports/weekly?player_id={player_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    report = response.json()
    assert report["games_played"] == 1
    assert report["active_concepts_observed"] == 1
    assert report["active_mastery"] == [
        {"concept_code": "tactics.fork.knight", "name": "Knight fork", "mastery": 0.75}
    ]
    assert report["top_recurring_diagnosis"] is None


def test_weekly_report_selects_most_frequent_recurring_diagnosis(
    client: TestClient, db_session: Session
) -> None:
    player_id = "weekly-recurring-player"
    token = create_player_token(client, player_id)
    for diagnosis, count in (
        ("threat_processing.missed_capture", 4),
        ("threat_processing.missed_check", 3),
    ):
        for _ in range(count):
            game = Game(pgn="1. e4 e5", owner_player_id=player_id)
            db_session.add(game)
            db_session.flush()
            create_report_lesson(db_session, game, diagnosis)
    db_session.commit()

    response = client.get(
        f"/v1/reports/weekly?player_id={player_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert (
        response.json()["top_recurring_diagnosis"]["diagnosis"]
        == "threat_processing.missed_capture"
    )
    assert response.json()["top_recurring_diagnosis"]["name"] == "Missed capture"


def test_progress_reports_taxonomy_names_for_stored_skills(
    client: TestClient, db_session: Session
) -> None:
    player_id = "progress-player"
    token = create_player_token(client, player_id)
    db_session.add_all(
        [
            SkillState(
                player_id=player_id,
                concept_code="board_awareness.hanging_piece",
                alpha=1,
                beta=3,
            ),
            SkillState(player_id=player_id, concept_code="legacy.unknown"),
        ]
    )
    db_session.commit()

    response = client.get(
        f"/v1/players/{player_id}/progress",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    skills = {skill["concept"]: skill for skill in response.json()["skills"]}
    assert skills["board_awareness.hanging_piece"] == {
        "concept": "board_awareness.hanging_piece",
        "name": "Hanging piece",
        "mastery": 0.25,
        "uncertainty": 0.0375,
    }
    assert skills["legacy.unknown"]["name"] == "Unclassified skill"


@pytest.mark.parametrize("diagnosis", [*sorted(TRUSTED_CODES), "tactics.pin"])
def test_summaries_exclude_markerless_legacy_and_untrusted_diagnoses(
    client: TestClient, db_session: Session, diagnosis: str
) -> None:
    player_id = "hidden-report-player"
    token = create_player_token(client, player_id)
    lessons = []
    for _ in range(3):
        game = Game(
            pgn="1. e4 e5 2. Nf3 Nc6 3. Bc4",
            moves=["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"],
            owner_player_id=player_id,
        )
        db_session.add(game)
        db_session.flush()
        lessons.append(
            create_report_lesson(db_session, game, diagnosis, marked=diagnosis == "tactics.pin")
        )
    db_session.commit()
    headers = {"Authorization": f"Bearer {token}"}
    patterns = client.get(f"/v1/players/{player_id}/patterns", headers=headers)
    assert patterns.status_code == 200
    assert patterns.json()["status"] == "insufficient_data"
    assert patterns.json()["recurring_diagnoses"] == []
    openings = client.get(f"/v1/reports/openings?player_id={player_id}", headers=headers)
    assert openings.status_code == 200
    assert openings.json()["openings"][0]["error_rate"] == 0.0
    weekly = client.get(f"/v1/reports/weekly?player_id={player_id}", headers=headers)
    assert weekly.status_code == 200
    assert weekly.json()["games_played"] == 3
    assert weekly.json()["top_recurring_diagnosis"] is None
    for lesson in lessons:
        retained = db_session.get(PersistedLessonOpportunity, lesson.id)
        assert retained is not None and retained.lesson_spec["diagnosis"]["primary"] == diagnosis

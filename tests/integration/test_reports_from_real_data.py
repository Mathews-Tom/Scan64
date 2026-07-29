from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
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
        db_session.add(
            PersistedLessonOpportunity(
                game_id=game.id,
                source_position_id=create_source_position(db_session, game.id).id,
                lesson_spec={
                    "diagnosis": {
                        "primary": "tactics.fork.knight",
                        "evidence_refs": [evidence_ref],
                    }
                },
            )
        )
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
                "diagnosis": "tactics.fork.knight",
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
    for diagnosis in ("tactics.pin", "tactics.fork.knight"):
        game = Game(pgn="1. e4 e5", owner_player_id=player_id)
        db_session.add(game)
        db_session.flush()
        db_session.add(
            PersistedLessonOpportunity(
                game_id=game.id,
                source_position_id=create_source_position(db_session, game.id).id,
                lesson_spec={"diagnosis": {"primary": diagnosis, "evidence_refs": []}},
            )
        )
    db_session.commit()

    sparse = client.get(
        f"/v1/players/{player_id}/patterns",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sparse.json()["status"] == "insufficient_data"

    game = Game(pgn="1. e4 e5", owner_player_id=player_id)
    db_session.add(game)
    db_session.flush()
    db_session.add(
        PersistedLessonOpportunity(
            game_id=game.id,
            source_position_id=create_source_position(db_session, game.id).id,
            lesson_spec={
                "diagnosis": {"primary": "opening.delayed_development", "evidence_refs": []}
            },
        )
    )
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
            "error_rate": 0.0,
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
        {"concept_code": "tactics.fork.knight", "mastery": 0.75}
    ]
    assert report["top_recurring_diagnosis"] is None


def test_weekly_report_selects_most_frequent_recurring_diagnosis(
    client: TestClient, db_session: Session
) -> None:
    player_id = "weekly-recurring-player"
    token = create_player_token(client, player_id)
    for diagnosis, count in (("aaa.major", 4), ("zzz.minor", 3)):
        for _ in range(count):
            game = Game(pgn="1. e4 e5", owner_player_id=player_id)
            db_session.add(game)
            db_session.flush()
            db_session.add(
                PersistedLessonOpportunity(
                    game_id=game.id,
                    source_position_id=create_source_position(db_session, game.id).id,
                    lesson_spec={
                        "diagnosis": {"primary": diagnosis, "evidence_refs": []}
                    },
                )
            )
    db_session.commit()

    response = client.get(
        f"/v1/reports/weekly?player_id={player_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["top_recurring_diagnosis"]["diagnosis"] == "aaa.major"

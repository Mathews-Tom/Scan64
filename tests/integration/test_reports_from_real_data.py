from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game


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

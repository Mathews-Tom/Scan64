from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.learning.evidence.models import Evidence


def create_player_token(client: TestClient, player_id: str, display_name: str) -> str:
    response = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": display_name},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def authorization_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def link_student_to_coach(
    client: TestClient,
    coach_id: str,
    student_id: str,
    student_token: str,
) -> None:
    response = client.post(
        f"/v1/coaches/{coach_id}/students/{student_id}",
        headers=authorization_header(student_token),
    )
    assert response.status_code == 201


def add_student_evidence(db_session: Session, player_id: str) -> None:
    game = Game(pgn="1. e4 e5", white="Student", black="Opponent")
    db_session.add(game)
    db_session.flush()
    db_session.add(PlaySession(player_id=player_id, game_id=game.id, status="completed"))

    position = Position(
        game_id=game.id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        canonical_id="initial-position",
        side_to_move="w",
    )
    db_session.add(position)
    db_session.flush()
    db_session.add(
        Evidence(
            evidence_id="student-evidence-1",
            kind="tactical-motif",
            position_id=str(position.id),
            engine_analysis_id="analysis-1",
            claim="Missed a knight fork",
            payload={"motif": "knight-fork"},
            producer={"name": "scan64", "version": "1"},
        )
    )
    db_session.commit()


def test_coach_dashboard_aggregates_linked_student_public_contracts(
    client: TestClient, db_session: Session
) -> None:
    coach_token = create_player_token(client, "coach-1", "Coach")
    student_token = create_player_token(client, "student-1", "Student")
    link_student_to_coach(client, "coach-1", "student-1", student_token)
    add_student_evidence(db_session, "student-1")

    dashboard_response = client.get(
        "/v1/coaches/coach-1/dashboard",
        headers=authorization_header(coach_token),
    )
    profile_response = client.get(
        "/v1/players/student-1/profile",
        headers=authorization_header(student_token),
    )
    patterns_response = client.get(
        "/v1/players/student-1/patterns",
        headers=authorization_header(student_token),
    )
    evidence_response = client.get(
        "/v1/players/student-1/evidence",
        headers=authorization_header(student_token),
    )

    assert dashboard_response.status_code == 200
    assert profile_response.status_code == 200
    assert patterns_response.status_code == 200
    assert evidence_response.status_code == 200
    students = dashboard_response.json()["students"]
    assert len(students) == 1
    assert students[0]["student_id"] == "student-1"
    assert students[0]["profile"] == profile_response.json()
    assert students[0]["patterns"] == patterns_response.json()
    assert students[0]["evidence"] == evidence_response.json()
    assert students[0]["evidence"]["evidence_items"] == [
        {
            "evidence_id": "student-evidence-1",
            "kind": "tactical-motif",
            "position_id": students[0]["evidence"]["evidence_items"][0]["position_id"],
            "claim": "Missed a knight fork",
            "payload": {"motif": "knight-fork"},
            "producer": {"name": "scan64", "version": "1"},
        }
    ]
    assert "engine_analysis_id" not in students[0]["evidence"]["evidence_items"][0]


def test_coach_dashboard_requires_the_coach_bearer_token(
    client: TestClient, db_session: Session
) -> None:
    create_player_token(client, "coach-1", "Coach")
    student_token = create_player_token(client, "student-1", "Student")
    link_student_to_coach(client, "coach-1", "student-1", student_token)
    add_student_evidence(db_session, "student-1")

    response = client.get("/v1/coaches/coach-1/dashboard")

    assert response.status_code == 401


def test_player_reports_reject_a_different_players_bearer_token(client: TestClient) -> None:
    create_player_token(client, "student-1", "Student")
    other_player_token = create_player_token(client, "other-player", "Other Player")

    for endpoint in ("profile", "progress", "patterns", "evidence"):
        response = client.get(
            f"/v1/players/student-1/{endpoint}",
            headers=authorization_header(other_player_token),
        )
        assert response.status_code == 403


def test_coach_dashboard_excludes_a_deleted_student(client: TestClient) -> None:
    coach_token = create_player_token(client, "coach-1", "Coach")
    student_token = create_player_token(client, "student-1", "Student")
    link_student_to_coach(client, "coach-1", "student-1", student_token)

    deletion_response = client.request(
        "DELETE",
        "/v1/players/student-1/data",
        json={"dry_run": False, "confirmation": "delete-student-1"},
        headers=authorization_header(student_token),
    )
    assert deletion_response.status_code == 200

    dashboard_response = client.get(
        "/v1/coaches/coach-1/dashboard",
        headers=authorization_header(coach_token),
    )
    assert dashboard_response.status_code == 200
    assert dashboard_response.json()["students"] == []

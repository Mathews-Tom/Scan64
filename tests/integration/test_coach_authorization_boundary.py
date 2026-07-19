from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.coach.models import CoachStudentLink
from scan64.learning.evidence.models import Evidence


def create_player_token(client: TestClient, player_id: str) -> str:
    response = client.post("/v1/players", json={"id": player_id})
    assert response.status_code == 200
    return response.json()["access_token"]


def authorization_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_student_explicitly_authorizes_coach_linkage(
    client: TestClient, db_session: Session
) -> None:
    create_player_token(client, "coach-1")
    student_token = create_player_token(client, "student-1")

    response = client.post(
        "/v1/coaches/coach-1/students/student-1",
        headers=authorization_header(student_token),
    )

    assert response.status_code == 201
    assert response.json()["coach_id"] == "coach-1"
    assert response.json()["student_id"] == "student-1"
    assert db_session.get(CoachStudentLink, ("coach-1", "student-1")) is not None


def test_linkage_rejects_a_different_students_token(
    client: TestClient, db_session: Session
) -> None:
    create_player_token(client, "coach-1")
    create_player_token(client, "student-1")
    other_student_token = create_player_token(client, "student-2")

    response = client.post(
        "/v1/coaches/coach-1/students/student-1",
        headers=authorization_header(other_student_token),
    )

    assert response.status_code == 403
    assert db_session.get(CoachStudentLink, ("coach-1", "student-1")) is None


def add_player_evidence(db_session: Session, player_id: str, evidence_id: str) -> None:
    game = Game(pgn="1. e4 e5", white=player_id, black="Opponent")
    db_session.add(game)
    db_session.flush()
    db_session.add(PlaySession(player_id=player_id, game_id=game.id, status="completed"))
    position = Position(
        game_id=game.id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        canonical_id=f"{player_id}-initial-position",
        side_to_move="w",
    )
    db_session.add(position)
    db_session.flush()
    db_session.add(
        Evidence(
            evidence_id=evidence_id,
            kind="tactical-motif",
            position_id=str(position.id),
            engine_analysis_id=f"{evidence_id}-analysis",
            claim=f"{player_id} missed a knight fork",
        )
    )
    db_session.commit()


def test_dashboard_excludes_an_unlinked_students_patterns_and_evidence(
    client: TestClient, db_session: Session
) -> None:
    coach_token = create_player_token(client, "coach-1")
    create_player_token(client, "coach-2")
    linked_student_token = create_player_token(client, "linked-student")
    unlinked_student_token = create_player_token(client, "unlinked-student")
    linked_response = client.post(
        "/v1/coaches/coach-1/students/linked-student",
        headers=authorization_header(linked_student_token),
    )
    assert linked_response.status_code == 201
    other_link_response = client.post(
        "/v1/coaches/coach-2/students/unlinked-student",
        headers=authorization_header(unlinked_student_token),
    )
    assert other_link_response.status_code == 201
    add_player_evidence(db_session, "linked-student", "linked-evidence")
    add_player_evidence(db_session, "unlinked-student", "unlinked-evidence")

    response = client.get(
        "/v1/coaches/coach-1/dashboard",
        headers=authorization_header(coach_token),
    )

    assert response.status_code == 200
    dashboard = response.json()
    assert [student["student_id"] for student in dashboard["students"]] == ["linked-student"]
    assert dashboard["students"][0]["patterns"]["player_id"] == "linked-student"
    assert [
        item["evidence_id"] for item in dashboard["students"][0]["evidence"]["evidence_items"]
    ] == ["linked-evidence"]

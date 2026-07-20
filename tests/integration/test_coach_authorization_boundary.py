from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.coach.models import CoachStudentLink


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

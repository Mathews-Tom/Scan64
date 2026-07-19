from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import DeletionAudit, Player, PlayerCredential, PlayerProfile
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.coach.models import CoachStudentLink


def create_game_records(session: Session, player_id: str) -> dict[str, object]:
    game = Game(pgn="1. e4 e5")
    session.add(game)
    session.commit()
    session.refresh(game)

    play_session = PlaySession(player_id=player_id, game_id=game.id)
    position = Position(
        game_id=game.id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="initial-position",
    )
    session.add(play_session)
    session.add(position)
    session.commit()
    session.refresh(position)

    engine_analysis = EngineAnalysis(position_id=position.id)
    analysis_job = AnalysisJob(game_id=game.id)
    lesson_opportunity = PersistedLessonOpportunity(
        game_id=game.id,
        lesson_spec={"id": "lesson-opportunity"},
    )
    session.add(engine_analysis)
    session.add(analysis_job)
    session.add(lesson_opportunity)
    session.commit()

    return {
        "game": game.id,
        "play_session": play_session.id,
        "position": position.id,
        "engine_analysis": engine_analysis.id,
        "analysis_job": analysis_job.id,
        "lesson_opportunity": lesson_opportunity.id,
    }


def create_player_token(client: TestClient, player_id: str, display_name: str) -> str:
    response = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": display_name, "preferences": {}},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def authorization_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_export_import_roundtrip(client: TestClient, db_session: Session):
    player_id = "test-export-user"
    access_token = create_player_token(client, player_id, "Test User")
    record_ids = create_game_records(db_session, player_id)

    response = client.post(
        "/v1/exports",
        json={"player_id": player_id},
        headers=authorization_header(access_token),
    )
    assert response.status_code == 200
    archive = response.json()

    assert archive["player"]["id"] == player_id
    assert archive["credential_hash"] != access_token
    assert archive["profile"]["display_name"] == "Test User"
    assert len(archive["play_sessions"]) == 1
    assert len(archive["games"]) == 1
    assert len(archive["positions"]) == 1
    assert len(archive["engine_analyses"]) == 1
    assert len(archive["analysis_jobs"]) == 1
    assert len(archive["lesson_opportunities"]) == 1

    response = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{player_id}"},
        headers=authorization_header(access_token),
    )
    assert response.status_code == 200
    assert db_session.get(Player, player_id) is None
    assert db_session.get(PlayerCredential, player_id) is None
    assert db_session.get(Game, record_ids["game"]) is None
    assert db_session.get(Position, record_ids["position"]) is None
    assert db_session.get(EngineAnalysis, record_ids["engine_analysis"]) is None
    assert db_session.get(AnalysisJob, record_ids["analysis_job"]) is None
    assert db_session.get(PersistedLessonOpportunity, record_ids["lesson_opportunity"]) is None

    response = client.post(
        "/v1/imports",
        json=archive,
        headers=authorization_header(access_token),
    )
    assert response.status_code == 200
    assert db_session.get(Player, player_id) is not None
    assert db_session.get(PlayerProfile, player_id).display_name == "Test User"
    assert db_session.get(Game, record_ids["game"]) is not None
    assert db_session.get(PlaySession, record_ids["play_session"]) is not None
    assert db_session.get(Position, record_ids["position"]) is not None
    assert db_session.get(EngineAnalysis, record_ids["engine_analysis"]) is not None
    assert db_session.get(AnalysisJob, record_ids["analysis_job"]) is not None
    assert db_session.get(PersistedLessonOpportunity, record_ids["lesson_opportunity"]) is not None
    assert db_session.get(PlayerCredential, player_id) is not None


def test_deletion_dry_run_reports_complete_owned_data(client: TestClient, db_session: Session):
    player_id = "test-delete-user"
    access_token = create_player_token(client, player_id, "Delete User")
    record_ids = create_game_records(db_session, player_id)

    response = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": True},
        headers=authorization_header(access_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["dry_run"] is True
    assert data["affected_rows"] == {
        "player": 1,
        "profile": 1,
        "play_sessions": 1,
        "games": 1,
        "positions": 1,
        "engine_analyses": 1,
        "analysis_jobs": 1,
        "lesson_opportunities": 1,
        "skill_states": 0,
        "review_schedules": 0,
        "study_sessions": 0,
        "content_attempts": 0,
        "coach_student_links": 0,
    }
    assert data["audit_id"] is None
    assert db_session.get(Player, player_id) is not None
    assert db_session.get(Game, record_ids["game"]) is not None


def test_deletion_confirmed_removes_complete_owned_data(client: TestClient, db_session: Session):
    player_id = "test-delete-confirmed"
    access_token = create_player_token(client, player_id, "Delete User")
    record_ids = create_game_records(db_session, player_id)

    response = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": False},
        headers=authorization_header(access_token),
    )
    assert response.status_code == 400

    response = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{player_id}"},
        headers=authorization_header(access_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["dry_run"] is False
    assert data["affected_rows"]["player"] == 1
    assert data["audit_id"] is not None
    assert db_session.get(Player, player_id) is None
    assert db_session.get(PlayerProfile, player_id) is None
    assert db_session.get(Game, record_ids["game"]) is None
    assert db_session.get(PlaySession, record_ids["play_session"]) is None
    assert db_session.get(Position, record_ids["position"]) is None
    assert db_session.get(EngineAnalysis, record_ids["engine_analysis"]) is None
    assert db_session.get(AnalysisJob, record_ids["analysis_job"]) is None
    assert db_session.get(PersistedLessonOpportunity, record_ids["lesson_opportunity"]) is None

    audit = db_session.get(DeletionAudit, data["audit_id"])
    assert audit is not None
    assert audit.player_id == player_id
    assert audit.affected_rows == data["affected_rows"]



def test_deletion_removes_coach_student_links(client: TestClient, db_session: Session):
    coach_id = "test-delete-coach"
    student_id = "test-delete-student"
    create_player_token(client, coach_id, "Coach")
    student_token = create_player_token(client, student_id, "Student")
    link_response = client.post(
        f"/v1/coaches/{coach_id}/students/{student_id}",
        headers=authorization_header(student_token),
    )
    assert link_response.status_code == 201

    dry_run = client.request(
        "DELETE",
        f"/v1/players/{student_id}/data",
        json={"dry_run": True},
        headers=authorization_header(student_token),
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["affected_rows"]["coach_student_links"] == 1
    assert db_session.get(CoachStudentLink, (coach_id, student_id)) is not None

    response = client.request(
        "DELETE",
        f"/v1/players/{student_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{student_id}"},
        headers=authorization_header(student_token),
    )
    assert response.status_code == 200
    assert response.json()["affected_rows"]["coach_student_links"] == 1
    assert db_session.get(CoachStudentLink, (coach_id, student_id)) is None


def test_deletion_removes_coach_student_links_when_coach_is_deleted(
    client: TestClient, db_session: Session
):
    coach_id = "test-delete-coach"
    student_id = "test-keep-student"
    coach_token = create_player_token(client, coach_id, "Coach")
    student_token = create_player_token(client, student_id, "Student")
    link_response = client.post(
        f"/v1/coaches/{coach_id}/students/{student_id}",
        headers=authorization_header(student_token),
    )
    assert link_response.status_code == 201

    response = client.request(
        "DELETE",
        f"/v1/players/{coach_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{coach_id}"},
        headers=authorization_header(coach_token),
    )
    assert response.status_code == 200
    assert response.json()["affected_rows"]["coach_student_links"] == 1
    assert db_session.get(CoachStudentLink, (coach_id, student_id)) is None

def test_deletion_preserves_shared_game_for_other_player(client: TestClient, db_session: Session):
    deleting_player_id = "test-delete-shared"
    remaining_player_id = "test-keep-shared"
    access_tokens = {
        player_id: create_player_token(client, player_id, player_id)
        for player_id in (deleting_player_id, remaining_player_id)
    }

    game = Game(pgn="1. e4 e5")
    db_session.add(game)
    db_session.commit()
    db_session.refresh(game)
    deleting_session = PlaySession(player_id=deleting_player_id, game_id=game.id)
    remaining_session = PlaySession(player_id=remaining_player_id, game_id=game.id)
    db_session.add(deleting_session)
    db_session.add(remaining_session)
    db_session.commit()

    response = client.request(
        "DELETE",
        f"/v1/players/{deleting_player_id}/data",
        json={
            "dry_run": False,
            "confirmation": f"delete-{deleting_player_id}",
        },
        headers=authorization_header(access_tokens[deleting_player_id]),
    )
    assert response.status_code == 200
    assert response.json()["affected_rows"]["games"] == 0
    assert db_session.get(Game, game.id) is not None
    assert db_session.get(PlaySession, deleting_session.id) is None
    assert db_session.get(PlaySession, remaining_session.id) is not None


def test_lifecycle_rejects_requests_without_player_token(client: TestClient, db_session: Session):
    player_id = "test-token-required"
    response = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": "Protected User", "preferences": {}},
    )
    assert response.status_code == 200
    create_game_records(db_session, player_id)

    response = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": True},
    )

    assert response.status_code == 401


def test_lifecycle_rejects_other_player_token(client: TestClient, db_session: Session):
    victim_id = "test-token-victim"
    victim_token = create_player_token(client, victim_id, "Victim")
    other_token = create_player_token(client, "test-token-other", "Other")
    record_ids = create_game_records(db_session, victim_id)

    response = client.post(
        "/v1/exports",
        json={"player_id": victim_id},
        headers=authorization_header(other_token),
    )
    assert response.status_code == 403

    response = client.request(
        "DELETE",
        f"/v1/players/{victim_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{victim_id}"},
        headers=authorization_header(other_token),
    )
    assert response.status_code == 403
    assert db_session.get(Player, victim_id) is not None
    assert db_session.get(Game, record_ids["game"]) is not None

    response = client.request(
        "DELETE",
        f"/v1/players/{victim_id}/data",
        json={"dry_run": True},
        headers=authorization_header(victim_token),
    )
    assert response.status_code == 200


def test_import_rejects_cross_player_records(client: TestClient, db_session: Session):
    player_id = "test-import-owner"
    access_token = create_player_token(client, player_id, "Import Owner")
    victim_id = "test-import-victim"
    create_player_token(client, victim_id, "Victim")

    response = client.post(
        "/v1/exports",
        json={"player_id": player_id},
        headers=authorization_header(access_token),
    )
    assert response.status_code == 200
    archive = response.json()
    archive["skill_states"] = [{"player_id": victim_id, "concept_code": "tactics.fork"}]

    response = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{player_id}"},
        headers=authorization_header(access_token),
    )
    assert response.status_code == 200

    response = client.post(
        "/v1/imports",
        json=archive,
        headers=authorization_header(access_token),
    )

    assert response.status_code == 400


def test_lifecycle_does_not_reveal_player_existence_without_token(client: TestClient):
    response = client.post("/v1/exports", json={"player_id": "missing-player"})
    assert response.status_code == 401

    response = client.request(
        "DELETE",
        "/v1/players/missing-player/data",
        json={"dry_run": True},
    )
    assert response.status_code == 401


def test_reports_endpoints(client: TestClient, db_session: Session):
    player_id = "test-reports-user"
    player_response = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": "Reports User", "preferences": {}},
    )
    assert player_response.status_code == 200
    headers = authorization_header(player_response.json()["access_token"])

    for endpoint in ("progress", "evidence", "patterns"):
        response = client.get(f"/v1/players/{player_id}/{endpoint}", headers=headers)
        assert response.status_code == 200

    response = client.get(f"/v1/players/{player_id}/evidence")
    assert response.status_code == 401

    resp = client.get(f"/v1/reports/weekly?player_id={player_id}")
    assert resp.status_code == 200

    resp = client.get(f"/v1/reports/openings?player_id={player_id}")
    assert resp.status_code == 200

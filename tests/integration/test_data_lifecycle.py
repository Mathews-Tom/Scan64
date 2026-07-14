from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import DeletionAudit, Player, PlayerProfile
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position


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


def test_export_import_roundtrip(client: TestClient, db_session: Session):
    player_id = "test-export-user"
    response = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": "Test User", "preferences": {}},
    )
    assert response.status_code == 200
    record_ids = create_game_records(db_session, player_id)

    response = client.post("/v1/exports", json={"player_id": player_id})
    assert response.status_code == 200
    archive = response.json()

    assert archive["player"]["id"] == player_id
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
    )
    assert response.status_code == 200
    assert db_session.get(Player, player_id) is None
    assert db_session.get(Game, record_ids["game"]) is None
    assert db_session.get(Position, record_ids["position"]) is None
    assert db_session.get(EngineAnalysis, record_ids["engine_analysis"]) is None
    assert db_session.get(AnalysisJob, record_ids["analysis_job"]) is None
    assert db_session.get(PersistedLessonOpportunity, record_ids["lesson_opportunity"]) is None

    response = client.post("/v1/imports", json=archive)
    assert response.status_code == 200
    assert db_session.get(Player, player_id) is not None
    assert db_session.get(PlayerProfile, player_id).display_name == "Test User"
    assert db_session.get(Game, record_ids["game"]) is not None
    assert db_session.get(PlaySession, record_ids["play_session"]) is not None
    assert db_session.get(Position, record_ids["position"]) is not None
    assert db_session.get(EngineAnalysis, record_ids["engine_analysis"]) is not None
    assert db_session.get(AnalysisJob, record_ids["analysis_job"]) is not None
    assert db_session.get(PersistedLessonOpportunity, record_ids["lesson_opportunity"]) is not None


def test_deletion_dry_run_reports_complete_owned_data(client: TestClient, db_session: Session):
    player_id = "test-delete-user"
    response = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": "Delete User", "preferences": {}},
    )
    assert response.status_code == 200
    record_ids = create_game_records(db_session, player_id)

    response = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": True},
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
    }
    assert data["audit_id"] is None
    assert db_session.get(Player, player_id) is not None
    assert db_session.get(Game, record_ids["game"]) is not None


def test_deletion_confirmed_removes_complete_owned_data(client: TestClient, db_session: Session):
    player_id = "test-delete-confirmed"
    response = client.post(
        "/v1/players",
        json={"id": player_id, "display_name": "Delete User", "preferences": {}},
    )
    assert response.status_code == 200
    record_ids = create_game_records(db_session, player_id)

    response = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": False},
    )
    assert response.status_code == 400

    response = client.request(
        "DELETE",
        f"/v1/players/{player_id}/data",
        json={"dry_run": False, "confirmation": f"delete-{player_id}"},
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


def test_deletion_preserves_shared_game_for_other_player(client: TestClient, db_session: Session):
    deleting_player_id = "test-delete-shared"
    remaining_player_id = "test-keep-shared"
    for player_id in (deleting_player_id, remaining_player_id):
        response = client.post(
            "/v1/players",
            json={"id": player_id, "display_name": player_id, "preferences": {}},
        )
        assert response.status_code == 200

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
    )
    assert response.status_code == 200
    assert response.json()["affected_rows"]["games"] == 0
    assert db_session.get(Game, game.id) is not None
    assert db_session.get(PlaySession, deleting_session.id) is None
    assert db_session.get(PlaySession, remaining_session.id) is not None

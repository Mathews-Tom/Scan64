from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import Player, PlayerProfile
from scan64.chess.games.models import Game, PlaySession

def test_export_import_roundtrip(client: TestClient, db_session: Session):
    # Create player and some data
    player_id = "test-export-user"
    resp = client.post("/v1/players", json={"id": player_id, "display_name": "Test User", "preferences": {}})
    assert resp.status_code == 200
    
    game = Game(pgn="1. e4 e5")
    db_session.add(game)
    db_session.commit()
    db_session.refresh(game)
    
    play_session = PlaySession(player_id=player_id, game_id=game.id)
    db_session.add(play_session)
    db_session.commit()
    
    # 1. Export
    response = client.post(f"/v1/exports", json={"player_id": player_id})
    assert response.status_code == 200
    export_data = response.json()
    
    assert export_data["player"]["id"] == player_id
    assert export_data["profile"]["display_name"] == "Test User"
    assert len(export_data["play_sessions"]) == 1
    assert len(export_data["games"]) == 1
    
    # 2. Delete (simulate deletion for testing import)
    db_session.delete(play_session)
    db_session.delete(game)
    db_session.delete(db_session.get(PlayerProfile, player_id))
    db_session.delete(db_session.get(Player, player_id))
    db_session.commit()
    
    # Verify deleted
    assert not db_session.get(Player, player_id)
    
    # 3. Import
    response = client.post("/v1/imports", json=export_data)
    assert response.status_code == 200
    
    # Verify imported
    assert db_session.get(Player, player_id)
    profile = db_session.get(PlayerProfile, player_id)
    assert profile.display_name == "Test User"

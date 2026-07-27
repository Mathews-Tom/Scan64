from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import Player
from scan64.chess.games.models import Game

PGN = '[Event "Ownership test"]\n[White "White"]\n[Black "Black"]\n[Result "*"]\n\n1. e4 e5 *\n'


def test_imported_game_records_its_existing_owner(client: TestClient, db_session: Session) -> None:
    player = Player(id="import-owner")
    db_session.add(player)
    db_session.commit()

    response = client.post("/v1/games", json={"pgn": PGN, "player_id": player.id})

    assert response.status_code == 200
    game = db_session.get(Game, UUID(response.json()["id"]))
    assert game is not None
    assert game.owner_player_id == player.id


def test_analysis_job_rejects_ownerless_legacy_game(
    client: TestClient, db_session: Session
) -> None:
    game = Game(pgn=PGN)
    db_session.add(game)
    db_session.commit()

    response = client.post(f"/v1/games/{game.id}/analysis-jobs")
    assert response.status_code == 409
    assert response.json()["detail"] == "Game has no owner and cannot be analysed"

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.games.models import Game

PGN = '[Event "Ownership test"]\n[White "White"]\n[Black "Black"]\n[Result "*"]\n\n1. e4 e5 *\n'


def _register(db_session: Session, player_id: str) -> dict[str, str]:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_imported_game_records_its_existing_owner(client: TestClient, db_session: Session) -> None:
    headers = _register(db_session, "import-owner")

    response = client.post(
        "/v1/games", json={"pgn": PGN, "player_id": "import-owner"}, headers=headers
    )

    assert response.status_code == 200
    game = db_session.get(Game, UUID(response.json()["id"]))
    assert game is not None
    assert game.owner_player_id == "import-owner"


def test_analysis_job_hides_ownerless_legacy_game(client: TestClient, db_session: Session) -> None:
    headers = _register(db_session, "player")
    game = Game(pgn=PGN)
    db_session.add(game)
    db_session.commit()

    response = client.post(f"/v1/games/{game.id}/analysis-jobs", headers=headers)
    assert response.status_code == 404

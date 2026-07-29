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


def test_game_history_and_positions_exclude_another_player(
    client: TestClient, db_session: Session
) -> None:
    owner_headers = _register(db_session, "owner")
    other_headers = _register(db_session, "other")
    owner_game = Game(pgn=PGN, owner_player_id="owner")
    other_game = Game(pgn=PGN, owner_player_id="other")
    db_session.add_all([owner_game, other_game])
    db_session.commit()

    owner_history = client.get("/v1/games", headers=owner_headers)
    other_history = client.get("/v1/games", headers=other_headers)

    assert owner_history.status_code == other_history.status_code == 200
    assert [game["id"] for game in owner_history.json()["items"]] == [str(owner_game.id)]
    assert [game["id"] for game in other_history.json()["items"]] == [str(other_game.id)]
    response = client.get(f"/v1/games/{owner_game.id}/positions", headers=other_headers)
    assert response.status_code == 404

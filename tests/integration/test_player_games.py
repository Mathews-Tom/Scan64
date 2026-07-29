from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position

PGN = (
    '[Event "Import"]\n[Site "Reykjavik"]\n[Date "1972.07.11"]\n'
    '[White "alice"]\n[Black "carol"]\n[Result "1-0"]\n\n1. e4 e5 1-0\n'
)


def _register(db_session: Session, player_id: str) -> str:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_played_and_imported_games_are_both_listed_once(
    client: TestClient, db_session: Session
) -> None:
    token = _register(db_session, "alice")
    played = Game(
        pgn="",
        moves=["e2e4"],
        white="alice",
        black="Stockfish (strength 1)",
        result="0-1",
        owner_player_id="alice",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(played)
    db_session.commit()
    source_position = Position(
        game_id=played.id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="initial",
    )
    db_session.add(source_position)
    db_session.commit()
    db_session.add(
        PersistedLessonOpportunity(
            game_id=played.id,
            source_position_id=source_position.id,
            player_id="alice",
            lesson_spec={},
        )
    )
    db_session.commit()

    imported = client.post(
        "/v1/games", json={"pgn": PGN, "player_id": "alice"}, headers=_auth(token)
    )
    assert imported.status_code == 200, imported.text

    response = client.get("/v1/players/alice/games", headers=_auth(token))

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["id"] for item in items] == [imported.json()["id"], str(played.id)]
    assert items[0]["diagnosis_count"] == 0
    assert items[1]["diagnosis_count"] == 1
    assert items[1]["result"] == "0-1"
    assert items[1]["black"] == "Stockfish (strength 1)"
    assert items[0]["date"] == "1972.07.11"
    assert items[1]["date"] == played.created_at.strftime("%Y.%m.%d")
    assert items[0]["created_at"] is not None


def test_another_players_games_are_not_listed(client: TestClient, db_session: Session) -> None:
    token = _register(db_session, "alice")
    _register(db_session, "bob")
    db_session.add(Game(pgn="", moves=[], white="bob", black="x", owner_player_id="bob"))
    db_session.add(Game(pgn="", moves=[], white="?", black="?", owner_player_id=None))
    db_session.commit()

    response = client.get("/v1/players/alice/games", headers=_auth(token))

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


def test_listing_requires_the_players_own_token(client: TestClient, db_session: Session) -> None:
    _register(db_session, "alice")
    bob_token = _register(db_session, "bob")

    assert client.get("/v1/players/alice/games").status_code == 401
    assert client.get("/v1/players/alice/games", headers=_auth(bob_token)).status_code == 403


def test_listing_pages_through_a_players_games(client: TestClient, db_session: Session) -> None:
    token = _register(db_session, "alice")
    base = datetime.now(UTC)
    for index in range(3):
        db_session.add(
            Game(
                pgn="",
                moves=[],
                white="alice",
                black="Stockfish",
                owner_player_id="alice",
                created_at=base - timedelta(minutes=index),
            )
        )
    db_session.commit()

    first = client.get("/v1/players/alice/games?limit=2", headers=_auth(token)).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None

    second = client.get(
        f"/v1/players/alice/games?limit=2&cursor={first['next_cursor']}", headers=_auth(token)
    ).json()

    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    listed = [item["id"] for item in first["items"] + second["items"]]
    assert len(set(listed)) == 3


def test_an_out_of_range_limit_is_rejected(client: TestClient, db_session: Session) -> None:
    token = _register(db_session, "alice")
    db_session.add(Game(pgn="", moves=[], white="alice", black="x", owner_player_id="alice"))
    db_session.commit()

    assert client.get("/v1/players/alice/games?limit=0", headers=_auth(token)).status_code == 422
    assert client.get("/v1/players/alice/games?limit=-1", headers=_auth(token)).status_code == 422
    assert client.get("/v1/players/alice/games?limit=101", headers=_auth(token)).status_code == 422


def test_an_unreadable_cursor_is_rejected(client: TestClient, db_session: Session) -> None:
    token = _register(db_session, "alice")

    response = client.get("/v1/players/alice/games?cursor=not-a-cursor", headers=_auth(token))

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid cursor"

from __future__ import annotations

import io
from uuid import UUID, uuid4

import chess.pgn
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import scan64.persistence.database as db_module
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.pgn import CorruptGameError
from scan64.chess.games.play_session_service import PlaySessionNotActive, PlaySessionService
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.providers.stockfish.adapter import StockfishConfig

BLACK_TO_MOVE_FEN = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"


def _register(db_session: Session, player_id: str = "alice") -> dict[str, str]:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


def _start(client: TestClient, headers: dict[str, str], **extra: object) -> str:
    body: dict[str, object] = {"player_id": "alice", "opponent_config": {"strength": "1"}}
    body.update(extra)
    response = client.post("/v1/play-sessions", json=body, headers=headers)
    assert response.status_code == 200, response.text
    session_id = response.json()["id"]
    assert isinstance(session_id, str)
    return session_id


def test_resigning_as_white_completes_the_session_and_loses_the_game(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(db_session)
    session_id = _start(client, auth)
    played = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "e2e4"},
        headers={**auth, "Idempotency-Key": "one"},
    )
    assert played.status_code == 200, played.text

    response = client.post(f"/v1/play-sessions/{session_id}/resign", headers=auth)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    play_session = db_session.get(PlaySession, UUID(session_id))
    assert play_session is not None
    db_session.refresh(play_session)
    game = db_session.get(Game, play_session.game_id)
    assert game is not None
    assert game.result == "0-1"
    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    assert parsed is not None
    assert parsed.headers["Result"] == "0-1"


def test_resigning_as_black_awards_the_game_to_white(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(db_session)
    session_id = _start(client, auth, initial_fen=BLACK_TO_MOVE_FEN)

    response = client.post(f"/v1/play-sessions/{session_id}/resign", headers=auth)

    assert response.status_code == 200, response.text
    play_session = db_session.get(PlaySession, UUID(session_id))
    assert play_session is not None
    game = db_session.get(Game, play_session.game_id)
    assert game is not None
    assert game.result == "1-0"


def test_resigning_before_any_move_completes_a_session_without_a_game(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(db_session)
    session_id = _start(client, auth)

    response = client.post(f"/v1/play-sessions/{session_id}/resign", headers=auth)

    assert response.status_code == 200, response.text
    play_session = db_session.get(PlaySession, UUID(session_id))
    assert play_session is not None
    assert play_session.status == "completed"
    assert play_session.game_id is None


def test_resigning_twice_is_rejected(client: TestClient, db_session: Session) -> None:
    auth = _register(db_session)
    session_id = _start(client, auth)
    assert client.post(f"/v1/play-sessions/{session_id}/resign", headers=auth).status_code == 200

    repeated = client.post(f"/v1/play-sessions/{session_id}/resign", headers=auth)

    assert repeated.status_code == 409
    assert repeated.json()["detail"] == "PlaySession is completed"


def test_resigning_an_unknown_session_is_not_found(client: TestClient, db_session: Session) -> None:
    auth = _register(db_session)
    response = client.post(f"/v1/play-sessions/{UUID(int=1)}/resign", headers=auth)

    assert response.status_code == 404


def test_a_move_after_resigning_is_rejected(client: TestClient, db_session: Session) -> None:
    auth = _register(db_session)
    session_id = _start(client, auth)
    client.post(f"/v1/play-sessions/{session_id}/resign", headers=auth)

    response = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "e2e4"},
        headers={**auth, "Idempotency-Key": "after-resign"},
    )

    assert response.status_code == 409


def test_resigning_a_game_already_in_progress_concedes_for_the_side_on_move(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(db_session)
    game = Game(
        pgn="",
        moves=["e2e4"],
        white="Stockfish (strength 1)",
        black="alice",
        owner_player_id="alice",
    )
    db_session.add(game)
    db_session.commit()
    created = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "alice",
            "game_id": str(game.id),
            "opponent_config": {"strength": "1"},
        },
        headers=auth,
    )
    assert created.status_code == 200, created.text

    response = client.post(f"/v1/play-sessions/{created.json()['id']}/resign", headers=auth)

    assert response.status_code == 200, response.text
    db_session.refresh(game)
    assert game.result == "1-0"


def test_resigning_a_session_whose_game_row_is_missing_is_loud(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(db_session)
    play_session = PlaySession(player_id="alice", game_id=uuid4(), opponent_config={})
    db_session.add(play_session)
    db_session.commit()

    with pytest.raises(CorruptGameError):
        client.post(f"/v1/play-sessions/{play_session.id}/resign", headers=auth)

    db_session.refresh(play_session)
    assert play_session.status == "active"


def test_resigning_a_play_from_here_game_keeps_its_original_date(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(db_session)
    game = Game(
        pgn="",
        moves=["e2e4"],
        white="Fischer",
        black="Spassky",
        date="1972.07.11",
        owner_player_id="alice",
    )
    db_session.add(game)
    db_session.commit()
    created = client.post(
        "/v1/play-sessions",
        json={"player_id": "alice", "game_id": str(game.id), "opponent_config": {"strength": "1"}},
        headers=auth,
    )
    assert created.status_code == 200, created.text

    resigned = client.post(f"/v1/play-sessions/{created.json()['id']}/resign", headers=auth)

    assert resigned.status_code == 200, resigned.text
    db_session.refresh(game)
    assert game.date == "1972.07.11"
    assert '[Date "1972.07.11"]' in game.pgn


def test_resigning_requires_the_session_owner_token(
    client: TestClient, db_session: Session
) -> None:
    owner = _register(db_session)
    intruder = _register(db_session, "mallory")
    session_id = _start(client, owner)

    assert client.post(f"/v1/play-sessions/{session_id}/resign").status_code == 401
    assert (
        client.post(f"/v1/play-sessions/{session_id}/resign", headers=intruder).status_code == 404
    )


def test_only_one_of_two_concurrent_terminal_transitions_wins(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(db_session)
    session_id = _start(client, auth)
    played = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "e2e4"},
        headers={**auth, "Idempotency-Key": "one"},
    )
    assert played.status_code == 200, played.text

    with Session(db_module.engine) as concurrent:
        loser = concurrent.get(PlaySession, UUID(session_id))
        assert loser is not None and loser.status == "active"
        concurrent.refresh(loser)
        losing_game = concurrent.get(Game, loser.game_id)
        assert losing_game is not None

        winner = client.post(f"/v1/play-sessions/{session_id}/resign", headers=auth)
        assert winner.status_code == 200, winner.text

        service = PlaySessionService(
            db_session=concurrent,
            stockfish_provider=StockfishOpponentProvider(StockfishConfig()),
        )
        with pytest.raises(PlaySessionNotActive):
            service.complete_session(loser, losing_game, "1-0")

    response = client.get(f"/v1/play-sessions/{session_id}", headers=auth)
    assert response.status_code == 200, response.text
    game = db_session.get(Game, UUID(response.json()["game_id"]))
    assert game is not None
    db_session.refresh(game)
    assert game.result == "0-1"

from __future__ import annotations

import io
from uuid import UUID

import chess.pgn
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import Player
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.pgn import CorruptGameError, build_pgn

FOOLS_MATE_FEN = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"


def _play(client: TestClient, session_id: str, move: str, key: str) -> None:
    response = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": move},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 200, response.text


def test_played_game_pgn_reimports_and_names_the_player(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(Player(id="alice"))
    db_session.commit()

    game = Game(
        pgn="",
        headers={"FEN": FOOLS_MATE_FEN},
        moves=[],
        white="Stockfish (strength 1)",
        black="alice",
        owner_player_id="alice",
    )
    db_session.add(game)
    db_session.commit()
    play_session = PlaySession(
        player_id="alice", game_id=game.id, opponent_config={"strength": "1"}
    )
    db_session.add(play_session)
    db_session.commit()

    _play(client, str(play_session.id), "d8h4", "mate")

    db_session.refresh(game)
    assert game.result == "0-1"

    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    assert parsed is not None
    assert parsed.headers["Black"] == "alice"
    assert parsed.headers["White"] == "Stockfish (strength 1)"
    assert parsed.headers["FEN"] == FOOLS_MATE_FEN
    assert parsed.headers["Result"] == "0-1"
    assert [move.uci() for move in parsed.mainline_moves()] == game.moves

    imported = client.post("/v1/games", json={"pgn": game.pgn, "player_id": "alice"})
    assert imported.status_code == 200, imported.text
    reimported = db_session.get(Game, UUID(imported.json()["id"]))
    assert reimported is not None
    assert reimported.moves == game.moves
    assert reimported.black == "alice"
    assert reimported.result == "0-1"
    assert reimported.owner_player_id == "alice"


def test_pgn_is_rebuilt_after_every_move(client: TestClient, db_session: Session) -> None:
    created = client.post(
        "/v1/play-sessions",
        json={"player_id": "alice", "opponent_config": {"strength": "1"}},
    )
    session_id = created.json()["id"]

    _play(client, session_id, "e2e4", "one")
    play_session = db_session.get(PlaySession, UUID(session_id))
    assert play_session is not None
    game = db_session.get(Game, play_session.game_id)
    assert game is not None

    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    assert parsed is not None
    assert [move.uci() for move in parsed.mainline_moves()] == game.moves
    assert parsed.headers["White"] == "alice"
    assert parsed.headers["Result"] == "*"


def test_a_session_has_a_valid_pgn_before_its_first_move(
    client: TestClient, db_session: Session
) -> None:
    created = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "alice",
            "opponent_config": {"strength": "1"},
            "initial_fen": FOOLS_MATE_FEN,
        },
    )
    assert created.status_code == 200, created.text

    game = db_session.get(Game, UUID(created.json()["game_id"]))
    assert game is not None
    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    assert parsed is not None
    assert parsed.headers["FEN"] == FOOLS_MATE_FEN
    assert list(parsed.mainline_moves()) == []


def test_rendering_a_game_whose_moves_do_not_fit_it_is_loud() -> None:
    corrupt = Game(pgn="", moves=["e2e4", "e2e4"], white="alice", black="Stockfish")

    with pytest.raises(CorruptGameError, match="illegal move"):
        build_pgn(corrupt)


def test_an_imported_games_tags_survive_being_played_on(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(Player(id="alice"))
    db_session.commit()
    imported = client.post(
        "/v1/games",
        json={
            "pgn": (
                '[Event "Reykjavik"]\n[Site "Reykjavik ISL"]\n[Date "1972.07.11"]\n'
                '[White "Spassky"]\n[Black "Fischer"]\n[Result "1-0"]\n[ECO "E56"]\n'
                '[WhiteElo "2660"]\n\n1. d4 Nf6 1-0\n'
            ),
            "player_id": "alice",
        },
    )
    assert imported.status_code == 200, imported.text
    game = db_session.get(Game, UUID(imported.json()["id"]))
    assert game is not None
    original_pgn = game.pgn

    created = client.post(
        "/v1/play-sessions",
        json={"player_id": "alice", "game_id": str(game.id), "opponent_config": {"strength": "1"}},
    )
    assert created.status_code == 200, created.text

    db_session.refresh(game)
    assert game.pgn == original_pgn

    rebuilt = chess.pgn.read_game(io.StringIO(build_pgn(game)))
    assert rebuilt is not None
    assert rebuilt.headers["ECO"] == "E56"
    assert rebuilt.headers["WhiteElo"] == "2660"
    assert rebuilt.headers["Event"] == "Reykjavik"
    assert rebuilt.headers["Date"] == "1972.07.11"
    assert rebuilt.headers["White"] == "alice"


def test_another_players_game_cannot_be_attached_or_touched(
    client: TestClient, db_session: Session
) -> None:
    game = Game(
        pgn='[Event "Theirs"]\n\n1. e4 1-0\n',
        moves=["e2e4"],
        white="Kasparov",
        black="Karpov",
        owner_player_id="bob",
    )
    db_session.add(game)
    db_session.commit()

    created = client.post(
        "/v1/play-sessions",
        json={"player_id": "alice", "game_id": str(game.id), "opponent_config": {"strength": "1"}},
    )
    assert created.status_code == 403, created.text

    db_session.refresh(game)
    assert game.pgn == '[Event "Theirs"]\n\n1. e4 1-0\n'
    assert (game.white, game.black) == ("Kasparov", "Karpov")


def test_a_played_game_is_tagged_as_a_scan64_session(
    client: TestClient, db_session: Session
) -> None:
    created = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "alice",
            "opponent_config": {"strength": "1"},
            "initial_fen": FOOLS_MATE_FEN,
        },
    )
    assert created.status_code == 200, created.text

    game = db_session.get(Game, UUID(created.json()["game_id"]))
    assert game is not None
    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    assert parsed is not None
    assert parsed.headers["Event"] == "Scan64 play session"
    assert parsed.headers["Site"] == "Scan64"
    assert parsed.headers["Round"] == "-"

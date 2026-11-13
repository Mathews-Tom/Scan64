"""Prospective hint provenance, snapshot CAS and canonical revisit regressions."""

from unittest.mock import AsyncMock

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.games.assistance import (
    PlaySnapshot,
    SessionHintReveal,
    StalePlaySnapshot,
    claim_snapshot,
    persist_hint_reveal,
    prefix_hash,
)
from scan64.chess.games.models import Game, PlaySession
from scan64.providers.stockfish.pool import PooledStockfishAdapter


def _register(session: Session) -> dict[str, str]:
    token, hashed = issue_player_token()
    session.add(Player(id="hint-tracking-owner"))
    session.add(PlayerCredential(player_id="hint-tracking-owner", token_hash=hashed))
    session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_legacy_session_tracking_defaults_unknown() -> None:
    play = PlaySession(player_id="legacy")
    assert play.assistance_tracking_version is None
    assert play.credit_analysis_status is None


@pytest.mark.parametrize("custom", [False, True])
def test_api_tracking_and_custom_start_are_server_owned(
    client: TestClient, db_session: Session, custom: bool
) -> None:
    headers = _register(db_session)
    response = client.post(
        "/v1/play-sessions", headers=headers,
        json={
            "player_id": "hint-tracking-owner",
            "initial_fen": chess.STARTING_FEN if custom else None,
            "opponent_config": {"custom_start": "false", "assistance_generation": "999"},
        },
    )
    assert response.status_code == 200, response.text
    play = db_session.exec(select(PlaySession)).one()
    assert play.assistance_tracking_version == 1
    assert play.opponent_config["custom_start"] == str(custom).lower()
    assert play.opponent_config["assistance_generation"] == "0"
    assert play.opponent_config["initial_ply"] == "0"


def test_first_hint_persists_without_creating_game(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _register(db_session)
    created = client.post(
        "/v1/play-sessions", headers=headers, json={"player_id": "hint-tracking-owner"}
    )
    assert created.status_code == 200
    monkeypatch.setattr(
        app.state.engine_pool_manager, "analyze_hint",
        AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["g1f3"], "score_cp": 0}])),
    )
    for step in [1, 3, 2]:
        response = client.post(
            "/v1/insights/position", headers=headers,
            json={"player_id": "hint-tracking-owner", "play_session_id": created.json()["id"],
                  "step": step},
        )
        assert response.status_code == 200, response.text
    reveal = db_session.exec(select(SessionHintReveal)).one()
    assert reveal.highest_step == 3
    assert reveal.ply == 1
    assert reveal.initial_fen == chess.STARTING_FEN
    assert reveal.moves == []
    assert reveal.canonical_id == chess.Board().epd()
    assert reveal.prefix_hash == prefix_hash(chess.STARTING_FEN, [])
    db_session.expire_all()
    assert db_session.exec(select(PlaySession)).one().game_id is None
    assert db_session.exec(select(Game)).all() == []


@pytest.mark.parametrize("change", ["completion", "link_game", "moves", "initial_fen", "aba"])
def test_stale_hint_snapshot_cannot_disclose_or_write(
    db_session: Session, change: str
) -> None:
    game = None if change == "link_game" else Game(pgn="*")
    if game is not None:
        db_session.add(game)
        db_session.flush()
    play = PlaySession(player_id="owner", game_id=game.id if game else None)
    db_session.add(play)
    db_session.commit()
    snapshot = PlaySnapshot.capture(play, game)
    if change == "completion":
        play.status = "completed"
    elif change == "link_game":
        linked = Game(pgn="*")
        db_session.add(linked)
        db_session.flush()
        play.game_id = linked.id
    elif change == "moves":
        assert game is not None
        game.moves = ["e2e4"]
    elif change == "initial_fen":
        assert game is not None
        game.headers = {"FEN": chess.STARTING_FEN.replace("0 1", "7 4")}
    else:
        # A takeback/replay can restore the exact board and prefix, not its generation.
        claim_snapshot(db_session, snapshot)
    db_session.commit()
    with pytest.raises(StalePlaySnapshot):
        persist_hint_reveal(db_session, snapshot, chess.Board(), 3)
    assert db_session.exec(select(SessionHintReveal)).all() == []


def test_reveal_survives_takeback_and_counter_different_repetition(db_session: Session) -> None:
    game = Game(pgn="*")
    db_session.add(game)
    db_session.flush()
    play = PlaySession(player_id="owner", game_id=game.id)
    db_session.add(play)
    db_session.commit()
    persist_hint_reveal(db_session, PlaySnapshot.capture(play, game), chess.Board(), 1)
    original_hash = prefix_hash(chess.STARTING_FEN, [])
    game.moves = ["g1f3", "g8f6", "f3g1", "f6g8"]
    db_session.commit()
    board = chess.Board()
    for move in game.moves:
        board.push_uci(move)
    assert board.fen() != chess.STARTING_FEN
    persist_hint_reveal(db_session, PlaySnapshot.capture(play, game), board, 2)
    reveals = db_session.exec(select(SessionHintReveal)).all()
    assert len(reveals) == 2
    assert {row.canonical_id for row in reveals} == {chess.Board().epd()}
    assert len({row.prefix_hash for row in reveals}) == 2
    game.moves = []
    db_session.commit()
    persist_hint_reveal(db_session, PlaySnapshot.capture(play, game), chess.Board(), 3)
    assert db_session.get(SessionHintReveal, (play.id, original_hash)).highest_step == 3
    assert len(db_session.exec(select(SessionHintReveal)).all()) == 2


def test_prefix_hash_includes_full_initial_position_and_order() -> None:
    assert prefix_hash(chess.STARTING_FEN, ["e2e4", "e7e5"]) == prefix_hash(
        chess.STARTING_FEN, ["e2e4", "e7e5"]
    )
    assert prefix_hash(chess.STARTING_FEN, []) != prefix_hash(
        chess.STARTING_FEN.replace("0 1", "1 2"), []
    )
    assert prefix_hash(chess.STARTING_FEN, ["e2e4", "e7e5"]) != prefix_hash(
        chess.STARTING_FEN, ["e7e5", "e2e4"]
    )


@pytest.mark.parametrize("mutation", ["move", "takeback", "complete", "first_game", "reveal"])
def test_engine_work_race_returns_409_without_hint_data(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    headers = _register(db_session)
    game = None
    if mutation != "first_game":
        game = Game(pgn="*", moves=["g1f3", "g8f6"])
        db_session.add(game)
        db_session.flush()
    play = PlaySession(
        player_id="hint-tracking-owner", game_id=game.id if game else None,
        assistance_tracking_version=1,
    )
    db_session.add(play)
    db_session.commit()

    async def concurrent_change(_fen: str) -> EngineAnalysis:
        if mutation == "complete":
            play.status = "completed"
        elif mutation == "first_game":
            first_game = Game(pgn="*")
            db_session.add(first_game)
            db_session.flush()
            play.game_id = first_game.id
        elif mutation == "reveal":
            claim_snapshot(db_session, PlaySnapshot.capture(play, game))
        else:
            assert game is not None
            game.moves = (
                [*game.moves, "e2e4", "e7e5"] if mutation == "move" else []
            )
        db_session.commit()
        return EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 0}])

    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", concurrent_change)
    response = client.post(
        "/v1/insights/position", headers=headers,
        json={"player_id": "hint-tracking-owner", "play_session_id": str(play.id), "step": 3},
    )
    assert response.status_code == 409, response.text
    assert set(response.json()) == {"detail"}
    assert db_session.exec(select(SessionHintReveal)).all() == []


def test_cached_insight_keeps_authorization_projection_and_provenance_per_request(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _register(db_session)
    token, hashed = issue_player_token()
    db_session.add(Player(id="other-hint-owner"))
    db_session.add(PlayerCredential(player_id="other-hint-owner", token_hash=hashed))
    db_session.commit()
    other_headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["g1f3"], "score_cp": 0}]))
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    created = client.post(
        "/v1/play-sessions", headers=headers, json={"player_id": "hint-tracking-owner"}
    )
    assert created.status_code == 200
    body = {"player_id": "hint-tracking-owner", "play_session_id": created.json()["id"], "step": 1}
    first = client.post("/v1/insights/position", headers=headers, json=body)
    second = client.post("/v1/insights/position", headers=headers, json={**body, "step": 3})
    assert first.status_code == second.status_code == 200
    assert first.json()["step"] == 1
    assert "candidates" not in first.json()
    assert second.json()["step"] == 3
    assert analyze.await_count == 1
    assert db_session.exec(select(SessionHintReveal)).one().highest_step == 3

    forbidden = client.post("/v1/insights/position", headers=other_headers, json=body)
    assert forbidden.status_code == 404
    other = client.post(
        "/v1/insights/position", headers=other_headers,
        json={"player_id": "other-hint-owner", "fen": chess.STARTING_FEN, "step": 1},
    )
    assert other.status_code == 200
    assert "candidates" not in other.json()
    assert analyze.await_count == 1
    assert len(db_session.exec(select(SessionHintReveal)).all()) == 1

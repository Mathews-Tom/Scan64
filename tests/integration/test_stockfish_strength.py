from unittest.mock import AsyncMock
from uuid import UUID

import chess
import chess.engine
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.api.play import PlaySessionCreate, get_opponent_provider
from scan64.chess.games.models import PlaySession
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.chess.opponents.protocols import MoveDecision
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.providers.stockfish.adapter import StockfishConfig


def authorize(client: TestClient, db_session: Session) -> None:
    token, token_hash = issue_player_token()
    db_session.add(Player(id="skill-player"))
    db_session.add(PlayerCredential(player_id="skill-player", token_hash=token_hash))
    db_session.commit()
    client.headers["Authorization"] = f"Bearer {token}"


@pytest.mark.parametrize("provider", [None, "stockfish"])
@pytest.mark.parametrize("strength", ["1500", "-1", "21", "1.5", "", "skill 0", " 0", "０"])
def test_stockfish_rejects_invalid_skill_level(
    client: TestClient, db_session: Session, provider: str | None, strength: str
) -> None:
    authorize(client, db_session)
    config = {"strength": strength}
    if provider is not None:
        config["provider"] = provider
    response = client.post(
        "/v1/play-sessions", json={"player_id": "skill-player", "opponent_config": config}
    )
    assert response.status_code == 422
    assert "Stockfish strength must be an integer string from 0 to 20" in response.text


@pytest.mark.parametrize("strength, expected", [("0", "0"), ("1", "1"), ("20", "20"), ("05", "5")])
def test_stockfish_accepts_skill_level_bounds(
    client: TestClient, db_session: Session, strength: str, expected: str
) -> None:
    authorize(client, db_session)
    response = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "skill-player",
            "opponent_config": {"strength": strength},
            "initial_fen": chess.STARTING_FEN,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["opponent_config"]["strength"] == expected
    game = client.get(f"/v1/games/{response.json()['game_id']}")
    assert game.status_code == 200, game.text
    assert game.json()["black"] == f"Stockfish (skill level {expected})"


@pytest.mark.parametrize("config", [{}, {"provider": "stockfish"}])
def test_stockfish_unspecified_strength_defaults_to_zero(config: dict[str, str]) -> None:
    assert (
        PlaySessionCreate(player_id="player", opponent_config=config).opponent_config["strength"]
        == "0"
    )
    assert PlaySessionCreate(player_id="player").opponent_config == {"strength": "0"}


def test_maia_keeps_rating_strength_and_unspecified_config() -> None:
    config = {"provider": "maia", "strength": "1500"}
    assert PlaySessionCreate(player_id="player", opponent_config=config).opponent_config == config
    assert PlaySessionCreate(
        player_id="player", opponent_config={"provider": "maia"}
    ).opponent_config == {"provider": "maia"}


@pytest.mark.parametrize("source", ["play", "analysis", "famous", "unspecified"])
def test_screen_session_config_reaches_engine_at_skill_zero(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    authorize(client, db_session)
    engine = AsyncMock()
    engine.play.return_value = chess.engine.PlayResult(chess.Move.from_uci("e7e5"), None)
    monkeypatch.setattr(chess.engine, "popen_uci", AsyncMock(return_value=(None, engine)))
    app.dependency_overrides[get_opponent_provider] = lambda: StockfishOpponentProvider(
        StockfishConfig()
    )
    payload: dict[str, object] = {"player_id": "skill-player"}
    if source != "unspecified":
        payload["opponent_config"] = {"strength": "0"}
    if source == "analysis":
        created_game = client.post(
            "/v1/games",
            json={
                "player_id": "skill-player",
                "owner_color": "white",
                "pgn": f'[FEN "{chess.STARTING_FEN}"]\n[SetUp "1"]\n\n',
            },
        )
        assert created_game.status_code == 200, created_game.text
        payload["game_id"] = created_game.json()["id"]
    elif source == "famous":
        payload["initial_fen"] = chess.STARTING_FEN
    created = client.post("/v1/play-sessions", json=payload)
    assert created.status_code == 200, created.text
    assert created.json()["opponent_config"]["strength"] == "0"
    response = client.post(f"/v1/play-sessions/{created.json()['id']}/moves", json={"move": "e2e4"})
    assert response.status_code == 200, response.text
    engine.configure.assert_awaited_once_with({"Threads": 1, "Hash": 16, "Skill Level": 0})
    engine.play.assert_awaited_once()
    listed = client.get("/v1/players/skill-player/games")
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["black"] == "Stockfish (skill level 0)"
    stored = db_session.get(PlaySession, UUID(created.json()["id"]))
    assert stored is not None
    assert stored.opponent_config["strength"] == "0"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider, expected", [("stockfish", 0), ("maia", 10)])
async def test_service_unspecified_strength_preserves_provider_default(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, provider: str, expected: int
) -> None:
    play_session = PlaySession(player_id="skill-player", opponent_config={"provider": provider})
    db_session.add(play_session)
    db_session.commit()
    opponent = AsyncMock()
    opponent.choose_move.return_value = MoveDecision(uci_move="e7e5", score=None, time_taken_ms=1)
    service = PlaySessionService(db_session, StockfishOpponentProvider(StockfishConfig()))
    monkeypatch.setattr(service, "opponent_provider_for", lambda config: opponent)
    await service.make_move(play_session.id, "e2e4")
    assert opponent.choose_move.await_args.args[1].strength_setting == expected

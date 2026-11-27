"""Bot play under a non-Stockfish engine profile returns 503, never a 500 or a fallback."""

from pathlib import Path
from unittest.mock import AsyncMock

import chess
import chess.engine
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.games.models import Game, PlaySession
from scan64.providers.engine.config import ENGINE_CONFIG_ENV

ETHEREAL_OPTIONS = ("Hash", "Threads", "EvalFile", "MultiPV", "Ponder", "UCI_Chess960")


def authorize(client: TestClient, db_session: Session) -> None:
    token, token_hash = issue_player_token()
    db_session.add(Player(id="profile-player"))
    db_session.add(PlayerCredential(player_id="profile-player", token_hash=token_hash))
    db_session.commit()
    client.headers["Authorization"] = f"Bearer {token}"


@pytest.fixture(name="ethereal_profile")
def ethereal_profile_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncMock:
    """An Ethereal-like profile: no Skill Level, UCI_LimitStrength or UCI_Elo."""
    binary = tmp_path / "ethereal"
    binary.write_bytes(b"ethereal test double")
    profile = tmp_path / "engine.toml"
    profile.write_text(
        f"[engine]\nname = \"ethereal\"\nbinary_path = '{binary}'\n", encoding="utf-8"
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(profile))
    engine = AsyncMock()
    engine.id = {"name": "Ethereal 14.40"}
    engine.config = {}
    engine.options = dict.fromkeys(ETHEREAL_OPTIONS)

    async def reject(options: dict[str, object]) -> None:
        unknown = [name for name in options if name not in engine.options]
        if unknown:
            raise chess.engine.EngineError(f"engine does not support option {unknown[0]}")

    engine.configure.side_effect = reject
    monkeypatch.setattr(chess.engine, "popen_uci", AsyncMock(return_value=(None, engine)))
    return engine


def assert_bot_play_unavailable(response_json: dict[str, object]) -> None:
    detail = response_json["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "bot_play_unavailable"
    assert detail["message"] == "Bot play is unavailable under the active engine profile."
    assert "Skill Level" in str(detail["reason"])
    assert "Ethereal" in str(detail["reason"])


@pytest.mark.usefixtures("ethereal_profile")
def test_bot_move_returns_503_and_keeps_the_player_move(
    client: TestClient, db_session: Session
) -> None:
    authorize(client, db_session)
    created = client.post(
        "/v1/play-sessions",
        json={"player_id": "profile-player", "opponent_config": {"bot_level": "3"}},
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["id"]

    response = client.post(f"/v1/play-sessions/{session_id}/moves", json={"move": "e2e4"})

    assert response.status_code == 503, response.text
    assert_bot_play_unavailable(response.json())
    db_session.expire_all()
    play_session = db_session.exec(select(PlaySession)).one()
    game = db_session.get(Game, play_session.game_id)
    assert game is not None and game.moves == ["e2e4"]

    resumed = client.post(f"/v1/play-sessions/{session_id}/resume")
    assert resumed.status_code == 503, resumed.text
    assert_bot_play_unavailable(resumed.json())


@pytest.mark.usefixtures("ethereal_profile")
def test_bot_first_move_returns_503_and_creates_no_session(
    client: TestClient, db_session: Session
) -> None:
    authorize(client, db_session)
    response = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "profile-player",
            "player_color": "black",
            "initial_fen": chess.STARTING_FEN,
            "opponent_config": {"bot_level": "3"},
        },
    )
    assert response.status_code == 503, response.text
    assert_bot_play_unavailable(response.json())
    db_session.expire_all()
    assert db_session.exec(select(PlaySession)).all() == []

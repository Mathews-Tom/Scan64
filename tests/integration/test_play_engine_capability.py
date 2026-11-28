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
from scan64.providers.engine.config import ENGINE_CONFIG_ENV, LADDER_ENGINE_CONFIG_ENV

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
    assert detail["kind"] == "missing_capability"


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


@pytest.fixture(name="narrow_arasan_profile")
def narrow_arasan_profile_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncMock:
    """An Elo-ladder profile whose level 1 UCI_Elo (1000) the engine does not accept."""
    binary = tmp_path / "arasan"
    binary.write_bytes(b"arasan test double")
    profile = tmp_path / "engine.toml"
    profile.write_text(
        f"[engine]\nname = \"arasan\"\nbinary_path = '{binary}'\n"
        '[ladder]\nstrength = "elo"\nelo = [1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500]\n'
        "movetime_ms = 100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(profile))
    engine = AsyncMock()
    engine.id = {"name": "Arasan 26.1"}
    engine.config = {}
    engine.options = dict.fromkeys(("Threads", "Hash", "MultiPV", "UCI_LimitStrength"))
    engine.options["UCI_Elo"] = chess.engine.Option("UCI_Elo", "spin", 3450, 1350, 3450, [])
    engine.play.return_value = chess.engine.PlayResult(chess.Move.from_uci("e7e5"), None)
    monkeypatch.setattr(chess.engine, "popen_uci", AsyncMock(return_value=(None, engine)))
    return engine


def _move_detail(client: TestClient, level: str) -> tuple[str, dict[str, object]]:
    created = client.post(
        "/v1/play-sessions",
        json={"player_id": "profile-player", "opponent_config": {"bot_level": level}},
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["id"]
    response = client.post(f"/v1/play-sessions/{session_id}/moves", json={"move": "e2e4"})
    return session_id, response.json() | {"status": response.status_code}


def test_out_of_range_elo_returns_an_actionable_503(
    narrow_arasan_profile: AsyncMock, client: TestClient, db_session: Session
) -> None:
    authorize(client, db_session)
    _, body = _move_detail(client, "1")
    assert body["status"] == 503, body
    detail = body["detail"]
    assert isinstance(detail, dict) and detail["code"] == "bot_play_unavailable"
    assert "UCI_Elo 1000, outside the 1350-3450" in str(detail["reason"])
    assert detail["kind"] == "elo_out_of_range"
    narrow_arasan_profile.play.assert_not_awaited()


def test_a_session_from_another_ladder_cannot_continue_or_change_cohort(
    narrow_arasan_profile: AsyncMock, client: TestClient, db_session: Session
) -> None:
    authorize(client, db_session)
    created = client.post(
        "/v1/play-sessions",
        json={"player_id": "profile-player", "opponent_config": {"bot_level": "3"}},
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["id"]
    play_session = db_session.exec(select(PlaySession)).one()
    assert play_session.opponent_config["bot_policy_version"].startswith("uci-elo-v1:arasan:")
    # The session was saved under the error-budget ladder; the server now runs Arasan's.
    play_session.opponent_config = {
        **play_session.opponent_config,
        "bot_policy_version": "stockfish-error-budget-v1",
    }
    db_session.add(play_session)
    db_session.commit()

    response = client.post(f"/v1/play-sessions/{session_id}/moves", json={"move": "e2e4"})

    assert response.status_code == 503, response.text
    assert "started under the bot ladder 'stockfish-error-budget-v1'" in str(
        response.json()["detail"]["reason"]
    )
    assert response.json()["detail"]["kind"] == "policy_mismatch"
    narrow_arasan_profile.play.assert_not_awaited()
    db_session.expire_all()
    stored = db_session.exec(select(PlaySession)).one()
    assert stored.opponent_config["bot_policy_version"] == "stockfish-error-budget-v1"


@pytest.fixture(name="split_profiles")
def split_profiles_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[AsyncMock, AsyncMock]:
    """Analysis under an engine without a ladder, bot moves under an Elo-ladder engine."""
    analysis_binary = tmp_path / "caissa"
    ladder_binary = tmp_path / "arasan"
    for binary in (analysis_binary, ladder_binary):
        binary.write_bytes(b"test double")
    analysis_profile = tmp_path / "analysis.toml"
    analysis_profile.write_text(
        f"[engine]\nname = \"caissa\"\nbinary_path = '{analysis_binary}'\n", encoding="utf-8"
    )
    ladder_profile = tmp_path / "ladder.toml"
    ladder_profile.write_text(
        f"[engine]\nname = \"arasan\"\nbinary_path = '{ladder_binary}'\n"
        '[ladder]\nstrength = "elo"\nelo = [1350, 1500, 1700, 1900, 2100, 2300, 2600, 3000]\n'
        "movetime_ms = 100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(analysis_profile))
    monkeypatch.setenv(LADDER_ENGINE_CONFIG_ENV, str(ladder_profile))
    analysis = AsyncMock()
    analysis.id = {"name": "Caissa 2.0"}
    analysis.config = {}
    analysis.options = dict.fromkeys(ETHEREAL_OPTIONS)
    ladder = AsyncMock()
    ladder.id = {"name": "Arasan 26.1"}
    ladder.config = {}
    ladder.options = dict.fromkeys(("Threads", "Hash", "MultiPV", "UCI_LimitStrength"))
    ladder.options["UCI_Elo"] = chess.engine.Option("UCI_Elo", "spin", 3450, 1000, 3450, [])
    ladder.play.return_value = chess.engine.PlayResult(chess.Move.from_uci("e7e5"), None)
    engines = {str(analysis_binary): analysis, str(ladder_binary): ladder}

    async def popen(command: str) -> tuple[None, AsyncMock]:
        return None, engines[command]

    monkeypatch.setattr(chess.engine, "popen_uci", popen)
    return analysis, ladder


def test_bot_moves_come_from_the_ladder_engine_profile(
    split_profiles: tuple[AsyncMock, AsyncMock], client: TestClient, db_session: Session
) -> None:
    analysis, ladder = split_profiles
    authorize(client, db_session)
    session_id, body = _move_detail(client, "3")
    assert body["status"] == 200, body
    ladder.play.assert_awaited_once()
    assert ladder.play.await_args is not None
    assert ladder.play.await_args.args[1] == chess.engine.Limit(time=0.1)
    analysis.play.assert_not_awaited()
    play_session = db_session.exec(select(PlaySession)).one()
    assert str(play_session.id) == session_id
    assert play_session.opponent_config["bot_policy_version"] == (
        "uci-elo-v1:arasan:1350,1500,1700,1900,2100,2300,2600,3000:100ms"
    )

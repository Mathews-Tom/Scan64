from unittest.mock import AsyncMock
from uuid import UUID

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.api.play import PlaySessionCreate, get_opponent_provider
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.chess.opponents.bots import BOT_LEVELS
from scan64.chess.opponents.protocols import MoveDecision
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.providers.stockfish.adapter import StockfishConfig


def authorize(client: TestClient, db_session: Session, player_id: str = "skill-player") -> None:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    client.headers["Authorization"] = f"Bearer {token}"


@pytest.mark.parametrize("provider", [None, "stockfish"])
@pytest.mark.parametrize("level", ["0", "9", "-1", "1500", "1.5", "", " 1", "１"])
def test_stockfish_rejects_unknown_bot_level(
    client: TestClient, db_session: Session, provider: str | None, level: str
) -> None:
    authorize(client, db_session)
    config = {"bot_level": level}
    if provider is not None:
        config["provider"] = provider
    response = client.post(
        "/v1/play-sessions", json={"player_id": "skill-player", "opponent_config": config}
    )
    assert response.status_code == 422


def test_bot_catalogue_is_the_single_named_eight_level_definition(client: TestClient) -> None:
    response = client.get("/v1/bots")
    assert response.status_code == 200
    levels = response.json()
    assert [level["level"] for level in levels] == list(range(1, 9))
    assert [level["name"] for level in levels] == [level.name for level in BOT_LEVELS]
    assert all(level["name"] and level["description"] for level in levels)
    assert all("elo" not in level for level in levels)


@pytest.mark.parametrize("level", range(1, 9))
def test_stockfish_accepts_named_levels(
    client: TestClient, db_session: Session, level: int
) -> None:
    authorize(client, db_session)
    response = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "skill-player",
            "opponent_config": {"bot_level": str(level)},
            "initial_fen": chess.STARTING_FEN,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["opponent_config"]["bot_level"] == str(level)
    game = client.get(f"/v1/games/{response.json()['game_id']}")
    assert game.status_code == 200
    assert BOT_LEVELS[level - 1].name in game.json()["black"]


@pytest.mark.parametrize("config", [{}, {"provider": "stockfish"}])
def test_stockfish_defaults_to_bot_level_one(config: dict[str, str]) -> None:
    assert (
        PlaySessionCreate(player_id="player", opponent_config=config).opponent_config["bot_level"]
        == "1"
    )


@pytest.mark.parametrize("strength", ["0", "20", "1500"])
def test_new_stockfish_sessions_refuse_raw_strength(
    client: TestClient, db_session: Session, strength: str
) -> None:
    authorize(client, db_session)
    response = client.post(
        "/v1/play-sessions",
        json={"player_id": "skill-player", "opponent_config": {"strength": strength}},
    )
    assert response.status_code == 422
    assert "use bot_level" in response.text


def test_maia_keeps_rating_strength_and_unspecified_config() -> None:
    config = {"provider": "maia", "strength": "1500"}
    assert PlaySessionCreate(player_id="player", opponent_config=config).opponent_config == config
    assert PlaySessionCreate(
        player_id="player", opponent_config={"provider": "maia"}
    ).opponent_config == {"provider": "maia"}


@pytest.mark.parametrize("source", ["play", "analysis", "famous", "unspecified"])
def test_screen_session_config_reaches_opponent_at_level_one(
    client: TestClient, db_session: Session, source: str
) -> None:
    authorize(client, db_session)
    opponent = AsyncMock()
    opponent.choose_move.return_value = MoveDecision(uci_move="e7e5")
    app.dependency_overrides[get_opponent_provider] = lambda: opponent
    payload: dict[str, object] = {"player_id": "skill-player"}
    if source != "unspecified":
        payload["opponent_config"] = {"bot_level": "1"}
    if source == "analysis":
        game = client.post(
            "/v1/games",
            json={
                "player_id": "skill-player",
                "owner_color": "white",
                "pgn": f'[FEN "{chess.STARTING_FEN}"]\n[SetUp "1"]\n\n',
            },
        )
        assert game.status_code == 200
        payload["game_id"] = game.json()["id"]
    elif source == "famous":
        payload["initial_fen"] = chess.STARTING_FEN
    created = client.post("/v1/play-sessions", json=payload)
    assert created.status_code == 200, created.text
    response = client.post(f"/v1/play-sessions/{created.json()['id']}/moves", json={"move": "e2e4"})
    assert response.status_code == 200, response.text
    context = opponent.choose_move.await_args.args[1]
    assert context.bot_level == 1
    assert str(context.seed) == created.json()["opponent_config"]["seed"]
    assert context.ply == 1


def test_black_start_takeback_continue_reload_and_resign(
    client: TestClient, db_session: Session
) -> None:
    authorize(client, db_session)
    opponent = AsyncMock()
    opponent.choose_move.side_effect = [
        MoveDecision(uci_move=move) for move in ["e2e4", "g1f3", "g1f3"]
    ]
    app.dependency_overrides[get_opponent_provider] = lambda: opponent
    created = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "skill-player",
            "player_color": "black",
            "opponent_config": {"bot_level": "1"},
        },
    )
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["player_color"] == "black"
    assert data["opponent_move"] == "e2e4"
    game_id, session_id = data["game_id"], data["id"]
    game = db_session.get(Game, UUID(game_id))
    assert game is not None
    assert game.moves == ["e2e4"]
    assert game.owner_color == "black"
    assert game.black == "skill-player"
    assert BOT_LEVELS[0].name in game.white
    # An opening bot move is not a human move pair and cannot be undone.
    assert client.post(f"/v1/play-sessions/{session_id}/takeback").status_code == 409
    wrong_side = client.post(f"/v1/play-sessions/{session_id}/moves", json={"move": "d2d4"})
    assert wrong_side.status_code == 400
    played = client.post(f"/v1/play-sessions/{session_id}/moves", json={"move": "e7e5"})
    assert played.status_code == 200, played.text
    assert played.json()["opponent_move"] == "g1f3"
    undone = client.post(f"/v1/play-sessions/{session_id}/takeback")
    assert undone.status_code == 200, undone.text
    assert undone.json()["player_color"] == "black"
    db_session.refresh(game)
    assert game.moves == ["e2e4"]
    assert "Nf3" not in game.pgn
    continued = client.post(f"/v1/play-sessions/{session_id}/moves", json={"move": "e7e5"})
    assert continued.status_code == 200, continued.text
    assert continued.json()["opponent_move"] == "g1f3"
    # New database session simulates a process reload, not a cached response.
    db_session.expire_all()
    resumed = client.get(f"/v1/play-sessions/{session_id}")
    assert resumed.json()["player_color"] == "black"
    assert resumed.json()["opponent_move"] is None
    resigned = client.post(f"/v1/play-sessions/{session_id}/resign")
    assert resigned.status_code == 200, resigned.text
    assert resigned.json()["status"] == "completed"
    db_session.refresh(game)
    assert game.owner_color == "black"
    assert game.result == "1-0"
    assert client.post(f"/v1/play-sessions/{session_id}/takeback").status_code == 409


def test_takeback_preserves_imported_history(client: TestClient, db_session: Session) -> None:
    authorize(client, db_session)
    game = Game(pgn="", moves=["e2e4", "e7e5"], owner_color="white", owner_player_id="skill-player")
    db_session.add(game)
    db_session.commit()
    opponent = AsyncMock()
    opponent.choose_move.return_value = MoveDecision(uci_move="b8c6")
    app.dependency_overrides[get_opponent_provider] = lambda: opponent
    created = client.post(
        "/v1/play-sessions", json={"player_id": "skill-player", "game_id": str(game.id)}
    )
    session_id = created.json()["id"]
    assert client.post(f"/v1/play-sessions/{session_id}/takeback").status_code == 409
    assert (
        client.post(f"/v1/play-sessions/{session_id}/moves", json={"move": "g1f3"}).status_code
        == 200
    )
    assert client.post(f"/v1/play-sessions/{session_id}/takeback").status_code == 200
    db_session.refresh(game)
    assert game.moves == ["e2e4", "e7e5"]
    assert client.post(f"/v1/play-sessions/{session_id}/takeback").status_code == 409


@pytest.mark.parametrize("status, coach_mode", [("active", True), ("completed", False)])
def test_takeback_refuses_coach_and_finished_sessions(
    client: TestClient, db_session: Session, status: str, coach_mode: bool
) -> None:
    authorize(client, db_session)
    game = Game(pgn="", moves=["e2e4", "e7e5"], owner_player_id="skill-player", owner_color="white")
    db_session.add(game)
    db_session.commit()
    play_session = PlaySession(
        player_id="skill-player",
        game_id=game.id,
        opponent_config={"bot_level": "1", "player_color": "white"},
        status=status,
        coach_mode=coach_mode,
    )
    db_session.add(play_session)
    db_session.commit()
    response = client.post(f"/v1/play-sessions/{play_session.id}/takeback")
    assert response.status_code == 409
    db_session.refresh(game)
    assert game.moves == ["e2e4", "e7e5"]


def test_takeback_hides_other_players_sessions(client: TestClient, db_session: Session) -> None:
    authorize(client, db_session)
    play_session = PlaySession(player_id="someone-else")
    db_session.add(play_session)
    db_session.commit()
    assert client.post(f"/v1/play-sessions/{play_session.id}/takeback").status_code == 404
    assert client.post(f"/v1/play-sessions/{UUID(int=1)}/takeback").status_code == 404
    client.headers.pop("Authorization")
    assert client.post(f"/v1/play-sessions/{play_session.id}/takeback").status_code == 401


@pytest.mark.parametrize("bit, expected", [(0, "black"), (1, "white")])
def test_random_color_is_chosen_once_and_persisted(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    bit: int,
    expected: str,
) -> None:
    authorize(client, db_session)
    monkeypatch.setattr("scan64.api.play.secrets.randbits", lambda count: bit)
    opponent = AsyncMock()
    opponent.choose_move.return_value = MoveDecision(uci_move="e2e4")
    app.dependency_overrides[get_opponent_provider] = lambda: opponent
    created = client.post(
        "/v1/play-sessions", json={"player_id": "skill-player", "player_color": "random"}
    )
    assert created.status_code == 200, created.text
    assert created.json()["player_color"] == expected
    assert created.json()["opponent_config"]["player_color"] == expected
    assert (
        client.get(f"/v1/play-sessions/{created.json()['id']}").json()["player_color"] == expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider, expected", [("stockfish", 0), ("maia", 10)])
async def test_service_unspecified_strength_preserves_legacy_provider_default(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, provider: str, expected: int
) -> None:
    play_session = PlaySession(player_id="skill-player", opponent_config={"provider": provider})
    db_session.add(play_session)
    db_session.commit()
    opponent = AsyncMock()
    opponent.choose_move.return_value = MoveDecision(uci_move="e7e5")
    service = PlaySessionService(db_session, StockfishOpponentProvider(StockfishConfig()))
    monkeypatch.setattr(service, "opponent_provider_for", lambda config: opponent)
    await service.make_move(play_session.id, "e2e4")
    context = opponent.choose_move.await_args.args[1]
    assert context.strength_setting == expected
    assert context.bot_level is None


def test_legacy_strength_session_can_resume_and_play(
    client: TestClient, db_session: Session
) -> None:
    authorize(client, db_session)
    game = Game(pgn="", moves=["e2e4"], owner_player_id="skill-player", owner_color="black")
    db_session.add(game)
    db_session.commit()
    play_session = PlaySession(
        player_id="skill-player", game_id=game.id, opponent_config={"strength": "5"}
    )
    db_session.add(play_session)
    db_session.commit()
    opponent = AsyncMock()
    opponent.choose_move.return_value = MoveDecision(uci_move="g1f3")
    app.dependency_overrides[get_opponent_provider] = lambda: opponent
    resumed = client.get(f"/v1/play-sessions/{play_session.id}")
    assert resumed.status_code == 200
    assert resumed.json()["player_color"] == "black"
    played = client.post(f"/v1/play-sessions/{play_session.id}/moves", json={"move": "e7e5"})
    assert played.status_code == 200, played.text
    context = opponent.choose_move.await_args.args[1]
    assert context.bot_level is None
    assert context.strength_setting == 5


def test_black_coach_diagnostic_uses_the_human_move_not_the_bot_opening(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorize(client, db_session)
    opponent = AsyncMock()
    opponent.choose_move.side_effect = [
        MoveDecision(uci_move="e2e4"),
        MoveDecision(uci_move="g1f3"),
    ]
    app.dependency_overrides[get_opponent_provider] = lambda: opponent
    prepare = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "scan64.chess.games.play_session_service.prepare_coach_interruption", prepare
    )
    created = client.post(
        "/v1/play-sessions",
        json={"player_id": "skill-player", "player_color": "black", "coach_mode": True},
    )
    assert created.status_code == 200, created.text
    prepare.assert_not_awaited()
    played = client.post(f"/v1/play-sessions/{created.json()['id']}/moves", json={"move": "e7e5"})
    assert played.status_code == 200, played.text
    prepare.assert_awaited_once()
    arguments = prepare.await_args.kwargs
    assert arguments["before_board"].turn == chess.BLACK
    assert arguments["after_board"].turn == chess.WHITE
    assert arguments["played_move"] == "e5"
    assert arguments["history_san"] == ["e4", "e5"]
    assert arguments["game"].owner_color == "black"


def test_takeback_conflicts_when_resign_commits_after_read(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scan64.persistence.database as db_module
    from scan64.chess.games import play_session_service as service_module

    authorize(client, db_session)
    opponent = AsyncMock()
    opponent.choose_move.return_value = MoveDecision(uci_move="e7e5")
    app.dependency_overrides[get_opponent_provider] = lambda: opponent
    created = client.post("/v1/play-sessions", json={"player_id": "skill-player"}).json()
    session_id = created["id"]
    assert client.post(
        f"/v1/play-sessions/{session_id}/moves", json={"move": "e2e4"}
    ).status_code == 200
    original_build = service_module.build_pgn
    injected = False

    def resign_before_write(game: Game) -> str:
        nonlocal injected
        if not injected:
            injected = True
            with Session(db_module.engine) as concurrent:
                PlaySessionService(concurrent, opponent).resign(UUID(session_id))
        return original_build(game)

    monkeypatch.setattr(service_module, "build_pgn", resign_before_write)
    response = client.post(f"/v1/play-sessions/{session_id}/takeback")
    assert injected
    assert response.status_code == 409, response.text
    db_session.expire_all()
    play = db_session.get(PlaySession, UUID(session_id))
    assert play is not None and play.status == "completed"
    game = db_session.get(Game, play.game_id)
    assert game is not None
    assert game.moves == ["e2e4", "e7e5"]
    assert game.result == "0-1"
    assert "1. e4 e5" in game.pgn
    assert '[Result "0-1"]' in game.pgn


def test_move_conflicts_when_takeback_commits_during_engine_wait(
    client: TestClient, db_session: Session
) -> None:
    import scan64.persistence.database as db_module

    authorize(client, db_session)
    opponent = AsyncMock()
    opponent.choose_move.return_value = MoveDecision(uci_move="e7e5")
    app.dependency_overrides[get_opponent_provider] = lambda: opponent
    session_id = client.post(
        "/v1/play-sessions", json={"player_id": "skill-player"}
    ).json()["id"]
    assert client.post(
        f"/v1/play-sessions/{session_id}/moves", json={"move": "e2e4"}
    ).status_code == 200

    async def takeback_before_reply(*args: object) -> MoveDecision:
        with Session(db_module.engine) as concurrent:
            PlaySessionService(concurrent, opponent).takeback(UUID(session_id))
        return MoveDecision(uci_move="b8c6")

    opponent.choose_move.side_effect = takeback_before_reply
    response = client.post(
        f"/v1/play-sessions/{session_id}/moves", json={"move": "g1f3"}
    )
    assert response.status_code == 409, response.text
    db_session.expire_all()
    play = db_session.get(PlaySession, UUID(session_id))
    assert play is not None and play.status == "active"
    game = db_session.get(Game, play.game_id)
    assert game is not None
    assert game.moves == []
    assert "Nf3" not in game.pgn

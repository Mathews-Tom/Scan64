"""Durable human prefixes and fenced, explicitly resumed opponent replies."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from scan64.api.models import Player
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.assistance import PlaySnapshot, assistance_modes, persist_assistance
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.pgn import build_pgn
from scan64.chess.games.play_session_service import PlaySessionNotActive, PlaySessionService
from scan64.chess.opponents.protocols import MoveDecision
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.providers.stockfish.adapter import StockfishConfig
from tests.integration.test_play_assistance import OWNER, _register, _source


@pytest.fixture
def recovery_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bot-recovery.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Player(id=OWNER))
        session.commit()
    try:
        yield engine
    finally:
        engine.dispose()


def _seed(
    engine: Engine,
    *,
    existing_game: bool = True,
    moves: tuple[str, ...] = (),
    coach_mode: bool = False,
) -> UUID:
    with Session(engine) as session:
        game = None
        if existing_game:
            game = Game(pgn="", moves=list(moves), white=OWNER, black="Stockfish")
            game.pgn = build_pgn(game)
            session.add(game)
        play = PlaySession(
            player_id=OWNER,
            game_id=game.id if game else None,
            coach_mode=coach_mode,
            assistance_tracking_version=1,
            opponent_config={
                "player_color": "white",
                "assistance_generation": "0",
                "initial_ply": "0",
            },
        )
        session.add(play)
        session.commit()
        return play.id


def _service(
    session: Session, monkeypatch: pytest.MonkeyPatch, choose_move: AsyncMock
) -> PlaySessionService:
    provider = StockfishOpponentProvider(StockfishConfig())
    monkeypatch.setattr(provider, "choose_move", choose_move)
    return PlaySessionService(session, provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_game", [False, True])
@pytest.mark.parametrize("coach_mode", [False, True])
async def test_human_move_pgn_and_claim_are_visible_while_opponent_is_blocked(
    recovery_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    existing_game: bool,
    coach_mode: bool,
) -> None:
    session_id = _seed(recovery_engine, existing_game=existing_game, coach_mode=coach_mode)
    with Session(recovery_engine) as session:
        play = session.get(PlaySession, session_id)
        assert play is not None
        game = session.get(Game, play.game_id) if play.game_id else None
        generation = persist_assistance(
            session, PlaySnapshot.capture(play, game), chess.Board(), 0, mode="guard"
        )
    started, release = asyncio.Event(), asyncio.Event()

    async def reply(*_: object) -> MoveDecision:
        started.set()
        await release.wait()
        return MoveDecision("e7e5")

    async def diagnose(**_: object) -> None:
        with Session(recovery_engine) as observer:
            stored_play = observer.get(PlaySession, session_id)
            assert stored_play is not None and stored_play.game_id is not None
            stored_game = observer.get(Game, stored_play.game_id)
            assert stored_game is not None and stored_game.moves == ["e2e4"]
            assert "1. e4" in stored_game.pgn

    diagnostic = AsyncMock(side_effect=diagnose)
    monkeypatch.setattr(
        "scan64.chess.games.play_session_service.prepare_coach_interruption", diagnostic
    )
    opponent = AsyncMock(side_effect=reply)
    with Session(recovery_engine) as session:
        service = _service(session, monkeypatch, opponent)
        task = asyncio.create_task(
            service.make_move(session_id, "e2e4", expected_generation=generation)
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            with Session(recovery_engine) as observer:
                stored_play = observer.get(PlaySession, session_id)
                assert stored_play is not None and stored_play.game_id is not None
                stored_game = observer.get(Game, stored_play.game_id)
                assert stored_game is not None and stored_game.moves == ["e2e4"]
                assert "1. e4" in stored_game.pgn
                assert int(stored_play.opponent_config["assistance_generation"]) == generation + 1
                assert assistance_modes(observer, session_id) == ["guard"]
                assert stored_play.status == "active" and stored_game.result == "*"
        finally:
            release.set()
            result = await asyncio.wait_for(task, timeout=2)
    assert result.opponent_move == "e7e5" and result.status == "active"
    assert diagnostic.await_count == int(coach_mode)
    position, context = opponent.await_args.args
    assert chess.Board(position.fen).turn == chess.BLACK and context.ply == 1
    with Session(recovery_engine) as observer:
        stored_play = observer.get(PlaySession, session_id)
        assert stored_play is not None
        stored_game = observer.get(Game, stored_play.game_id)
        assert stored_game is not None and stored_game.moves == ["e2e4", "e7e5"]
        assert int(stored_play.opponent_config["assistance_generation"]) == generation + 2
        assert len(observer.exec(select(Game)).all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("coach_mode", [False, True])
async def test_failed_ordinary_move_recovers_in_new_service_without_regrading_human(
    recovery_engine: Engine, monkeypatch: pytest.MonkeyPatch, coach_mode: bool
) -> None:
    session_id = _seed(recovery_engine, existing_game=False, coach_mode=coach_mode)
    diagnostic = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "scan64.chess.games.play_session_service.prepare_coach_interruption", diagnostic
    )
    opponent = AsyncMock(side_effect=[RuntimeError("engine failed"), MoveDecision("e7e5")])
    with Session(recovery_engine) as session:
        with pytest.raises(RuntimeError, match="engine failed"):
            await _service(session, monkeypatch, opponent).make_move(session_id, "e2e4")
    with Session(recovery_engine) as observer:
        stored_play = observer.get(PlaySession, session_id)
        assert stored_play is not None and stored_play.game_id is not None
        game = observer.get(Game, stored_play.game_id)
        assert game is not None and game.moves == ["e2e4"]
        assert "1. e4" in game.pgn
        assert stored_play.opponent_config["assistance_generation"] == "1"
    with Session(recovery_engine) as session:
        result = await _service(session, monkeypatch, opponent).resume_bot_turn(session_id)
        assert result.opponent_move == "e7e5" and result.interruption is None
        assert result.status == "active"
    assert diagnostic.await_count == int(coach_mode)
    assert opponent.await_count == 2
    with Session(recovery_engine) as observer:
        stored_play = observer.get(PlaySession, session_id)
        assert stored_play is not None and stored_play.game_id is not None
        game = observer.get(Game, stored_play.game_id)
        assert game is not None and game.moves == ["e2e4", "e7e5"]
        assert len(observer.exec(select(Game)).all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resign", "takeback", "headers"])
async def test_resumed_reply_cannot_overwrite_concurrent_canonical_edit(
    recovery_engine: Engine, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    prefix = ("e2e4", "e7e5", "g1f3")
    session_id = _seed(recovery_engine, moves=prefix)
    started, release = asyncio.Event(), asyncio.Event()

    async def reply(*_: object) -> MoveDecision:
        started.set()
        await release.wait()
        return MoveDecision("b8c6")

    with Session(recovery_engine) as pending:
        service = _service(pending, monkeypatch, AsyncMock(side_effect=reply))
        task = asyncio.create_task(service.resume_bot_turn(session_id))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            with Session(recovery_engine) as concurrent:
                editor = _service(concurrent, monkeypatch, AsyncMock())
                if operation == "resign":
                    editor.resign(session_id)
                elif operation == "takeback":
                    editor.takeback(session_id)
                else:
                    play = concurrent.get(PlaySession, session_id)
                    assert play is not None and play.game_id is not None
                    game = concurrent.get(Game, play.game_id)
                    assert game is not None
                    game.headers = {"Event": "Changed while opponent worked"}
                    game.pgn = build_pgn(game)
                    concurrent.add(game)
                    concurrent.commit()
        finally:
            release.set()
            with pytest.raises(PlaySessionNotActive, match="changed"):
                await asyncio.wait_for(task, timeout=2)
    with Session(recovery_engine) as observer:
        stored_play = observer.get(PlaySession, session_id)
        assert stored_play is not None and stored_play.game_id is not None
        game = observer.get(Game, stored_play.game_id)
        assert game is not None
        assert game.moves == list(prefix[:-1] if operation == "takeback" else prefix)
        assert "Nc6" not in game.pgn
        assert stored_play.status == ("completed" if operation == "resign" else "active")
        assert game.result == ("0-1" if operation == "resign" else "*")
        if operation == "headers":
            assert game.headers == {"Event": "Changed while opponent worked"}


@pytest.mark.asyncio
async def test_competing_resumes_publish_only_one_opponent_reply(
    recovery_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = _seed(recovery_engine, moves=("e2e4",))
    first_started, second_started = asyncio.Event(), asyncio.Event()
    first_release, second_release = asyncio.Event(), asyncio.Event()

    async def first_reply(*_: object) -> MoveDecision:
        first_started.set()
        await first_release.wait()
        return MoveDecision("e7e5")

    async def second_reply(*_: object) -> MoveDecision:
        second_started.set()
        await second_release.wait()
        return MoveDecision("c7c5")

    with Session(recovery_engine) as first, Session(recovery_engine) as second:
        winner = asyncio.create_task(
            _service(first, monkeypatch, AsyncMock(side_effect=first_reply)).resume_bot_turn(
                session_id
            )
        )
        loser = asyncio.create_task(
            _service(second, monkeypatch, AsyncMock(side_effect=second_reply)).resume_bot_turn(
                session_id
            )
        )
        try:
            await asyncio.wait_for(first_started.wait(), timeout=2)
            await asyncio.wait_for(second_started.wait(), timeout=2)
            first_release.set()
            result = await asyncio.wait_for(winner, timeout=2)
            second_release.set()
            with pytest.raises(PlaySessionNotActive, match="changed"):
                await asyncio.wait_for(loser, timeout=2)
        finally:
            first_release.set()
            second_release.set()
            await asyncio.gather(winner, loser, return_exceptions=True)
    assert result.opponent_move == "e7e5" and result.status == "active"
    with Session(recovery_engine) as observer:
        play = observer.get(PlaySession, session_id)
        assert play is not None and play.game_id is not None
        game = observer.get(Game, play.game_id)
        assert game is not None and game.moves == ["e2e4", "e7e5"]
        assert "1. e4 e5" in game.pgn
        assert play.opponent_config["assistance_generation"] == "1"


def test_resume_retains_owner_authentication_turn_and_terminal_boundaries(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _register(db_session)
    other = _register(db_session, "bot-recovery-other")
    play, game, _ = _source(db_session)
    session_id = play.id
    opponent = AsyncMock()
    monkeypatch.setattr(StockfishOpponentProvider, "choose_move", opponent)
    path = f"/v1/play-sessions/{session_id}/resume"
    assert client.post(path).status_code == 401
    assert client.post(path, headers=other).status_code == 404
    assert client.post(f"/v1/play-sessions/{uuid4()}/resume", headers=owner).status_code == 404
    assert client.post(path, headers=owner).status_code == 400
    game.moves = ["e2e4"]
    game.pgn = build_pgn(game)
    db_session.add(game)
    db_session.commit()
    for _ in range(2):
        assert client.get(f"/v1/play-sessions/{session_id}", headers=owner).status_code == 200
    opponent.assert_not_awaited()
    play.status = "completed"
    db_session.add(play)
    db_session.commit()
    assert client.post(path, headers=owner).status_code == 409
    opponent.assert_not_awaited()


@pytest.mark.asyncio
async def test_resumed_mate_completes_and_enqueues_analysis_once(
    recovery_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = ("f2f3", "e7e5", "g2g4")
    session_id = _seed(recovery_engine, moves=prefix)
    with Session(recovery_engine) as session:
        play = session.get(PlaySession, session_id)
        assert play is not None and play.game_id is not None
        game = session.get(Game, play.game_id)
        assert game is not None
        game.owner_player_id = OWNER
        session.add(game)
        session.commit()
    opponent = AsyncMock(return_value=MoveDecision("d8h4"))
    with Session(recovery_engine) as session:
        service = _service(session, monkeypatch, opponent)
        result = await service.resume_bot_turn(session_id)
        assert result.status == "completed" and result.opponent_move == "d8h4"
        assert len(service.pending_analysis) == 1
        assert service.pending_analysis[0][0] == OWNER
        with pytest.raises(PlaySessionNotActive, match="completed"):
            await service.resume_bot_turn(session_id)
    opponent.assert_awaited_once()
    with Session(recovery_engine) as observer:
        play = observer.get(PlaySession, session_id)
        assert play is not None and play.game_id is not None
        game = observer.get(Game, play.game_id)
        assert game is not None and game.moves == [*prefix, "d8h4"]
        assert game.result == "0-1" and "Qh4#" in game.pgn
        jobs = observer.exec(select(AnalysisJob).where(AnalysisJob.game_id == game.id)).all()
        assert len(jobs) == 1

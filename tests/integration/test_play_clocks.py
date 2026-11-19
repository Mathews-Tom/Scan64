from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.api.play as play_api
import scan64.chess.games.clocks as clocks
import scan64.chess.games.play_session_service as play_service
from scan64.api.app import app
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.clocks import ClockConflict, PlayClockRuntime, clock_control, project_clock
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.play_session_service import PlaySessionService, TakebackNotAllowed
from scan64.chess.opponents.protocols import MoveDecision, OpponentContext
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.providers.stockfish.adapter import StockfishConfig


class FrozenTime:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 19, 12, tzinfo=UTC)

    def advance(self, milliseconds: int) -> None:
        self.now += timedelta(milliseconds=milliseconds)


class ScriptedOpponent(StockfishOpponentProvider):
    def __init__(self, move: str = "e7e5", effect=None) -> None:
        super().__init__(StockfishConfig())
        self.move = move
        self.effect = effect
        self.contexts: list[OpponentContext] = []

    async def choose_move(self, position, context: OpponentContext) -> MoveDecision:
        self.contexts.append(context)
        if self.effect is not None:
            await self.effect()
        return MoveDecision(self.move)


@pytest.fixture
def frozen_time(monkeypatch: pytest.MonkeyPatch) -> FrozenTime:
    frozen = FrozenTime()
    monkeypatch.setattr(clocks, "utc_now", lambda: frozen.now)
    monkeypatch.setattr(play_service, "utc_now", lambda: frozen.now)
    monkeypatch.setattr(play_api, "utc_now", lambda: frozen.now)

    async def no_delay(seconds: float) -> None:
        return None

    monkeypatch.setattr(play_service, "sleep", no_delay)
    return frozen


@pytest.fixture
def scripted_api():
    provider = ScriptedOpponent()
    app.dependency_overrides[play_api.get_opponent_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(play_api.get_opponent_provider, None)


def seed_clock(
    db: Session,
    frozen: FrozenTime,
    *,
    moves: tuple[str, ...] = (),
    fen: str | None = None,
    player_color: str = "white",
    increment: int = 2,
    remaining: int = 60_000,
) -> tuple[PlaySession, Game, PlayClock]:
    board = chess.Board(fen or chess.STARTING_FEN)
    for move in moves:
        board.push_uci(move)
    game = Game(
        pgn="",
        headers={"TimeControl": f"60+{increment}", **({"FEN": fen} if fen else {})},
        moves=list(moves),
        owner_player_id="alice",
        owner_color=player_color,
    )
    db.add(game)
    db.flush()
    play = PlaySession(
        player_id="alice",
        game_id=game.id,
        opponent_config={
            "provider": "stockfish",
            "bot_level": "1",
            "player_color": player_color,
            "assistance_generation": "0",
        },
        clock_config={"initial_seconds": "60", "increment_seconds": str(increment)},
    )
    db.add(play)
    db.flush()
    clock = PlayClock(
        session_id=play.id,
        initial_seconds=60,
        increment_seconds=increment,
        white_remaining_ms=remaining,
        black_remaining_ms=remaining,
        active_color="white" if board.turn else "black",
        deadline_at=frozen.now + timedelta(milliseconds=remaining),
    )
    db.add(clock)
    db.commit()
    return play, game, clock


def register(db: Session, player_id: str = "alice") -> dict[str, str]:
    token, token_hash = issue_player_token()
    db.add(Player(id=player_id))
    db.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def start(client: TestClient, auth: dict[str, str], **extra):
    response = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "alice",
            "clock_config": {
                "initial_seconds": 60,
                "increment_seconds": 2,
            },
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_projection_clamps_and_normalizes_sqlite_datetime(db_session, frozen_time):
    play, _, clock = seed_clock(db_session, frozen_time)
    clock.deadline_at = frozen_time.now.replace(tzinfo=None) + timedelta(seconds=60)
    frozen_time.advance(1250)
    view = project_clock(clock)
    assert view.white_remaining_ms == 58_750
    assert view.black_remaining_ms == 60_000
    assert view.deadline_at.tzinfo == UTC
    assert view.server_now.tzinfo == UTC
    frozen_time.advance(100_000)
    assert project_clock(clock).white_remaining_ms == 0
    assert clock_control(play, clock).initial_seconds == 60
    assert clock_control(play, None) is None
    play.clock_config = {"initial_seconds": "61", "increment_seconds": "2"}
    with pytest.raises(ValueError, match="does not match"):
        clock_control(play, clock)


@pytest.mark.asyncio
async def test_human_boundary_and_both_increments_are_durable(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time)
    session_id, game_id = play.id, game.id
    frozen_time.advance(2000)

    async def inspect_boundary():
        assert not db_session.in_transaction()
        with Session(db_session.get_bind()) as other:
            assert other.get(Game, game_id).moves == ["e2e4"]
            saved = other.get(PlayClock, session_id)
            assert saved.version == 1
            assert saved.active_color == "black"
            assert saved.white_remaining_ms == 60_000
        frozen_time.advance(1000)

    opponent = ScriptedOpponent(effect=inspect_boundary)
    result = await PlaySessionService(db_session, opponent).make_move(session_id, "e2e4", 0)
    assert result.opponent_move == "e7e5"
    db_session.refresh(clock)
    db_session.refresh(game)
    assert clock.version == 2
    assert clock.active_color == "white"
    assert clock.white_remaining_ms == 60_000
    assert clock.black_remaining_ms == 61_000
    assert game.moves == ["e2e4", "e7e5"]
    assert opponent.contexts[0].time_remaining_ms == 60_000
    assert opponent.contexts[0].bot_level == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, -1, 1])
async def test_version_is_required_before_engine_work(db_session, frozen_time, version):
    play, game, clock = seed_clock(db_session, frozen_time)
    opponent = ScriptedOpponent()
    with pytest.raises(ClockConflict, match="version"):
        await PlaySessionService(db_session, opponent).make_move(play.id, "e2e4", version)
    assert opponent.contexts == []
    assert game.moves == []
    assert clock.version == 0


@pytest.mark.asyncio
async def test_engine_failure_keeps_human_move_for_explicit_resume(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time)
    session_id = play.id

    async def fail():
        raise RuntimeError("engine unavailable")

    with pytest.raises(RuntimeError, match="engine unavailable"):
        await PlaySessionService(db_session, ScriptedOpponent(effect=fail)).make_move(
            session_id, "e2e4", 0
        )
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert clock.active_color == "black"
    assert clock.version == 1
    frozen_time.advance(3000)
    service = PlaySessionService(db_session, ScriptedOpponent())
    assert await service.resume_opponent(session_id, 1) == "e7e5"
    db_session.refresh(clock)
    assert clock.black_remaining_ms == 59_000
    assert clock.version == 2


@pytest.mark.asyncio
async def test_provider_timeout_is_not_misreported_as_flag(db_session, frozen_time):
    play, _, clock = seed_clock(db_session, frozen_time)

    async def fail():
        raise TimeoutError("provider budget exhausted")

    with pytest.raises(TimeoutError, match="provider budget"):
        await PlaySessionService(db_session, ScriptedOpponent(effect=fail)).make_move(
            play.id, "e2e4", 0
        )
    db_session.refresh(clock)
    assert clock.active_color == "black"
    assert clock.terminal_reason is None


@pytest.mark.asyncio
async def test_cancellation_leaves_restartable_bot_turn(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time)
    session_id = play.id
    entered = asyncio.Event()

    async def block():
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        PlaySessionService(db_session, ScriptedOpponent(effect=block)).make_move(
            session_id, "e2e4", 0
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert clock.version == 1
    assert (
        await PlaySessionService(db_session, ScriptedOpponent()).resume_opponent(session_id, 1)
        == "e7e5"
    )


@pytest.mark.asyncio
async def test_duplicate_resume_does_not_start_duplicate_engine(db_session, frozen_time):
    play, _, _ = seed_clock(db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    runtime = PlayClockRuntime(db_session.get_bind())
    entered, release = asyncio.Event(), asyncio.Event()

    async def block():
        entered.set()
        await release.wait()

    opponent = ScriptedOpponent(effect=block)
    service = PlaySessionService(db_session, opponent, clock_runtime=runtime)
    task = asyncio.create_task(service.resume_opponent(session_id, 0))
    await entered.wait()
    with Session(db_session.get_bind()) as other:
        duplicate = PlaySessionService(other, opponent, clock_runtime=runtime)
        with pytest.raises(ClockConflict, match="already in progress"):
            await duplicate.resume_opponent(session_id, 0)
    assert len(opponent.contexts) == 1
    release.set()
    assert await task == "e7e5"
    await runtime.stop()


@pytest.mark.asyncio
async def test_late_engine_result_cannot_overwrite_resignation(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time)
    session_id = play.id

    async def resign_elsewhere():
        with Session(db_session.get_bind()) as other:
            PlaySessionService(other, ScriptedOpponent()).resign(session_id)

    with pytest.raises(ClockConflict):
        await PlaySessionService(db_session, ScriptedOpponent(effect=resign_elsewhere)).make_move(
            session_id, "e2e4", 0
        )
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert game.result == "0-1"
    assert clock.terminal_reason == "resignation"
    assert clock.version == 2
    assert (
        len(db_session.exec(select(AnalysisJob).where(AnalysisJob.game_id == game.id)).all()) == 1
    )


@pytest.mark.asyncio
async def test_result_at_exact_deadline_flags_without_publishing_bot_move(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",))

    async def arrive_late():
        frozen_time.advance(60_000)

    with pytest.raises(ClockConflict, match="deadline"):
        await PlaySessionService(db_session, ScriptedOpponent(effect=arrive_late)).resume_opponent(
            play.id, 0
        )
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert game.result == "1-0"
    assert clock.black_remaining_ms == 0
    assert clock.flagged_color == "black"
    assert clock.active_color is None


@pytest.mark.asyncio
async def test_real_bot_timeout_is_bounded_and_flags(db_session, frozen_time):
    play, game, clock = seed_clock(
        db_session, frozen_time, moves=("e2e4",), remaining=10, increment=0
    )

    async def never_finishes():
        try:
            await asyncio.Event().wait()
        finally:
            frozen_time.advance(10)

    with pytest.raises(ClockConflict, match="deadline"):
        await PlaySessionService(
            db_session, ScriptedOpponent(effect=never_finishes)
        ).resume_opponent(play.id, 0)
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert clock.terminal_reason == "flag"
    assert clock.version == 1


@pytest.mark.parametrize(
    ("fen", "expected"),
    [
        (chess.STARTING_FEN, "0-1"),
        ("7k/8/8/8/8/8/6R1/K7 w - - 0 1", "1/2-1/2"),
    ],
)
def test_human_flag_and_material_specific_draw(db_session, frozen_time, fen, expected):
    play, game, clock = seed_clock(db_session, frozen_time, fen=fen)
    frozen_time.advance(60_000)
    service = PlaySessionService(db_session, ScriptedOpponent())
    service.resign(play.id)  # Expiration wins over the requested resignation.
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.result == expected
    assert clock.terminal_reason == "flag"
    assert clock.flagged_color == "white"
    assert clock.white_remaining_ms == 0
    assert clock.deadline_at is None


@pytest.mark.asyncio
async def test_terminal_human_move_stops_clock_atomically(db_session, frozen_time):
    play, game, clock = seed_clock(
        db_session, frozen_time, moves=("f2f3", "e7e5", "g2g4"), player_color="black"
    )
    opponent = ScriptedOpponent()
    result = await PlaySessionService(db_session, opponent).make_move(play.id, "d8h4", 0)
    assert result.opponent_move is None
    assert opponent.contexts == []
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.result == "0-1"
    assert clock.terminal_reason == "board"
    assert clock.active_color is None
    assert clock.black_remaining_ms == 62_000
    assert clock.version == 1


def test_timed_takeback_never_changes_clock_or_moves(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4", "e7e5"))
    with pytest.raises(TakebackNotAllowed, match="timed"):
        PlaySessionService(db_session, ScriptedOpponent()).takeback(play.id)
    assert game.moves == ["e2e4", "e7e5"]
    assert clock.version == 0


@pytest.mark.asyncio
async def test_legacy_static_clock_json_is_untimed(db_session):
    game = Game(pgn="", moves=[], owner_player_id="alice")
    db_session.add(game)
    db_session.flush()
    play = PlaySession(
        player_id="alice",
        game_id=game.id,
        clock_config={"time_remaining_ms": "1"},
        opponent_config={"player_color": "white"},
    )
    db_session.add(play)
    db_session.commit()
    opponent = ScriptedOpponent()
    await PlaySessionService(db_session, opponent).make_move(play.id, "e2e4")
    assert opponent.contexts[0].time_remaining_ms is None
    assert db_session.get(PlayClock, play.id) is None


@pytest.mark.asyncio
async def test_runtime_expires_without_browser_and_admits_analysis(
    db_session, frozen_time, monkeypatch
):
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    frozen_time.advance(60_000)
    admitted = asyncio.Event()

    async def submit(player_id, job_id, pool):
        assert player_id == "alice"
        admitted.set()

    monkeypatch.setattr("scan64.chess.analysis.jobs.submit_analysis_job", submit)
    runtime = PlayClockRuntime(db_session.get_bind())
    runtime.start()
    try:
        await asyncio.wait_for(admitted.wait(), timeout=1)
        with Session(db_session.get_bind()) as other:
            assert other.get(PlaySession, session_id).status == "completed"
            assert other.get(PlayClock, session_id).terminal_reason == "flag"
            assert other.get(Game, game.id).moves == ["e2e4"]
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_restart_does_not_run_pending_engine_and_shutdown_cancels_work(
    db_session, frozen_time
):
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    runtime = PlayClockRuntime(db_session.get_bind())
    runtime.start()
    await asyncio.sleep(0)
    await runtime.stop()
    db_session.refresh(clock)
    assert clock.version == 0
    assert clock.active_color == "black"
    restarted = PlayClockRuntime(db_session.get_bind())
    restarted.start()
    entered = asyncio.Event()

    async def wait_forever():
        entered.set()
        await asyncio.Event().wait()

    service = PlaySessionService(
        db_session, ScriptedOpponent(effect=wait_forever), clock_runtime=restarted
    )
    task = asyncio.create_task(service.resume_opponent(session_id, 0))
    await entered.wait()
    await restarted.stop()
    assert task.cancelled()
    db_session.refresh(game)
    assert game.moves == ["e2e4"]


def test_timed_creation_and_opponent_first_start(client, db_session, frozen_time, scripted_api):
    auth = register(db_session)
    scripted_api.move = "e2e4"
    created = start(client, auth, player_color="black")
    assert created["opponent_move"] is None
    assert created["clock"]["active_color"] == "white"
    assert created["clock"]["version"] == 0
    assert scripted_api.contexts == []
    resumed = client.post(
        f"/v1/play-sessions/{created['id']}/opponent-move",
        json={"expected_clock_version": 0},
        headers=auth,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["opponent_move"] == "e2e4"
    assert resumed.json()["clock"]["active_color"] == "black"
    assert resumed.json()["clock"]["version"] == 1
    assert created["clock_config"] == {"initial_seconds": "60", "increment_seconds": "2"}
    game = db_session.get(Game, UUID(created["game_id"]))
    assert game.headers["TimeControl"] == "60+2"
    assert game.moves == ["e2e4"]


def test_bot_first_creation_retains_retryable_identity_when_engine_fails(
    client, db_session, frozen_time, scripted_api
):
    auth = register(db_session)

    async def fail() -> None:
        raise RuntimeError("engine unavailable")

    scripted_api.effect = fail
    created = start(client, auth, player_color="black")
    assert created["game_id"] is not None
    assert created["opponent_move"] is None
    assert created["clock"]["version"] == 0
    assert created["clock"]["active_color"] == "white"
    failed = client.post(
        f"/v1/play-sessions/{created['id']}/opponent-move",
        json={"expected_clock_version": 0},
        headers=auth,
    )
    assert failed.status_code == 500
    assert failed.json()["detail"] == "engine unavailable"
    pending = client.get(f"/v1/play-sessions/{created['id']}", headers=auth).json()
    assert pending["clock"]["version"] == 0
    assert pending["clock"]["active_color"] == "white"
    game = db_session.get(Game, UUID(created["game_id"]))
    assert game.moves == []
    scripted_api.effect = None
    scripted_api.move = "e2e4"
    resumed = client.post(
        f"/v1/play-sessions/{created['id']}/opponent-move",
        json={"expected_clock_version": 0},
        headers=auth,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["clock"]["version"] == 1
    db_session.refresh(game)
    assert game.moves == ["e2e4"]


@pytest.mark.parametrize(
    "control",
    [
        {"initial_seconds": True},
        {"initial_seconds": "60"},
        {"initial_seconds": 0},
        {"initial_seconds": 86401},
        {"initial_seconds": 60, "increment_seconds": -1},
        {"initial_seconds": 60, "increment_seconds": 61},
        {"initial_seconds": 60, "increment_seconds": 1.5},
        {"initial_seconds": 60, "remaining_ms": 1},
        {"time_remaining_ms": "60000"},
    ],
)
def test_http_rejects_untrusted_clock_inputs(client, db_session, control):
    auth = register(db_session)
    response = client.post(
        "/v1/play-sessions",
        json={"player_id": "alice", "clock_config": control},
        headers=auth,
    )
    assert response.status_code == 422
    assert db_session.exec(select(PlayClock)).all() == []


def test_http_versions_ownership_and_read_only_pending_state(
    client, db_session, frozen_time, scripted_api
):
    auth = register(db_session)
    created = start(client, auth)
    session_id = created["id"]
    missing = client.post(
        f"/v1/play-sessions/{session_id}/moves", json={"move": "e2e4"}, headers=auth
    )
    assert missing.status_code == 409
    bad_type = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "e2e4", "expected_clock_version": True},
        headers=auth,
    )
    assert bad_type.status_code == 422
    other_auth = register(db_session, "bob")
    hidden = client.post(
        f"/v1/play-sessions/{session_id}/opponent-move",
        json={"expected_clock_version": 0},
        headers=other_auth,
    )
    assert hidden.status_code == 404
    wrong_turn = client.post(
        f"/v1/play-sessions/{session_id}/opponent-move",
        json={"expected_clock_version": 0},
        headers=auth,
    )
    assert wrong_turn.status_code == 409
    assert scripted_api.contexts == []
    fetched = client.get(f"/v1/play-sessions/{session_id}", headers=auth)
    assert fetched.status_code == 200
    assert scripted_api.contexts == []
    played = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "e2e4", "expected_clock_version": 0},
        headers=auth,
    )
    assert played.status_code == 200, played.text
    assert played.json()["clock"]["version"] == 2
    stale = client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "g1f3", "expected_clock_version": 0},
        headers=auth,
    )
    assert stale.status_code == 409


def test_timed_game_is_private_from_later_untimed_continuation(
    client, db_session, frozen_time, scripted_api
):
    auth = register(db_session)
    timed = start(client, auth)
    source_id = UUID(timed["game_id"])
    source = db_session.get(Game, source_id)
    original_pgn = source.pgn
    continuation = client.post(
        "/v1/play-sessions",
        json={"player_id": "alice", "game_id": str(source_id)},
        headers=auth,
    )
    assert continuation.status_code == 200, continuation.text
    copied = continuation.json()
    assert copied["game_id"] != str(source_id)
    assert copied["clock"] is None
    assert copied["clock_config"] is None
    resigned = client.post(f"/v1/play-sessions/{copied['id']}/resign", headers=auth)
    assert resigned.status_code == 200
    db_session.refresh(source)
    assert source.result == "*"
    assert source.pgn == original_pgn
    saved = client.get(f"/v1/play-sessions/{timed['id']}", headers=auth).json()
    assert saved["status"] == "active"
    assert saved["clock"]["version"] == 0
    assert saved["clock"]["active_color"] == "white"


@pytest.mark.asyncio
async def test_coaching_sees_committed_boundary_and_does_not_pause_clock(
    db_session, frozen_time, monkeypatch
):
    play, game, clock = seed_clock(db_session, frozen_time)
    play.coach_mode = True
    db_session.add(play)
    db_session.commit()
    game_id, session_id = game.id, play.id

    async def prepare(**kwargs):
        assert not db_session.in_transaction()
        with Session(db_session.get_bind()) as other:
            assert other.get(Game, game_id).moves == ["e2e4"]
            assert other.get(PlayClock, session_id).active_color == "black"
        frozen_time.advance(2500)
        return None

    monkeypatch.setattr(play_service, "prepare_coach_interruption", prepare)
    opponent = ScriptedOpponent()
    await PlaySessionService(db_session, opponent).make_move(session_id, "e2e4", 0)
    assert opponent.contexts[0].time_remaining_ms == 57_500
    db_session.refresh(clock)
    assert clock.black_remaining_ms == 59_500


@pytest.mark.asyncio
async def test_bot_board_mate_stops_clock_and_enqueues_once(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time, moves=("f2f3", "e7e5", "g2g4"))
    service = PlaySessionService(db_session, ScriptedOpponent("d8h4"))
    assert await service.resume_opponent(play.id, 0) == "d8h4"
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.result == "0-1"
    assert clock.terminal_reason == "board"
    assert clock.active_color is None
    assert clock.black_remaining_ms == 62_000
    assert len(service.pending_analysis) == 1
    with pytest.raises(ClockConflict):
        await service.resume_opponent(play.id, 0)
    assert len(db_session.exec(select(AnalysisJob)).all()) == 1


@pytest.mark.asyncio
async def test_engine_real_work_reduces_pacing_sleep(db_session, frozen_time, monkeypatch):
    play, _, _ = seed_clock(db_session, frozen_time, moves=("e2e4",), remaining=3000, increment=0)
    slept = []

    async def record_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(play_service, "sleep", record_sleep)
    service = PlaySessionService(db_session, ScriptedOpponent())
    await service.resume_opponent(play.id, 0)
    assert len(slept) <= 1
    # min(1500, max(100, 3000 // 30)) == 100 ms, less real engine overhead.
    assert all(0 < delay <= 0.1 for delay in slept)


@pytest.mark.asyncio
async def test_clock_cas_rejects_snapshot_aba_and_preserves_move_boundary(db_session, frozen_time):
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",))
    session_id = play.id

    async def change_generation():
        with Session(db_session.get_bind()) as other:
            saved = other.get(PlaySession, session_id)
            saved.opponent_config = {
                **saved.opponent_config,
                "assistance_generation": "2",
            }
            other.add(saved)
            other.commit()

    service = PlaySessionService(db_session, ScriptedOpponent(effect=change_generation))
    with pytest.raises(play_service.PlaySessionNotActive):
        await service.resume_opponent(session_id, 0)
    db_session.refresh(game)
    db_session.refresh(clock)
    assert game.moves == ["e2e4"]
    assert clock.version == 0
    assert clock.active_color == "black"


@pytest.mark.asyncio
async def test_notify_wakes_scheduler_for_new_earlier_deadline(
    db_session, frozen_time, monkeypatch
):
    _, _, _ = seed_clock(db_session, frozen_time)
    runtime = PlayClockRuntime(db_session.get_bind())
    runtime.start()
    await asyncio.sleep(0)
    play, game, clock = seed_clock(db_session, frozen_time, moves=("e2e4",), remaining=100)
    session_id = play.id
    admitted = asyncio.Event()

    async def submit(player_id, job_id, pool):
        admitted.set()

    monkeypatch.setattr("scan64.chess.analysis.jobs.submit_analysis_job", submit)
    frozen_time.advance(100)
    runtime.notify()
    try:
        await asyncio.wait_for(admitted.wait(), timeout=1)
        db_session.refresh(clock)
        db_session.refresh(play)
        assert clock.terminal_reason == "flag"
        assert db_session.get(PlaySession, session_id).status == "completed"
    finally:
        await runtime.stop()


def test_timed_continuation_copies_owned_source(client, db_session, frozen_time, scripted_api):
    auth = register(db_session)
    source = Game(
        pgn="source",
        moves=["e2e4", "e7e5"],
        owner_player_id="alice",
        white="Alice",
        black="Original opponent",
        result="1-0",
    )
    db_session.add(source)
    db_session.commit()
    source_id = source.id
    created = start(client, auth, game_id=str(source_id))
    assert created["game_id"] != str(source_id)
    assert created["opponent_config"]["custom_start"] == "true"
    copy = db_session.get(Game, UUID(created["game_id"]))
    assert copy.moves == ["e2e4", "e7e5"]
    assert copy.result == "*"
    assert copy.headers["TimeControl"] == "60+2"
    db_session.refresh(source)
    assert source.result == "1-0"
    assert source.pgn == "source"
    continued = start(client, auth, game_id=created["game_id"], clock_config=None)
    assert continued["game_id"] != created["game_id"]
    ordinary = client.get(f"/v1/games/{continued['game_id']}", headers=auth)
    assert ordinary.status_code == 200
    ordinary_copy = db_session.get(Game, UUID(continued["game_id"]))
    assert ordinary_copy is not None
    assert ordinary_copy.moves == ["e2e4", "e7e5"]
    assert "1. e4 e5 *" in ordinary.json()["pgn"]
    assert "[TimeControl " not in ordinary.json()["pgn"]


def test_pending_http_get_does_not_run_engine_but_owned_command_resumes(
    client, db_session, frozen_time, scripted_api
):
    auth = register(db_session)
    play, _, _ = seed_clock(db_session, frozen_time, moves=("e2e4",))
    session_id = play.id
    state = client.get(f"/v1/play-sessions/{session_id}", headers=auth)
    assert state.status_code == 200
    assert state.json()["clock"]["active_color"] == "black"
    assert scripted_api.contexts == []
    resumed = client.post(
        f"/v1/play-sessions/{session_id}/opponent-move",
        json={"expected_clock_version": 0},
        headers=auth,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["opponent_move"] == "e7e5"
    assert resumed.json()["clock"]["version"] == 1
    assert len(scripted_api.contexts) == 1


@pytest.mark.parametrize(
    ("remaining", "increment", "target"),
    [(10, 0, 10), (3000, 0, 100), (9000, 0, 300), (30_000, 2, 1500), (90_000, 0, 1500)],
)
def test_pacing_formula_is_bounded_by_live_balance(remaining, increment, target):
    assert clocks.thinking_target_ms(remaining, increment) == target

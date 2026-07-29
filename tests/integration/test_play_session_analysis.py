from __future__ import annotations

import logging
import threading
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.inflight import InFlightAnalysisLimiter, in_flight_cap
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.models import Game, PlaySession

FOOLS_MATE_FEN = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"


class _NoCandidateOrchestrator:
    def __init__(self, *_: object) -> None:
        pass

    async def run_fast_pass(self, _: list[str], initial_fen: str | None = None) -> list[object]:
        return []


def _register(db_session: Session, player_id: str = "alice") -> dict[str, str]:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


def _mate_session(db_session: Session) -> PlaySession:
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
    return play_session


def test_playing_to_mate_completes_an_analysis_job_without_a_further_call(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _register(db_session)
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _NoCandidateOrchestrator)
    play_session = _mate_session(db_session)

    response = client.post(
        f"/v1/play-sessions/{play_session.id}/moves",
        json={"move": "d8h4"},
        headers={**auth, "Idempotency-Key": "mate"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    job = db_session.exec(
        select(AnalysisJob).where(AnalysisJob.game_id == play_session.game_id)
    ).one()
    assert job.status == "completed", job.error


def test_resigning_completes_an_analysis_job_without_a_further_call(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _register(db_session)
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _NoCandidateOrchestrator)
    created = client.post(
        "/v1/play-sessions",
        json={"player_id": "alice", "opponent_config": {"strength": "1"}},
        headers=auth,
    )
    session_id = created.json()["id"]
    client.post(
        f"/v1/play-sessions/{session_id}/moves",
        json={"move": "e2e4"},
        headers={**auth, "Idempotency-Key": "one"},
    )

    resigned = client.post(f"/v1/play-sessions/{session_id}/resign", headers=auth)

    assert resigned.status_code == 200, resigned.text
    play_session = db_session.get(PlaySession, UUID(session_id))
    assert play_session is not None
    job = db_session.exec(
        select(AnalysisJob).where(AnalysisJob.game_id == play_session.game_id)
    ).one()
    assert job.status == "completed", job.error


def test_resigning_before_a_move_enqueues_nothing(client: TestClient, db_session: Session) -> None:
    auth = _register(db_session)
    created = client.post(
        "/v1/play-sessions",
        json={
            "player_id": "alice",
            "opponent_config": {"strength": "1"},
            "initial_fen": FOOLS_MATE_FEN,
        },
        headers=auth,
    )
    assert created.status_code == 200, created.text

    resigned = client.post(f"/v1/play-sessions/{created.json()['id']}/resign", headers=auth)

    assert resigned.status_code == 200, resigned.text
    assert db_session.exec(select(AnalysisJob)).all() == []


def test_resigning_a_session_without_a_game_enqueues_nothing(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(db_session)
    created = client.post(
        "/v1/play-sessions",
        json={"player_id": "alice", "opponent_config": {"strength": "1"}},
        headers=auth,
    )
    assert created.status_code == 200, created.text

    resigned = client.post(f"/v1/play-sessions/{created.json()['id']}/resign", headers=auth)

    assert resigned.status_code == 200, resigned.text
    assert db_session.exec(select(AnalysisJob)).all() == []


def test_work_beyond_the_cap_is_queued_and_never_dropped() -> None:
    running = threading.Semaphore(0)
    release = threading.Event()
    observed: list[int] = []
    concurrent = 0
    guard = threading.Lock()

    def runner(job_id: UUID) -> None:
        nonlocal concurrent
        with guard:
            concurrent += 1
            observed.append(concurrent)
        running.release()
        release.wait(timeout=5)
        with guard:
            concurrent -= 1

    limiter = InFlightAnalysisLimiter(cap=1, runner=runner)
    job_ids = [uuid4() for _ in range(3)]
    worker = threading.Thread(target=limiter.submit, args=("alice", job_ids[0]))
    worker.start()
    assert running.acquire(timeout=5)

    limiter.submit("alice", job_ids[1])
    limiter.submit("alice", job_ids[2])
    assert limiter.queue_depth("alice") == 2

    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert max(observed) == 1
    assert len(observed) == 3
    assert limiter.queue_depth("alice") == 0


def test_a_failing_job_is_logged_and_does_not_drop_the_queue_behind_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    started = threading.Semaphore(0)
    release = threading.Event()
    ran: list[UUID] = []

    def runner(job_id: UUID) -> None:
        ran.append(job_id)
        if len(ran) == 1:
            started.release()
            release.wait(timeout=5)
            raise RuntimeError("engine unavailable")

    limiter = InFlightAnalysisLimiter(cap=1, runner=runner)
    first, second = uuid4(), uuid4()
    worker = threading.Thread(target=limiter.submit, args=("alice", first))

    with caplog.at_level(logging.ERROR, logger="scan64.chess.analysis.inflight"):
        worker.start()
        assert started.acquire(timeout=5)
        limiter.submit("alice", second)
        release.set()
        worker.join(timeout=5)

    assert ran == [first, second]
    assert f"Analysis job {first} failed" in caplog.text
    assert "engine unavailable" in caplog.text
    assert limiter.queue_depth("alice") == 0
    assert limiter.in_flight("alice") == 0


def test_a_second_player_is_not_blocked_by_the_first() -> None:
    ran: list[tuple[str, UUID]] = []

    def runner(job_id: UUID) -> None:
        ran.append(("run", job_id))

    limiter = InFlightAnalysisLimiter(cap=1, runner=runner)
    alice_job, bob_job = uuid4(), uuid4()
    limiter.submit("alice", alice_job)
    limiter.submit("bob", bob_job)

    assert [job for _, job in ran] == [alice_job, bob_job]
    assert limiter.queue_depth("bob") == 0


def test_a_job_killed_by_a_shutdown_signal_releases_its_slot() -> None:
    def runner(job_id: UUID) -> None:
        raise KeyboardInterrupt

    limiter = InFlightAnalysisLimiter(cap=1, runner=runner)

    with pytest.raises(KeyboardInterrupt):
        limiter.submit("alice", uuid4())

    assert limiter.in_flight("alice") == 0


def test_an_unreadable_cap_setting_is_rejected_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN64_ANALYSIS_IN_FLIGHT_CAP", "many")
    with pytest.raises(ValueError, match="must be an integer"):
        in_flight_cap()

    monkeypatch.setenv("SCAN64_ANALYSIS_IN_FLIGHT_CAP", "0")
    with pytest.raises(ValueError, match="at least 1"):
        in_flight_cap()

    monkeypatch.setenv("SCAN64_ANALYSIS_IN_FLIGHT_CAP", "4")
    assert in_flight_cap() == 4


def test_an_interrupted_drain_keeps_its_queue_for_the_next_submit() -> None:
    started = threading.Semaphore(0)
    release = threading.Event()
    ran: list[UUID] = []

    def runner(job_id: UUID) -> None:
        if not ran and job_id == first:
            started.release()
            release.wait(timeout=5)
            raise KeyboardInterrupt
        ran.append(job_id)

    limiter = InFlightAnalysisLimiter(cap=1, runner=runner)
    first, queued, later = uuid4(), uuid4(), uuid4()

    def submit_first() -> None:
        with pytest.raises(KeyboardInterrupt):
            limiter.submit("alice", first)

    worker = threading.Thread(target=submit_first)
    worker.start()
    assert started.acquire(timeout=5)
    limiter.submit("alice", queued)
    release.set()
    worker.join(timeout=5)

    assert limiter.in_flight("alice") == 0
    assert limiter.queue_depth("alice") == 1

    limiter.submit("alice", later)

    assert set(ran) == {queued, later}
    assert limiter.queue_depth("alice") == 0

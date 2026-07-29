from __future__ import annotations

import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.models import Game, PlaySession

FOOLS_MATE_FEN = "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2"


def _wait_for_terminal_job(
    db_session: Session, game_id: UUID, *, timeout: float = 2.0
) -> AnalysisJob:
    """Poll for the auto-enqueued analysis job to reach a terminal state.

    M41's admission control schedules a job's execution as an independent
    asyncio task rather than draining it synchronously within the request
    (M32's interim behaviour); a move or resign response no longer
    guarantees the job has finished by the time it returns, only that it
    was admitted without a further explicit call.
    """
    deadline = time.monotonic() + timeout
    while True:
        db_session.expire_all()
        job = db_session.exec(
            select(AnalysisJob).where(AnalysisJob.game_id == game_id)
        ).one_or_none()
        if job is not None and job.status in {"completed", "failed"}:
            return job
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"analysis job for game {game_id} did not reach a terminal state "
                f"within {timeout}s (last status: {job.status if job else 'missing'})"
            )
        time.sleep(0.01)


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
    job = _wait_for_terminal_job(db_session, play_session.game_id)
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
    job = _wait_for_terminal_job(db_session, play_session.game_id)
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


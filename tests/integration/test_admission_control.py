from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.admission import (
    AdmissionConfig,
    AdmissionController,
    daily_quota_from_env,
)
from scan64.chess.analysis.models import AnalysisJob
from scan64.chess.games.models import Game

PGN = '[Event "Admission test"]\n[White "White"]\n[Black "Black"]\n[Result "*"]\n\n1. e4 e5 *\n'


def _register(db_session: Session, player_id: str) -> dict[str, str]:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


# --- Direct AdmissionController coverage -----------------------------------
#
# These replace tests/integration/test_play_session_analysis.py's deleted
# InFlightAnalysisLimiter (M32) coverage with the equivalent guarantees
# against AdmissionController (M41), which supersedes and removes it.


@pytest.mark.asyncio
async def test_jobs_beyond_the_quota_are_queued_fair_share_and_never_dropped() -> None:
    controller = AdmissionController(AdmissionConfig(daily_quota_games=1))
    run_order: list[str] = []

    async def task(label: str) -> None:
        await asyncio.sleep(0.01)
        run_order.append(label)

    # First job for alice runs within quota; the next two are queued rather
    # than rejected or dropped.
    f1 = controller.submit("alice", lambda: task("alice-1"))
    f2 = controller.submit("alice", lambda: task("alice-2"))
    f3 = controller.submit("alice", lambda: task("alice-3"))

    await asyncio.gather(f1, f2, f3)

    assert set(run_order) == {"alice-1", "alice-2", "alice-3"}
    controller.stop()


@pytest.mark.asyncio
async def test_a_second_players_jobs_are_not_blocked_by_the_firsts_queue() -> None:
    controller = AdmissionController(AdmissionConfig(daily_quota_games=0))
    run_order: list[str] = []

    async def task(label: str) -> None:
        await asyncio.sleep(0.01)
        run_order.append(label)

    f_alice_1 = controller.submit("alice", lambda: task("alice"))
    f_alice_2 = controller.submit("alice", lambda: task("alice"))
    f_bob = controller.submit("bob", lambda: task("bob"))

    await asyncio.gather(f_alice_1, f_alice_2, f_bob)

    # Round-robin fair share: alice's second job never runs back-to-back
    # ahead of bob's, so bob is never starved behind alice's backlog.
    assert run_order == ["alice", "bob", "alice"]
    controller.stop()


@pytest.mark.asyncio
async def test_a_failing_job_does_not_strand_the_queue_behind_it() -> None:
    controller = AdmissionController(AdmissionConfig(daily_quota_games=1))
    ran: list[str] = []

    async def failing_task() -> None:
        ran.append("first")
        raise RuntimeError("engine unavailable")

    async def following_task() -> None:
        ran.append("second")

    f_first = controller.submit("alice", failing_task)
    f_second = controller.submit("alice", following_task)

    with pytest.raises(RuntimeError, match="engine unavailable"):
        await f_first
    await f_second

    assert ran == ["first", "second"]
    controller.stop()


def test_daily_quota_env_var_is_validated_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN64_ANALYSIS_DAILY_QUOTA", "many")
    with pytest.raises(ValueError, match="must be an integer"):
        daily_quota_from_env()

    monkeypatch.setenv("SCAN64_ANALYSIS_DAILY_QUOTA", "-1")
    with pytest.raises(ValueError, match="at least 0"):
        daily_quota_from_env()

    monkeypatch.setenv("SCAN64_ANALYSIS_DAILY_QUOTA", "0")
    assert daily_quota_from_env() == 0

    monkeypatch.setenv("SCAN64_ANALYSIS_DAILY_QUOTA", "7")
    assert daily_quota_from_env() == 7


# --- Production wiring: the real API, admission-controlled -----------------


def _wait_for_terminal_status(client: TestClient, job_id: str, headers: dict[str, str]) -> str:
    deadline = time.monotonic() + 5.0
    status = "pending"
    while status not in {"completed", "failed"} and time.monotonic() < deadline:
        response = client.get(f"/v1/analysis-jobs/{job_id}", headers=headers)
        assert response.status_code == 200
        status = response.json()["status"]
        if status not in {"completed", "failed"}:
            time.sleep(0.01)
    return status


def test_analysis_job_submitted_through_the_api_is_admitted_and_completes(
    client: TestClient, db_session: Session
) -> None:
    headers = _register(db_session, "admission-player")
    game_response = client.post(
        "/v1/games", json={"pgn": PGN, "player_id": "admission-player"}, headers=headers
    )
    assert game_response.status_code == 200
    game_id = game_response.json()["id"]

    job_response = client.post(f"/v1/games/{game_id}/analysis-jobs", headers=headers)
    assert job_response.status_code == 200
    job_data = job_response.json()
    # Queued status, never an error, at submission time.
    assert job_data["status"] == "pending"

    status = _wait_for_terminal_status(client, job_data["id"], headers)
    assert status == "completed"


def test_a_player_exceeding_the_daily_quota_is_queued_not_rejected(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quota-exhausted submission still returns 200/pending (never 429 or
    503) and still eventually completes — fair-share queueing, not
    rejection."""
    exhausted_controller = AdmissionController(AdmissionConfig(daily_quota_games=0))
    monkeypatch.setattr(jobs, "admission_controller", exhausted_controller)

    headers = _register(db_session, "quota-player")
    game_response = client.post(
        "/v1/games", json={"pgn": PGN, "player_id": "quota-player"}, headers=headers
    )
    game_id = game_response.json()["id"]

    job_response = client.post(f"/v1/games/{game_id}/analysis-jobs", headers=headers)
    assert job_response.status_code == 200
    job_data = job_response.json()
    assert job_data["status"] == "pending"

    status = _wait_for_terminal_status(client, job_data["id"], headers)
    assert status == "completed"
    exhausted_controller.stop()


def test_ownerless_legacy_game_admits_no_job_at_submission(
    client: TestClient, db_session: Session
) -> None:
    """H-002: admission control resolves the player from
    `Game.owner_player_id` at job submission; an ownerless legacy game is
    rejected loudly, and no `AnalysisJob` — hence no admission submission —
    is ever created for it."""
    headers = _register(db_session, "legacy-player")
    game = Game(pgn=PGN)
    db_session.add(game)
    db_session.commit()

    response = client.post(f"/v1/games/{game.id}/analysis-jobs", headers=headers)
    assert response.status_code == 404
    assert db_session.exec(select(AnalysisJob).where(AnalysisJob.game_id == game.id)).all() == []

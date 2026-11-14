from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.api.models import Player, PlayerOnboarding, PlayerProfile
from scan64.api.onboarding import LEVEL_MAPPING, MAPPING_VERSION
from scan64.content.puzzle_practice_models import PuzzleDailySlot, PuzzleRating
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.profiling.models import SkillState
from scan64.learning.profiling.priors import get_prior_for_rating
from tests.integration.test_puzzle_practice import NOW, clock, seed
from tests.integration.test_puzzle_runs import _action, _auth, _start


def state(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    response = client.get("/v1/players/puzzle-player/onboarding", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def declare(client: TestClient, auth: dict[str, str], level: str) -> dict[str, Any]:
    response = client.post(
        "/v1/players/puzzle-player/onboarding",
        headers=auth,
        json={"action": "declare", "declared_level": level},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("level,suggestion", list(LEVEL_MAPPING.items()))
def test_declaration_immediate_mapping_and_manual_precedence(
    client: TestClient,
    db_session: Session,
    level: str,
    suggestion: int,
) -> None:
    auth = _auth(client)
    measured = SkillState(
        player_id="puzzle-player",
        concept_code="unmapped",
        alpha=7.25,
        beta=4.5,
    )
    db_session.add(measured)
    db_session.commit()
    assert state(client, auth)["status"] == "pending"
    result = declare(client, auth, level)
    assert result["suggested_bot_level"] == result["preferred_bot_level"] == suggestion
    assert result["mapping_version"] == MAPPING_VERSION
    assert result["intro_started"] == result["intro_closed"] == 0
    choice = client.put(
        "/v1/players/puzzle-player/bot-choice",
        json={"bot_level": 8},
        headers=auth,
    )
    assert choice.status_code == 200
    again = declare(client, auth, "not_sure")
    assert again["preferred_bot_level"] == again["manual_bot_level"] == 8
    assert again["intro_started_at"] == result["intro_started_at"]
    play = client.post("/v1/play-sessions", headers=auth, json={"player_id": "puzzle-player"})
    assert play.status_code == 200, play.text
    assert play.json()["opponent_config"]["bot_level"] == "8"
    assert db_session.get(PlayerProfile, "puzzle-player").rating == 1500
    assert get_prior_for_rating(1500) == (3.0, 2.0)
    assert db_session.get(PuzzleRating, "puzzle-player") is None
    db_session.refresh(measured)
    assert (measured.alpha, measured.beta) == (7.25, 4.5)


def test_skip_continue_later_and_existing_opt_in_preserve_identity(
    client: TestClient,
    db_session: Session,
) -> None:
    auth = _auth(client)
    initial = state(client, auth)
    for action in ("continue_later", "skip", "continue_later"):
        result = client.post(
            "/v1/players/puzzle-player/onboarding",
            json={"action": action},
            headers=auth,
        )
        assert result.status_code == 200
        assert result.json()["player_id"] == initial["player_id"]
    assert state(client, auth)["status"] == "skipped"
    assert state(client, auth)["intro_started_at"] is None
    declare(client, auth, "beginner")
    assert state(client, auth)["status"] == "in_progress"
    assert state(client, auth)["skipped_at"] is None
    row = db_session.get(PlayerOnboarding, "puzzle-player")
    db_session.delete(row)
    player = db_session.get(Player, "puzzle-player")
    player.preferences = {"onboarding": {"status": "completed"}, "suggested_bot_level": 8}
    db_session.add(player)
    db_session.commit()
    assert state(client, auth)["status"] == "not_offered"
    assert state(client, auth)["preferred_bot_level"] == 1
    assert declare(client, auth, "club")["suggested_bot_level"] == 5


@pytest.mark.parametrize("choice", [True, "3", 0, 9, 3.5, None])
def test_manual_choice_strict_validation(client: TestClient, choice: Any) -> None:
    auth = _auth(client)
    assert (
        client.put(
            "/v1/players/puzzle-player/bot-choice",
            json={"bot_level": choice},
            headers=auth,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/players",
            json={"id": "invalid-choice", "preferences": {"manual_bot_level": choice}},
        ).status_code
        == 422
    )


def test_onboarding_writes_are_owned_and_server_stamped(client: TestClient) -> None:
    auth = _auth(client)
    for method, path, payload in (
        ("GET", "onboarding", None),
        ("GET", "strength", None),
        ("POST", "onboarding", {"action": "skip"}),
        ("PUT", "bot-choice", {"bot_level": 2}),
    ):
        missing = client.request(method, f"/v1/players/missing/{path}", headers=auth, json=payload)
        alien = client.request(method, f"/v1/players/other/{path}", headers=auth, json=payload)
        assert missing.status_code == alien.status_code == 404
        assert missing.json() == alien.json()
    for payload in (
        {"action": "declare", "declared_level": "expert"},
        {"action": "declare", "declared_level": "club", "suggested_bot_level": 8},
        {"action": "declare", "declared_level": "club", "intro_started_at": "2020-01-01"},
        {"action": "skip", "declared_level": "club"},
    ):
        assert (
            client.post(
                "/v1/players/puzzle-player/onboarding",
                headers=auth,
                json=payload,
            ).status_code
            == 422
        )


def test_intro_excludes_pre_opt_in_and_unstarted_then_continues_next_day(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    for index in range(8):
        seed(db_session, f"intro-{index}", sequence=index)
    old = _start(client, auth)
    old_row = db_session.get(PuzzleRun, old["run_id"])
    old_row.started_at = NOW - timedelta(days=1)
    db_session.add(old_row)
    db_session.commit()
    declared = declare(client, auth, "developing")
    assert declared["intro_started"] == 0
    assert _start(client, auth)["run_id"] == old["run_id"]
    assert _action(client, auth, old, "abandon").status_code == 200
    for outcome in ("solve", "hint"):
        run = _start(client, auth)
        if outcome == "hint":
            run = _action(client, auth, run, "hints", step=1).json()
        assert _action(client, auth, run, "moves", move="e7e5").status_code == 200
    partial = state(client, auth)
    assert partial["intro_started"] == partial["intro_closed"] == 2
    assert partial["status"] == "in_progress"
    clock(monkeypatch, NOW + timedelta(days=1))
    for outcome in ("abandon", "fail", "solve"):
        run = _start(client, auth)
        if outcome == "fail":
            for _ in range(3):
                response = _action(client, auth, run, "moves", move="a7a6")
                assert response.status_code == 200
                run = response.json()
        elif outcome == "solve":
            assert _action(client, auth, run, "moves", move="e7e5").status_code == 200
        else:
            assert _action(client, auth, run, "abandon").status_code == 200
    complete = state(client, auth)
    assert complete["intro_closed"] == complete["intro_started"] == 5
    assert complete["status"] == "completed" and complete["completed_at"]
    assert state(client, auth) == complete
    assert len(db_session.exec(select(PuzzleDailySlot)).all()) > 5
    rating = db_session.get(PuzzleRating, "puzzle-player")
    snapshot = (rating.rating, rating.rated_count)
    declare(client, auth, "advanced")
    db_session.refresh(rating)
    assert (rating.rating, rating.rated_count) == snapshot


def test_short_empty_supply_never_blocks_play(client: TestClient, db_session: Session) -> None:
    auth = _auth(client)
    assert declare(client, auth, "advanced")["preferred_bot_level"] == 6
    assert client.post("/v1/puzzles/runs", headers=auth).json()["status"] == "empty"
    play = client.post("/v1/play-sessions", headers=auth, json={"player_id": "puzzle-player"})
    assert play.status_code == 200 and play.json()["opponent_config"]["bot_level"] == "6"
    assert state(client, auth)["intro_closed"] == 0

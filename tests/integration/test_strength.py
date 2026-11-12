from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.play import BOT_POLICY_VERSION
from scan64.chess.analysis.credit import BEFORE_END, _session_exclusion
from scan64.chess.games.assistance import SessionHintReveal
from scan64.chess.games.eligibility import session_exclusion
from scan64.chess.games.models import Game, PlaySession
from tests.integration.test_puzzle_runs import _auth


def add_game(
    session: Session,
    index: int,
    result: str = "1-0",
    **overrides: Any,
) -> tuple[Game, PlaySession]:
    game = Game(
        id=UUID(int=index + 1),
        pgn="",
        owner_player_id="puzzle-player",
        owner_color="white",
        result=result,
        created_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(minutes=index),
    )
    session.add(game)
    session.flush()
    play = PlaySession(
        player_id="puzzle-player",
        game_id=game.id,
        status="completed",
        assistance_tracking_version=1,
        opponent_config={
            "provider": "stockfish",
            "bot_level": "3",
            "bot_policy_version": BOT_POLICY_VERSION,
            "player_color": "white",
            "seed": str(index),
            "initial_ply": "0",
            "custom_start": "false",
            "unknown": str(index),
            "assistance_generation": str(index),
        },
    )
    for key, value in overrides.items():
        if key == "config":
            play.opponent_config = {**play.opponent_config, **value}
        else:
            setattr(play, key, value)
    session.add(play)
    session.commit()
    return game, play


def strength(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    response = client.get("/v1/players/puzzle-player/strength", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def test_latest_five_previous_five_ignore_random_bookkeeping_and_credit_only_status(
    client: TestClient,
    db_session: Session,
) -> None:
    auth = _auth(client)
    for index in range(10):
        add_game(
            db_session, index, "0-1" if index < 5 else "1-0", credit_analysis_status=BEFORE_END
        )
    cohort = strength(client, auth)["bot_cohorts"][0]
    assert cohort["sample_size"] == 10 and cohort["wins"] == cohort["losses"] == 5
    assert cohort["latest"] == {"sample_size": 5, "wins": 5, "draws": 0, "losses": 0}
    assert cohort["previous"] == {"sample_size": 5, "wins": 0, "draws": 0, "losses": 5}
    assert cohort["trend"] == "improving"
    # A newer resignation is an ordinary terminal result, not filtered for no checkmate.
    add_game(db_session, 10, "0-1")
    newer = strength(client, auth)["bot_cohorts"][0]
    assert newer["sample_size"] == 11 and newer["latest"]["losses"] == 1
    assert strength(client, auth)["puzzle_rating"] == 1200
    assert strength(client, auth)["puzzle_provisional"] is True


def test_level_policy_legacy_and_maia_cohort_boundaries(
    client: TestClient, db_session: Session
) -> None:
    auth = _auth(client)
    add_game(db_session, 0)
    add_game(db_session, 1, "1/2-1/2", config={"bot_level": "4"})
    _, legacy = add_game(db_session, 2)
    legacy.opponent_config = {
        key: value for key, value in legacy.opponent_config.items() if key != "bot_policy_version"
    }
    db_session.add(legacy)
    db_session.commit()
    add_game(db_session, 3, config={"provider": "maia"})
    add_game(db_session, 4, config={"bot_policy_version": "stockfish-error-budget-v2"})
    data = strength(client, auth)
    assert len(data["bot_cohorts"]) == 4
    assert {row["bot_policy_version"] for row in data["bot_cohorts"]} == {
        BOT_POLICY_VERSION,
        "v1 (unstamped)",
        "stockfish-error-budget-v2",
    }
    assert all(row["trend"] == "insufficient_data" for row in data["bot_cohorts"])
    assert next(row for row in data["bot_cohorts"] if row["bot_level"] == 4)["draws"] == 1
    assert "Takebacks and external help are not certified" in data["disclosure"]


@pytest.mark.parametrize(
    "change",
    [
        {"status": "active"},
        {"status": "abandoned"},
        {"assistance_tracking_version": None},
        {"coach_mode": True},
        {"config": {"custom_start": "true"}},
        {"config": {"initial_fen": chess.STARTING_FEN}},
        {"config": {"bot_level": "9"}},
        {"config": {"bot_level": "03"}},
        {"player_id": "other"},
    ],
)
def test_shared_session_exclusions_and_validated_level(
    client: TestClient,
    db_session: Session,
    change: dict[str, Any],
) -> None:
    auth = _auth(client)
    add_game(db_session, 0, **change)
    assert strength(client, auth)["bot_cohorts"] == []


def test_hinted_import_unknown_result_and_custom_game_excluded(
    client: TestClient,
    db_session: Session,
) -> None:
    auth = _auth(client)
    game, play = add_game(db_session, 0)
    db_session.add(
        SessionHintReveal(
            session_id=play.id,
            player_id="puzzle-player",
            prefix_hash="proof",
            canonical_id="position",
            ply=0,
            initial_fen=chess.STARTING_FEN,
            moves=[],
            highest_step=1,
        )
    )
    add_game(db_session, 1, "*")
    add_game(db_session, 2, "unknown")
    custom, _ = add_game(db_session, 3)
    custom.headers = {"FEN": chess.STARTING_FEN}
    db_session.add(custom)
    db_session.add(Game(pgn="", owner_player_id="puzzle-player", result="1-0"))
    db_session.commit()
    assert strength(client, auth)["bot_cohorts"] == []
    play.credit_analysis_status = BEFORE_END
    assert _session_exclusion(game, play) == BEFORE_END
    assert session_exclusion(game, play) is None


def test_server_stamp_overwrites_spoof_and_analysis_default_unchanged(
    client: TestClient,
    db_session: Session,
) -> None:
    auth = _auth(client)
    client.post(
        "/v1/players/puzzle-player/onboarding",
        headers=auth,
        json={"action": "declare", "declared_level": "advanced"},
    )
    for config in ({}, {"bot_policy_version": "client-forgery", "bot_level": "2"}):
        response = client.post(
            "/v1/play-sessions",
            headers=auth,
            json={
                "player_id": "puzzle-player",
                "opponent_config": config,
            },
        )
        assert response.status_code == 200, response.text
        actual = response.json()["opponent_config"]
        assert actual["bot_policy_version"] == BOT_POLICY_VERSION
        assert actual["bot_level"] == config.get("bot_level", "6")
    continued = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "puzzle-player",
            "initial_fen": chess.STARTING_FEN,
        },
    )
    assert continued.status_code == 200
    assert continued.json()["opponent_config"]["bot_level"] == "1"


@pytest.mark.parametrize("recent,trend", [("0-1", "declining"), ("1-0", "steady")])
def test_same_timestamp_windows_use_game_id_not_session_order(
    client: TestClient,
    db_session: Session,
    recent: str,
    trend: str,
) -> None:
    auth = _auth(client)
    for index in reversed(range(10)):
        game, _ = add_game(db_session, index, "1-0" if index < 5 else recent)
        game.created_at = datetime(2026, 11, 1, tzinfo=UTC)
        db_session.add(game)
        db_session.commit()
    cohort = strength(client, auth)["bot_cohorts"][0]
    assert cohort["trend"] == trend
    assert cohort["previous"]["wins"] == 5
    assert cohort["latest"]["losses"] == (5 if trend == "declining" else 0)

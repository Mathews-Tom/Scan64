from __future__ import annotations

from typing import Literal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import Player
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.pgn import build_pgn
from tests.integration.test_data_lifecycle import authorization_header, create_player_token
from tests.integration.test_play_clock_lifecycle import _seed_clock


@pytest.mark.parametrize("reason", [None, "board", "flag", "resignation"])
@pytest.mark.parametrize("version_offset", [-2, -1, 0, 1])
def test_archive_clock_version_matches_exact_timed_history(
    client: TestClient,
    db_session: Session,
    reason: Literal["board", "flag", "resignation"] | None,
    version_offset: int,
) -> None:
    owner = "exact-clock-version-owner"
    headers = authorization_header(create_player_token(client, owner, owner))
    play_id, game_id = _seed_clock(db_session, owner)
    play = db_session.get(PlaySession, play_id)
    game = db_session.get(Game, game_id)
    clock = db_session.get(PlayClock, play_id)
    assert play is not None and game is not None and clock is not None
    if reason is not None:
        play.status = "completed"
        clock.active_color = None
        clock.deadline_at = None
        clock.terminal_reason = reason
        game.result = "0-1"
        if reason == "board":
            game.moves = ["f2f3", "e7e5", "g2g4", "d8h4"]
        elif reason == "flag":
            clock.flagged_color = "white"
            clock.white_remaining_ms = 0
    expected_moves = list(game.moves)
    expected_version = len(expected_moves) + (1 if reason in ("flag", "resignation") else 0)
    clock.version = expected_version
    game.pgn = build_pgn(game)
    db_session.add_all([play, game, clock])
    db_session.commit()
    exported = client.post("/v1/exports", headers=headers, json={"player_id": owner})
    assert exported.status_code == 200
    archive = exported.json()
    archive["play_clocks"][0]["version"] = expected_version + version_offset
    deleted = client.request(
        "DELETE",
        f"/v1/players/{owner}/data",
        headers=headers,
        json={"dry_run": False, "confirmation": f"delete-{owner}"},
    )
    assert deleted.status_code == 200
    restored = client.post("/v1/imports", headers=headers, json=archive)
    db_session.expire_all()
    if version_offset != 0:
        assert restored.status_code == 400, restored.text
        assert db_session.get(Player, owner) is None
        assert db_session.get(Game, game_id) is None
        assert db_session.get(PlaySession, play_id) is None
        assert db_session.get(PlayClock, play_id) is None
    else:
        assert restored.status_code == 200, restored.text
        retained = db_session.get(PlayClock, play_id)
        retained_game = db_session.get(Game, game_id)
        assert retained is not None and retained.version == expected_version
        assert retained_game is not None and retained_game.moves == expected_moves
        canonical = client.get(f"/v1/play-sessions/{play_id}", headers=headers)
        assert canonical.status_code == 200
        assert canonical.json()["clock"]["version"] == expected_version
        assert canonical.json()["status"] == ("active" if reason is None else "completed")


def test_continuation_archive_retains_zero_before_any_timed_move(
    client: TestClient, db_session: Session
) -> None:
    owner = "zero-clock-version-owner"
    headers = authorization_header(create_player_token(client, owner, owner))
    play_id, game_id = _seed_clock(db_session, owner)
    play = db_session.get(PlaySession, play_id)
    clock = db_session.get(PlayClock, play_id)
    assert play is not None and clock is not None
    play.opponent_config = {
        **play.opponent_config,
        "initial_ply": "2",
        "custom_start": "true",
        "assistance_generation": "0",
    }
    clock.version = 0
    db_session.add_all([play, clock])
    db_session.commit()
    exported = client.post("/v1/exports", headers=headers, json={"player_id": owner})
    assert exported.status_code == 200
    archive = exported.json()
    deleted = client.request(
        "DELETE",
        f"/v1/players/{owner}/data",
        headers=headers,
        json={"dry_run": False, "confirmation": f"delete-{owner}"},
    )
    assert deleted.status_code == 200
    restored = client.post("/v1/imports", headers=headers, json=archive)
    assert restored.status_code == 200, restored.text
    db_session.expire_all()
    retained = db_session.get(PlayClock, play_id)
    retained_game = db_session.get(Game, game_id)
    assert retained is not None and retained.version == 0
    assert retained_game is not None and retained_game.moves == ["e2e4", "e7e5"]
    canonical = client.get(f"/v1/play-sessions/{play_id}", headers=headers)
    assert canonical.status_code == 200
    assert canonical.json()["clock"]["version"] == 0
    assert canonical.json()["clock"]["active_color"] == "white"

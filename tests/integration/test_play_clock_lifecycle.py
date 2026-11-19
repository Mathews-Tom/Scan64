from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

import scan64.api.data_lifecycle as lifecycle
import scan64.persistence.database as database
from scan64.api.app import app
from scan64.api.models import Player
from scan64.chess.games.clock_models import PlayClock
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.pgn import build_pgn
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.persistence.database import create_database_engine, get_session
from scan64.providers.stockfish.adapter import StockfishConfig
from tests.integration.test_data_lifecycle import authorization_header, create_player_token


def _seed_clock(session: Session, owner: str, *, completed: bool = False) -> tuple[UUID, UUID]:
    game = Game(
        pgn="",
        moves=["e2e4", "e7e5"],
        owner_player_id=owner,
        owner_color="white",
        white=owner,
        black="Stockfish",
        headers={"TimeControl": "600+5"},
        result="0-1" if completed else "*",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    game.pgn = build_pgn(game)
    session.add(game)
    session.flush()
    play = PlaySession(
        player_id=owner,
        game_id=game.id,
        status="completed" if completed else "active",
        assistance_tracking_version=1,
        opponent_config={
            "provider": "stockfish",
            "bot_level": "2",
            "player_color": "white",
            "bot_policy_version": "stockfish-error-budget-v1",
            "custom_start": "false",
            "initial_ply": "0",
            "assistance_generation": "2",
        },
        clock_config={"initial_seconds": "600", "increment_seconds": "5"},
    )
    session.add(play)
    session.flush()
    session.add(
        PlayClock(
            session_id=play.id,
            initial_seconds=600,
            increment_seconds=5,
            white_remaining_ms=600000,
            black_remaining_ms=590000,
            active_color=None if completed else "white",
            deadline_at=None if completed else datetime.now(UTC) + timedelta(seconds=590),
            version=3 if completed else 2,
            terminal_reason="resignation" if completed else None,
        )
    )
    session.commit()
    return play.id, game.id


def _retained_clock(session: Session, session_id: UUID) -> dict[str, object]:
    clock = session.get(PlayClock, session_id)
    assert clock is not None
    return clock.model_dump(mode="json")


@pytest.mark.parametrize("completed", [False, True])
def test_owned_clock_roundtrip_and_reset_preserve_time_and_other_player(
    client: TestClient,
    db_session: Session,
    completed: bool,
) -> None:
    owner = "clock-lifecycle-owner"
    headers = authorization_header(create_player_token(client, owner, owner))
    other = "clock-lifecycle-other"
    other_headers = authorization_header(create_player_token(client, other, other))
    play_id, game_id = _seed_clock(db_session, owner, completed=completed)
    other_id, _ = _seed_clock(db_session, other)
    db_session.expire_all()
    other_before = _retained_clock(db_session, other_id)
    exported = client.post("/v1/exports", headers=headers, json={"player_id": owner})
    assert exported.status_code == 200
    archive = exported.json()
    assert len(archive["play_clocks"]) == 1
    saved_clock = archive["play_clocks"][0]
    assert saved_clock["session_id"] == str(play_id)
    assert saved_clock["version"] == (3 if completed else 2)
    assert client.get(f"/v1/play-sessions/{play_id}", headers=other_headers).status_code == 404
    reset = client.post(
        f"/v1/players/{owner}/progress/reset",
        headers=headers,
        json={"confirmation": "RESET PROGRESS"},
    )
    assert reset.status_code == 200
    db_session.expire_all()
    assert _retained_clock(db_session, play_id) == saved_clock
    deleted = client.request(
        "DELETE",
        f"/v1/players/{owner}/data",
        headers=headers,
        json={"dry_run": False, "confirmation": f"delete-{owner}"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["affected_rows"]["play_clocks"] == 1
    db_session.expire_all()
    assert db_session.get(PlayClock, play_id) is None
    assert _retained_clock(db_session, other_id) == other_before
    restored = client.post("/v1/imports", headers=headers, json=archive)
    assert restored.status_code == 200, restored.text
    db_session.expire_all()
    assert _retained_clock(db_session, play_id) == saved_clock
    assert db_session.get(Game, game_id) is not None
    resumed = client.get(f"/v1/play-sessions/{play_id}", headers=headers)
    assert resumed.status_code == 200
    projection = resumed.json()["clock"]
    assert projection["version"] == saved_clock["version"]
    if saved_clock["deadline_at"] is None:
        assert projection["deadline_at"] is None
    else:
        assert datetime.fromisoformat(projection["deadline_at"]).replace(
            tzinfo=UTC
        ) == datetime.fromisoformat(saved_clock["deadline_at"]).replace(tzinfo=UTC)
    assert projection["white_remaining_ms"] <= saved_clock["white_remaining_ms"]
    assert _retained_clock(db_session, other_id) == other_before


@pytest.mark.parametrize(
    "tamper",
    [
        "negative-balance",
        "boolean-version",
        "overflow-version",
        "bad-generation",
        "negative-generation",
        "oversized-generation",
        "wrong-side",
        "missing-deadline",
        "numeric-deadline",
        "terminal-reason",
        "foreign-session",
        "duplicate-clock",
        "missing-clock",
        "wrong-control",
        "wrong-game-control",
        "unearned-increment",
        "extended-deadline",
    ],
)
def test_invalid_clock_archive_fails_before_player_or_game_writes(
    client: TestClient,
    db_session: Session,
    tamper: str,
) -> None:
    owner = "invalid-clock-owner"
    headers = authorization_header(create_player_token(client, owner, owner))
    play_id, game_id = _seed_clock(db_session, owner)
    archive = client.post("/v1/exports", headers=headers, json={"player_id": owner}).json()
    assert (
        client.request(
            "DELETE",
            f"/v1/players/{owner}/data",
            headers=headers,
            json={"dry_run": False, "confirmation": f"delete-{owner}"},
        ).status_code
        == 200
    )
    clock = archive["play_clocks"][0]
    if tamper == "negative-balance":
        clock["white_remaining_ms"] = -1
    elif tamper == "boolean-version":
        clock["version"] = True
    elif tamper == "overflow-version":
        clock["version"] = 9223372036854775807
    elif tamper == "bad-generation":
        archive["play_sessions"][0]["opponent_config"]["assistance_generation"] = "bad"
    elif tamper == "negative-generation":
        archive["play_sessions"][0]["opponent_config"]["assistance_generation"] = "-1"
    elif tamper == "oversized-generation":
        archive["play_sessions"][0]["opponent_config"]["assistance_generation"] = "9" * 4300
    elif tamper == "wrong-side":
        clock["active_color"] = "black"
    elif tamper == "missing-deadline":
        clock["deadline_at"] = None
    elif tamper == "numeric-deadline":
        clock["deadline_at"] = datetime.now(UTC).timestamp()
    elif tamper == "terminal-reason":
        clock["terminal_reason"] = "flag"
    elif tamper == "foreign-session":
        clock["session_id"] = str(uuid4())
    elif tamper == "duplicate-clock":
        archive["play_clocks"].append(deepcopy(clock))
    elif tamper == "missing-clock":
        archive["play_clocks"] = []
    elif tamper == "wrong-control":
        archive["play_sessions"][0]["clock_config"]["initial_seconds"] = "601"
    elif tamper == "wrong-game-control":
        archive["games"][0]["headers"]["TimeControl"] = "300+0"
    elif tamper == "unearned-increment":
        clock["white_remaining_ms"] = 9999999
    else:
        clock["deadline_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    restored = client.post("/v1/imports", headers=headers, json=archive)
    assert restored.status_code == 400
    db_session.expire_all()
    assert db_session.get(Player, owner) is None
    assert db_session.get(Game, game_id) is None
    assert db_session.get(PlaySession, play_id) is None
    assert db_session.get(PlayClock, play_id) is None


@pytest.mark.parametrize("offset_hours", [-1, 1])
def test_imported_clock_deadline_preserves_instant_across_timezone_offsets(
    client: TestClient, db_session: Session, offset_hours: int
) -> None:
    owner = "clock-offset-owner"
    headers = authorization_header(create_player_token(client, owner, owner))
    play_id, _ = _seed_clock(db_session, owner)
    archive = client.post("/v1/exports", headers=headers, json={"player_id": owner}).json()
    clock = archive["play_clocks"][0]
    deadline = datetime.fromisoformat(clock["deadline_at"]).replace(tzinfo=UTC)
    clock["deadline_at"] = deadline.astimezone(timezone(timedelta(hours=offset_hours))).isoformat()
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
    assert retained is not None
    assert retained.deadline_at == deadline.replace(tzinfo=None)
    resumed = client.get(f"/v1/play-sessions/{play_id}", headers=headers)
    assert resumed.status_code == 200
    projected = resumed.json()["clock"]
    assert datetime.fromisoformat(projected["deadline_at"]).astimezone(UTC) == deadline
    assert projected["white_remaining_ms"] < 595000


def test_clock_export_is_importable_when_a_real_terminal_writer_commits_mid_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'clock-snapshot.sqlite'}")
    monkeypatch.setattr(database, "engine", engine)

    def isolated_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, isolated_session)
    try:
        with TestClient(app) as isolated_client:
            owner = "clock-snapshot-owner"
            headers = authorization_header(create_player_token(isolated_client, owner, owner))
            with Session(engine) as session:
                play_id, game_id = _seed_clock(session, owner)
            original_projection = lifecycle.project_onboarding_completion
            writer_committed = False

            def project_with_concurrent_terminal(row, closed_at):
                nonlocal writer_committed
                if not writer_committed:
                    writer_committed = True
                    with Session(engine) as writer:
                        service = PlaySessionService(
                            writer, StockfishOpponentProvider(StockfishConfig())
                        )
                        service.resign(play_id)
                return original_projection(row, closed_at)

            with monkeypatch.context() as projection_patch:
                projection_patch.setattr(
                    lifecycle, "project_onboarding_completion", project_with_concurrent_terminal
                )
                exported = isolated_client.post(
                    "/v1/exports", headers=headers, json={"player_id": owner}
                )
            assert exported.status_code == 200
            assert writer_committed
            archive = exported.json()
            assert archive["play_sessions"][0]["status"] == "active"
            assert archive["games"][0]["result"] == "*"
            assert archive["play_clocks"][0]["active_color"] == "white"
            assert archive["play_clocks"][0]["terminal_reason"] is None
            with Session(engine) as current:
                play = current.get(PlaySession, play_id)
                game = current.get(Game, game_id)
                clock = current.get(PlayClock, play_id)
                assert play is not None and play.status == "completed"
                assert game is not None and game.result == "0-1"
                assert clock is not None and clock.terminal_reason == "resignation"
            deleted = isolated_client.request(
                "DELETE",
                f"/v1/players/{owner}/data",
                headers=headers,
                json={"dry_run": False, "confirmation": f"delete-{owner}"},
            )
            assert deleted.status_code == 200
            restored = isolated_client.post("/v1/imports", headers=headers, json=archive)
            assert restored.status_code == 200, restored.text
            with Session(engine) as current:
                play = current.get(PlaySession, play_id)
                clock = current.get(PlayClock, play_id)
                assert play is not None and play.status == "active"
                assert clock is not None and clock.active_color == "white"
                assert clock.version == archive["play_clocks"][0]["version"]
    finally:
        engine.dispose()

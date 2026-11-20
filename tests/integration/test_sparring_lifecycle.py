from __future__ import annotations

import json
from collections.abc import Generator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, select

from scan64.api.app import app
from scan64.api.data_lifecycle import ExportArchive
from scan64.api.models import Player
from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview
from scan64.chess.analysis.review import stage_game_review
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.pgn import build_pgn
from scan64.chess.games.play_session_service import PlaySessionService
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.chess.positions.models import Position
from scan64.learning.habit_models import HabitCredit, PatternAward
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import (
    ReviewSchedule,
    ScheduleReviewObservation,
)
from scan64.learning.sparring import SparringAttempt, source_item_id
from scan64.persistence.database import create_database_engine, get_session
from scan64.providers.stockfish.adapter import StockfishConfig

OWNER = "sparring-lifecycle-owner"
OTHER = "sparring-lifecycle-other"
CREATED = datetime(2026, 1, 1, tzinfo=UTC)
MOVES = ["f2f3", "e7e5", "g2g4", "d8h4"]


@pytest.fixture(autouse=True)
def enable_foreign_keys(db_session: Session) -> None:
    db_session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    assert db_session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def register(client: TestClient, player_id: str) -> dict[str, str]:
    response = client.post("/v1/players", json={"id": player_id, "display_name": player_id})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def seed_graph(
    session: Session,
    player_id: str = OWNER,
    *,
    source_ply: int = 1,
    completed: bool = True,
    with_observation: bool = False,
) -> SparringAttempt:
    original = Game(
        pgn="",
        moves=MOVES,
        owner_player_id=player_id,
        owner_color="black",
        result="0-1",
        created_at=CREATED,
    )
    original.pgn = build_pgn(original)
    session.add(original)
    session.flush()
    board = chess.Board()
    source = Position(
        game_id=original.id, fen=board.fen(), side_to_move="w", canonical_id=board.epd()
    )
    board.push_uci(MOVES[0])
    reached = Position(
        game_id=original.id, fen=board.fen(), side_to_move="b", canonical_id=board.epd()
    )
    session.add_all([source, reached])
    session.flush()
    # Deliberately not winning by today's threshold: restoration proves provenance,
    # not whether the current admission policy would offer another attempt.
    before = EngineAnalysis(position_id=source.id, raw_result=[{"score_cp": 0, "pv": []}])
    after = EngineAnalysis(position_id=reached.id, raw_result=[{"score_cp": 0, "pv": []}])
    session.add_all([before, after])
    session.flush()
    review = GameMoveReview(
        game_id=original.id,
        ply=1,
        source_position_id=source.id,
        reached_position_id=reached.id,
        before_analysis_id=before.id,
        after_analysis_id=after.id,
        move_uci=MOVES[0],
        san="f3",
        mover="white",  # The owner need not be the selected record's mover.
    )
    session.add(review)
    selected = source if source_ply == 0 else reached
    bot_moves = MOVES[source_ply:] if completed else MOVES[:1] if source_ply == 0 else []
    bot_game = Game(
        pgn="",
        moves=bot_moves,
        headers={"FEN": selected.fen},
        owner_player_id=player_id,
        owner_color="black",
        result="0-1" if completed else "*",
        created_at=CREATED,
    )
    bot_game.pgn = build_pgn(bot_game)
    session.add(bot_game)
    session.flush()
    play = PlaySession(
        player_id=player_id,
        game_id=bot_game.id,
        status="completed" if completed else "active",
        assistance_tracking_version=1,
        opponent_config={
            "provider": "stockfish",
            "bot_level": "3",
            "player_color": "black",
            "custom_start": "true",
            "initial_ply": "1" if source_ply == 0 else "0",
            "seed": "12345678",
        },
    )
    session.add(play)
    session.flush()
    attempt = SparringAttempt(
        player_id=player_id,
        source_review_id=review.id,
        source_position_id=selected.id,
        source_analysis_id=before.id if source_ply == 0 else after.id,
        source_ply=source_ply,
        play_session_id=play.id,
        created_at=CREATED,
        completed_at=CREATED + timedelta(minutes=10) if completed else None,
        result="0-1" if completed else None,
        outcome="win" if completed else None,
    )
    session.add(attempt)
    schedule = ReviewSchedule(
        player_id=player_id,
        item_id=source_item_id(review.id, selected.id),
        next_review_at=CREATED + timedelta(days=17),
        last_reviewed_at=None if with_observation else CREATED + timedelta(minutes=10),
        legacy_interval_days=17,
        legacy_ease_factor=2.7,
    )
    session.add(schedule)
    session.flush()
    if with_observation:
        assert attempt.completed_at is not None
        session.add(
            schedule.update(
                success=attempt.outcome == "win",
                current_time=attempt.completed_at,
                hints_used=None,
                elapsed_ms=None,
                source_kind="sparring",
                source_id=str(attempt.id),
            )
        )
    session.add(SkillState(player_id=player_id, concept_code="tactics.mate", alpha=7, beta=3))
    session.commit()
    return attempt


def export(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post("/v1/exports", json={"player_id": OWNER}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def delete_owner(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.request(
        "DELETE",
        f"/v1/players/{OWNER}/data",
        json={"dry_run": False, "confirmation": f"delete-{OWNER}"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["affected_rows"]


def snapshot(session: Session) -> dict[str, list[str]]:
    session.expire_all()
    return {
        str(table): sorted(
            json.dumps(dict(row), sort_keys=True, default=str)
            for row in session.connection().execute(table.select()).mappings()
        )
        for table in SQLModel.metadata.sorted_tables
    }


@pytest.mark.parametrize("ordinary_resigned", [False, True])
@pytest.mark.parametrize("completed", [False, True])
def test_api_reused_bot_game_roundtrips_with_the_conversion_colour_and_no_learning_replay(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    completed: bool,
    ordinary_resigned: bool,
) -> None:
    headers = register(client, OWNER)
    original = Game(
        pgn="1. f3 e5 *",
        moves=["f2f3", "e7e5"],
        owner_player_id=OWNER,
        owner_color="black",
    )
    positions: dict[str, Position] = {}
    analyses: dict[UUID, EngineAnalysis] = {}
    reviews = stage_game_review(
        original,
        [
            EngineAnalysis(raw_result=[{"score_cp": 0, "pv": ["f2f3"]}]),
            EngineAnalysis(raw_result=[{"score_cp": 350, "pv": ["e7e5"]}]),
            EngineAnalysis(raw_result=[{"score_cp": 0, "pv": ["g2g4"]}]),
        ],
        positions,
        analyses,
        set(),
    )
    db_session.add(original)
    db_session.flush()
    db_session.add_all(positions.values())
    db_session.flush()
    db_session.add_all(analyses.values())
    db_session.flush()
    db_session.add_all(reviews)
    db_session.commit()
    launched = client.post(
        "/v1/play-sessions",
        headers=headers,
        json={
            "player_id": OWNER,
            "opponent_config": {"provider": "stockfish", "bot_level": "1"},
            "sparring_source": {"game_id": str(original.id), "ply": 1},
        },
    )
    assert launched.status_code == 200, launched.text
    conversion = launched.json()
    reused = client.post(
        "/v1/play-sessions",
        headers=headers,
        json={
            "player_id": OWNER,
            "game_id": conversion["game_id"],
            "player_color": "white",
        },
    )
    assert reused.status_code == 200, reused.text
    assert reused.json()["opponent_move"] is not None
    assert reused.json()["game_id"] != conversion["game_id"]
    if completed:
        terminal = client.post(f"/v1/play-sessions/{conversion['id']}/resign", headers=headers)
        assert terminal.status_code == 200, terminal.text
        assert terminal.json()["sparring"]["outcome"] == "loss"
    if ordinary_resigned:
        ordinary_terminal = client.post(
            f"/v1/play-sessions/{reused.json()['id']}/resign", headers=headers
        )
        assert ordinary_terminal.status_code == 200, ordinary_terminal.text
        assert ordinary_terminal.json()["status"] == "completed"
    archive = export(client, headers)
    bot = next(game for game in archive["games"] if game["id"] == conversion["game_id"])
    assert bot["owner_color"] == "black"
    assert bot["headers"]["FEN"] == conversion["sparring"]["source_fen"]
    assert bot["moves"] == []
    assert bot["result"] == ("1-0" if completed else "*")
    ordinary_game = next(
        game for game in archive["games"] if game["id"] == reused.json()["game_id"]
    )
    assert ordinary_game["owner_color"] == "white"
    assert ordinary_game["result"] == ("0-1" if ordinary_resigned else "*")
    archived_attempt = archive["sparring_attempts"][0]
    assert archived_attempt["result"] == ("1-0" if completed else None)
    assert archived_attempt["outcome"] == ("loss" if completed else None)
    assert len(archive["play_sessions"]) == 2
    retained = next(play for play in archive["play_sessions"] if play["id"] == conversion["id"])
    assert retained["opponent_config"]["player_color"] == "black"
    assert retained["status"] == ("completed" if completed else "active")
    observations = archive["schedule_review_observations"]
    if completed:
        assert len(observations) == 1
        observation = observations[0]
        assert observation["source_kind"] == "sparring"
        assert observation["source_id"] == archived_attempt["id"]
        assert observation["reviewed_at"] == archived_attempt["completed_at"]
        assert observation["item_id"] == source_item_id(
            UUID(archived_attempt["source_review_id"]), UUID(archived_attempt["source_position_id"])
        )
        assert observation["success"] is False
        assert observation["rating"] == 1
        assert observation["hints_used"] is None and observation["elapsed_ms"] is None
        assert observation["input_provenance"] == "server_recorded"
    else:
        assert observations == []
    assert delete_owner(client, headers)["play_sessions"] == 2

    def no_replay(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Imported conversions must not replay learning")

    monkeypatch.setattr(ReviewSchedule, "update", no_replay)
    monkeypatch.setattr(SkillState, "apply_observation", no_replay)
    imported = client.post("/v1/imports", json=archive, headers=headers)
    assert imported.status_code == 200, imported.text
    restored = client.get(f"/v1/play-sessions/{conversion['id']}", headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["sparring"]["player_color"] == "black"
    assert restored.json()["sparring"]["verification_status"] == "historical"
    assert restored.json()["sparring"]["result"] == ("1-0" if completed else None)
    assert restored.json()["sparring"]["outcome"] == ("loss" if completed else None)
    if not completed:
        moved = client.post(
            f"/v1/play-sessions/{conversion['id']}/moves",
            headers=headers,
            json={"move": "e7e5"},
        )
        assert moved.status_code == 200, moved.text
        db_session.expire_all()
        conversion_game = db_session.get(Game, UUID(conversion["game_id"]))
        assert conversion_game is not None and conversion_game.moves[0] == "e7e5"
        terminal = client.post(f"/v1/play-sessions/{conversion['id']}/resign", headers=headers)
        assert terminal.status_code == 200, terminal.text
        assert terminal.json()["sparring"]["outcome"] == "loss"
        assert terminal.json()["sparring"]["verification_status"] == "historical"
    repeated = client.post(f"/v1/play-sessions/{conversion['id']}/resign", headers=headers)
    assert repeated.status_code == 409, repeated.text
    reexport = export(client, headers)
    assert reexport["review_schedules"] == archive["review_schedules"]
    assert reexport["schedule_review_observations"] == [
        {**row, "verification_status": "historical"} for row in observations
    ]
    assert reexport["positive_move_credits"] == []
    assert db_session.exec(select(HabitCredit)).all() == []
    assert db_session.exec(select(PatternAward)).all() == []


@pytest.mark.parametrize("source_ply", [0, 1])
@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("source_colour", ["black", "white"])
def test_roundtrip_restores_historical_graph_without_learning_replay(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    source_ply: int,
    completed: bool,
    source_colour: str,
) -> None:
    headers = register(client, OWNER)
    attempt = seed_graph(
        db_session, source_ply=source_ply, completed=completed, with_observation=completed
    )
    attempt_id = attempt.id
    source_review = db_session.get(GameMoveReview, attempt.source_review_id)
    assert source_review is not None
    original = db_session.get(Game, source_review.game_id)
    assert original is not None
    original.owner_color = source_colour
    db_session.add(original)
    db_session.commit()
    archive = export(client, headers)
    assert len(archive["sparring_attempts"]) == 1
    assert len(archive["games"]) == 2
    assert len(archive["positions"]) == len(archive["engine_analyses"]) == 2
    assert len(archive["review_moves"]) == len(archive["play_sessions"]) == 1
    assert archive["sparring_attempts"][0]["verification_status"] == "verified"
    deleted = delete_owner(client, headers)
    assert deleted["sparring_attempts"] == 1
    assert deleted["schedule_review_observations"] == int(completed)

    def no_replay(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Historical restoration must not replay learning")

    monkeypatch.setattr(ReviewSchedule, "update", no_replay)
    monkeypatch.setattr(SkillState, "apply_observation", no_replay)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    db_session.expire_all()
    restored = db_session.get(SparringAttempt, attempt_id)
    assert restored is not None
    assert restored.model_dump(mode="json") == {
        **archive["sparring_attempts"][0],
        "verification_status": "historical",
    }
    schedule = db_session.get(
        ReviewSchedule,
        (OWNER, source_item_id(restored.source_review_id, restored.source_position_id)),
    )
    assert schedule is not None
    assert schedule.model_dump(mode="json") == archive["review_schedules"][0]
    assert db_session.exec(select(HabitCredit)).all() == []
    assert db_session.exec(select(PatternAward)).all() == []
    mastery = db_session.get(SkillState, (OWNER, "tactics.mate"))
    assert mastery is not None
    assert mastery.model_dump(mode="json") == archive["skill_states"][0]
    reexport = export(client, headers)
    assert reexport["games"] == archive["games"]
    assert reexport["play_sessions"] == archive["play_sessions"]
    assert reexport["sparring_attempts"][0]["verification_status"] == "historical"
    assert reexport["schedule_review_observations"] == [
        {**row, "verification_status": "historical"}
        for row in archive["schedule_review_observations"]
    ]


@pytest.mark.parametrize(
    ("collection", "field", "value"),
    [
        ("sparring_attempts", "player_id", OTHER),
        ("sparring_attempts", "source_review_id", "missing-id"),
        ("sparring_attempts", "source_position_id", "missing-id"),
        ("sparring_attempts", "source_analysis_id", "missing-id"),
        ("sparring_attempts", "play_session_id", "missing-id"),
        ("sparring_attempts", "source_ply", 0),
        ("sparring_attempts", "source_ply", True),
        ("sparring_attempts", "source_ply", -1),
        ("sparring_attempts", "source_ply", 2),
        ("sparring_attempts", "result", "1-0"),
        ("sparring_attempts", "outcome", "loss"),
        ("sparring_attempts", "completed_at", None),
        ("sparring_attempts", "completed_at", "2025-01-01T00:00:00+00:00"),
        ("sparring_attempts", "created_at", "not-a-time"),
        ("review_moves", "san", "e4"),
        ("review_moves", "move_uci", "e2e4"),
        ("review_moves", "mover", "black"),
        ("play_sessions", "player_id", OTHER),
        ("play_sessions", "status", "active"),
        ("play_sessions", "coach_mode", True),
        ("play_sessions", "clock_config", {"time_remaining_ms": "100"}),
    ],
)
def test_tampered_attempt_graph_is_rejected_before_any_write(
    client: TestClient,
    db_session: Session,
    collection: str,
    field: str,
    value: Any,
) -> None:
    headers = register(client, OWNER)
    seed_graph(db_session)
    archive = export(client, headers)
    delete_owner(client, headers)
    archive[collection][0][field] = str(uuid4()) if value == "missing-id" else value
    before = snapshot(db_session)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert snapshot(db_session) == before
    assert db_session.get(Player, OWNER) is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("seed", ""),
        ("seed", "-1"),
        ("seed", str(2**64)),
        ("custom_start", "false"),
        ("provider", "maia"),
        ("bot_level", "9"),
        ("player_color", "white"),
        ("initial_ply", "1"),
    ],
)
def test_tampered_bot_config_is_rejected_atomically(
    client: TestClient, db_session: Session, key: str, value: str
) -> None:
    headers = register(client, OWNER)
    seed_graph(db_session)
    archive = export(client, headers)
    delete_owner(client, headers)
    archive["play_sessions"][0]["opponent_config"][key] = value
    before = snapshot(db_session)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert snapshot(db_session) == before


@pytest.mark.parametrize(
    "tamper",
    [
        "duplicate-attempt",
        "duplicate-session-link",
        "duplicate-session",
        "missing-review",
        "missing-position",
        "missing-analysis",
        "missing-bot-game",
        "shared-game",
        "source-fen",
        "reached-fen",
        "illegal-original-replay",
        "illegal-bot-replay",
        "null-original-move",
        "null-bot-move",
        "wrong-analysis",
        "bot-fen",
        "bot-owner",
        "bot-color",
        "bot-color-mismatch",
        "bot-result",
    ],
)
def test_broken_closure_is_rejected_atomically(
    client: TestClient, db_session: Session, tamper: str
) -> None:
    headers = register(client, OWNER)
    seed_graph(db_session)
    archive = export(client, headers)
    delete_owner(client, headers)
    attempt = archive["sparring_attempts"][0]
    review = archive["review_moves"][0]
    original = next(game for game in archive["games"] if game["id"] == review["game_id"])
    play = archive["play_sessions"][0]
    bot = next(game for game in archive["games"] if game["id"] == play["game_id"])
    if tamper in {"duplicate-attempt", "duplicate-session-link"}:
        copied = deepcopy(attempt)
        if tamper == "duplicate-session-link":
            copied["id"] = str(uuid4())
        archive["sparring_attempts"].append(copied)
    elif tamper == "duplicate-session":
        archive["play_sessions"].append(deepcopy(play))
    elif tamper.startswith("missing-"):
        collection, identity = {
            "missing-review": ("review_moves", attempt["source_review_id"]),
            "missing-position": ("positions", attempt["source_position_id"]),
            "missing-analysis": ("engine_analyses", attempt["source_analysis_id"]),
            "missing-bot-game": ("games", play["game_id"]),
        }[tamper]
        archive[collection] = [row for row in archive[collection] if row["id"] != identity]
    elif tamper == "shared-game":
        play["game_id"] = original["id"]
    elif tamper in {"source-fen", "reached-fen"}:
        identity = review["source_position_id" if tamper == "source-fen" else "reached_position_id"]
        position = next(row for row in archive["positions"] if row["id"] == identity)
        position["fen"] = chess.Board().fen() if tamper == "reached-fen" else bot["headers"]["FEN"]
    elif tamper == "illegal-original-replay":
        original["moves"][-1] = "d8d1"
    elif tamper == "illegal-bot-replay":
        bot["moves"][-1] = "d8d1"
    elif tamper == "null-original-move":
        original["moves"][-1] = "0000"
    elif tamper == "null-bot-move":
        bot["moves"][-1] = "0000"
    elif tamper == "wrong-analysis":
        attempt["source_analysis_id"] = review["before_analysis_id"]
    elif tamper == "bot-fen":
        bot["headers"]["FEN"] = chess.STARTING_FEN
    elif tamper == "bot-owner":
        bot["owner_player_id"] = OTHER
    elif tamper == "bot-color":
        bot["owner_color"] = None
    elif tamper == "bot-color-mismatch":
        bot["owner_color"] = "white"
    elif tamper == "bot-result":
        bot["result"] = "1-0"
    before = snapshot(db_session)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert snapshot(db_session) == before


@pytest.mark.parametrize(
    "collision", ["game", "position", "analysis", "review", "session", "attempt"]
)
def test_existing_foreign_or_conflicting_identity_is_never_overwritten(
    client: TestClient, db_session: Session, collision: str
) -> None:
    headers = register(client, OWNER)
    register(client, OTHER)
    seed_graph(db_session)
    archive = export(client, headers)
    delete_owner(client, headers)
    other_attempt = seed_graph(db_session, OTHER)
    review = archive["review_moves"][0]
    if collision == "game":
        row = Game.model_validate(archive["games"][0])
        row.owner_player_id = OTHER
    elif collision == "position":
        row = Position.model_validate(archive["positions"][0])
        other_review = db_session.get(GameMoveReview, other_attempt.source_review_id)
        assert other_review is not None
        row.game_id = other_review.game_id
    elif collision == "analysis":
        row = EngineAnalysis.model_validate(archive["engine_analyses"][0])
        row.position_id = other_attempt.source_position_id
    elif collision == "review":
        row = GameMoveReview.model_validate(review)
        # Same natural occurrence with a different UUID must fail preflight too.
        original = Game.model_validate(
            next(game for game in archive["games"] if game["id"] == review["game_id"])
        )
        db_session.add(original)
        db_session.flush()
        db_session.add_all([Position.model_validate(data) for data in archive["positions"]])
        db_session.flush()
        db_session.add_all(
            [EngineAnalysis.model_validate(data) for data in archive["engine_analyses"]]
        )
        db_session.flush()
        row.id = uuid4()
    elif collision == "session":
        row = PlaySession.model_validate(archive["play_sessions"][0])
        row.player_id = OTHER
        row.game_id = None
    else:
        row = SparringAttempt.model_validate(archive["sparring_attempts"][0])
        row.player_id = OTHER
        row.source_review_id = other_attempt.source_review_id
        row.source_position_id = other_attempt.source_position_id
        row.source_analysis_id = other_attempt.source_analysis_id
        # A second owned bot session keeps the collision fixture itself FK-valid.
        other_play = db_session.get(PlaySession, other_attempt.play_session_id)
        assert other_play is not None
        new_play = PlaySession.model_validate({**other_play.model_dump(), "id": uuid4()})
        db_session.add(new_play)
        db_session.flush()
        row.play_session_id = new_play.id
    db_session.add(row)
    db_session.commit()
    before = snapshot(db_session)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert snapshot(db_session) == before


def test_legacy_omission_never_synthesizes_attempts_from_custom_sessions(
    client: TestClient, db_session: Session
) -> None:
    assert ExportArchive().sparring_attempts == []
    headers = register(client, OWNER)
    seed_graph(db_session)
    archive = export(client, headers)
    delete_owner(client, headers)
    del archive["sparring_attempts"]
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    assert db_session.exec(select(SparringAttempt)).all() == []
    assert len(db_session.exec(select(PlaySession)).all()) == 1
    assert export(client, headers)["review_schedules"] == archive["review_schedules"]


@pytest.mark.parametrize("completed", [False, True])
def test_owned_export_delete_counts_and_reset_preserve_source_closure(
    client: TestClient, db_session: Session, completed: bool
) -> None:
    headers = register(client, OWNER)
    other_headers = register(client, OTHER)
    attempt = seed_graph(db_session, completed=completed, with_observation=completed)
    other = seed_graph(db_session, OTHER, with_observation=True)
    other_export = client.post("/v1/exports", json={"player_id": OTHER}, headers=other_headers)
    assert other_export.status_code == 200, other_export.text
    other_archive = other_export.json()
    other_id = other.id
    archive = export(client, headers)
    assert [row["id"] for row in archive["sparring_attempts"]] == [str(attempt.id)]
    assert all(row["owner_player_id"] == OWNER for row in archive["games"])
    assert all(row["player_id"] == OWNER for row in archive["play_sessions"])
    dry_run = client.request(
        "DELETE", f"/v1/players/{OWNER}/data", json={"dry_run": True}, headers=headers
    )
    assert dry_run.status_code == 200, dry_run.text
    counts = dry_run.json()["affected_rows"]
    assert counts["review_schedules"] == 1
    assert counts["schedule_review_observations"] == int(completed)
    assert {
        key: counts[key]
        for key in (
            "sparring_attempts",
            "games",
            "play_sessions",
            "review_moves",
            "positions",
            "engine_analyses",
        )
    } == {
        "sparring_attempts": 1,
        "games": 2,
        "play_sessions": 1,
        "review_moves": 1,
        "positions": 2,
        "engine_analyses": 2,
    }
    reset = client.post(
        f"/v1/players/{OWNER}/progress/reset",
        headers=headers,
        json={"confirmation": "RESET PROGRESS"},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["cleared"]["sparring_attempts"] == 1
    assert reset.json()["cleared"]["review_schedules"] == 1
    assert reset.json()["cleared"]["schedule_review_observations"] == int(completed)
    after = export(client, headers)
    assert (
        after["sparring_attempts"]
        == after["review_schedules"]
        == after["schedule_review_observations"]
        == []
    )
    for collection in ("games", "positions", "engine_analyses", "review_moves", "play_sessions"):
        assert after[collection] == archive[collection]
    assert db_session.get(SparringAttempt, other_id) is not None
    assert (
        db_session.get(
            ReviewSchedule,
            (OTHER, source_item_id(other.source_review_id, other.source_position_id)),
        )
        is not None
    )
    after_reset = client.post("/v1/exports", json={"player_id": OTHER}, headers=other_headers)
    assert after_reset.status_code == 200, after_reset.text
    assert after_reset.json() == other_archive
    deleted = delete_owner(client, headers)
    assert deleted["sparring_attempts"] == 0
    assert deleted["games"] == 2
    assert db_session.get(SparringAttempt, other_id) is not None
    after_deletion = client.post("/v1/exports", json={"player_id": OTHER}, headers=other_headers)
    assert after_deletion.status_code == 200, after_deletion.text
    assert after_deletion.json() == other_archive


@pytest.mark.parametrize(
    ("result", "outcome"),
    [("1-0", "loss"), ("0-1", "win"), ("1/2-1/2", "draw")],
)
def test_historical_declared_results_are_preserved_without_requalification(
    client: TestClient, db_session: Session, result: str, outcome: str
) -> None:
    headers = register(client, OWNER)
    seed_graph(db_session, completed=False)
    archive = export(client, headers)
    delete_owner(client, headers)
    attempt = archive["sparring_attempts"][0]
    attempt.update(
        completed_at=(CREATED + timedelta(minutes=10)).isoformat(),
        result=result,
        outcome=outcome,
        verification_status="externally-claimed-verification",
    )
    play = archive["play_sessions"][0]
    play["status"] = "completed"
    bot = next(game for game in archive["games"] if game["id"] == play["game_id"])
    # A declared result can finish a legal, nonterminal board (e.g. resignation).
    bot["result"] = result
    bot["pgn"] = build_pgn(Game.model_validate(bot))
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 200, response.text
    restored = db_session.exec(select(SparringAttempt)).one()
    assert restored.verification_status == "historical"
    assert (restored.result, restored.outcome) == (result, outcome)
    assert restored.completed_at is not None
    assert restored.completed_at.replace(tzinfo=UTC) == CREATED + timedelta(minutes=10)
    assert export(client, headers)["review_schedules"] == archive["review_schedules"]


def test_restored_active_attempt_remains_historical_when_completed(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = register(client, OWNER)
    seed_graph(db_session, completed=False)
    archive = export(client, headers)
    delete_owner(client, headers)
    imported = client.post("/v1/imports", json=archive, headers=headers)
    assert imported.status_code == 200, imported.text

    def no_schedule_replay(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Imported attempts must not update verified learning")

    monkeypatch.setattr(ReviewSchedule, "update", no_schedule_replay)
    play_id = archive["play_sessions"][0]["id"]
    completed = client.post(f"/v1/play-sessions/{play_id}/resign", headers=headers)
    assert completed.status_code == 200, completed.text
    db_session.expire_all()
    restored = db_session.exec(select(SparringAttempt)).one()
    assert restored.verification_status == "historical"
    assert restored.completed_at is not None
    assert (restored.result, restored.outcome) == ("1-0", "loss")
    assert export(client, headers)["review_schedules"] == archive["review_schedules"]
    repeated = client.post(f"/v1/play-sessions/{play_id}/resign", headers=headers)
    assert repeated.status_code == 409, repeated.text
    assert export(client, headers)["schedule_review_observations"] == []
    assert db_session.exec(select(ScheduleReviewObservation)).all() == []


@pytest.mark.parametrize("tamper", ["status", "result", "completion", "missing-opening"])
def test_active_attempt_terminal_facts_must_agree(
    client: TestClient, db_session: Session, tamper: str
) -> None:
    headers = register(client, OWNER)
    seed_graph(db_session, source_ply=0, completed=False)
    archive = export(client, headers)
    delete_owner(client, headers)
    play = archive["play_sessions"][0]
    bot = next(game for game in archive["games"] if game["id"] == play["game_id"])
    if tamper == "status":
        play["status"] = "completed"
    elif tamper == "result":
        bot["result"] = "0-1"
    elif tamper == "completion":
        archive["sparring_attempts"][0]["completed_at"] = CREATED.isoformat()
    else:
        bot["moves"] = []
    before = snapshot(db_session)
    response = client.post("/v1/imports", json=archive, headers=headers)
    assert response.status_code == 400, response.text
    assert snapshot(db_session) == before


def test_export_uses_one_wal_snapshot_when_a_conversion_completes_between_reads(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'snapshot.sqlite'}")
    SQLModel.metadata.create_all(engine)

    def isolated_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, isolated_session)
    headers = register(client, OWNER)
    with Session(engine) as session:
        attempt = seed_graph(session, completed=False)
        attempt_id = attempt.id
        play_id = attempt.play_session_id
        play = session.get(PlaySession, play_id)
        assert play is not None
        bot_id = play.game_id
    completed = False

    def complete_during_export(_connection: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        nonlocal completed
        if completed or "FROM sparringattempt" not in statement:
            return
        completed = True
        with Session(engine) as writer:
            PlaySessionService(writer, StockfishOpponentProvider(StockfishConfig())).resign(play_id)

    event.listen(engine, "after_cursor_execute", complete_during_export)
    try:
        archive = export(client, headers)
        assert completed
        assert archive["sparring_attempts"][0]["completed_at"] is None
        archived_bot = next(game for game in archive["games"] if game["id"] == str(bot_id))
        assert archive["schedule_review_observations"] == []
        assert archived_bot["result"] == "*"
        with Session(engine) as session:
            live = session.get(SparringAttempt, attempt_id)
            assert live is not None
            assert live.outcome == "loss" and live.result == "1-0"
            observations = session.exec(select(ScheduleReviewObservation)).all()
            assert len(observations) == 1
            assert observations[0].source_id == str(attempt_id)
            assert observations[0].success is False
        delete_owner(client, headers)
        restored = client.post("/v1/imports", json=archive, headers=headers)
        assert restored.status_code == 200, restored.text
    finally:
        event.remove(engine, "after_cursor_execute", complete_during_export)
        engine.dispose()

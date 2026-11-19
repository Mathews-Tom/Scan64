from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview
from scan64.chess.analysis.review import stage_game_review
from scan64.chess.games.eligibility import session_exclusion
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.play_session_service import PlaySessionNotActive, PlaySessionService
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.chess.positions.models import Position
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule, _as_utc
from scan64.learning.sparring import (
    SparringAttempt,
    read_sparring,
    source_item_id,
    winning_position,
)
from scan64.providers.stockfish.adapter import StockfishConfig


def _register(client: TestClient, player_id: str = "sparring-owner") -> dict[str, str]:
    response = client.post("/v1/players", json={"id": player_id})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _source(
    session: Session, player_id: str = "sparring-owner", *, after_first_move_cp: int = 0
) -> tuple[Game, GameMoveReview]:
    game = Game(
        pgn="1. f3 e5 2. g4 Nc6 *",
        moves=["f2f3", "e7e5", "g2g4", "b8c6"],
        owner_player_id=player_id,
        owner_color="black",
    )
    evaluations = [
        EngineAnalysis(raw_result=[{"score_cp": 0, "pv": ["f2f3"]}]),
        EngineAnalysis(raw_result=[{"score_cp": after_first_move_cp, "pv": ["e7e5"]}]),
        EngineAnalysis(raw_result=[{"score_cp": 0, "pv": ["g2g4"]}]),
        EngineAnalysis(raw_result=[{"score_mate": 1, "pv": ["d8h4"]}]),
        EngineAnalysis(raw_result=[{"score_cp": -350, "pv": ["a2a3"]}]),
    ]
    positions: dict[str, Position] = {}
    analyses: dict[UUID, EngineAnalysis] = {}
    rows = stage_game_review(game, evaluations, positions, analyses, set())
    session.add(game)
    session.flush()
    session.add_all(positions.values())
    session.flush()
    session.add_all(analyses.values())
    session.flush()
    session.add_all(rows)
    session.commit()
    return game, rows[2]


def _start(client: TestClient, auth: dict[str, str], game_id: UUID, ply: int = 3) -> dict[str, Any]:
    response = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "sparring-owner",
            "opponent_config": {"provider": "stockfish", "bot_level": "1"},
            "sparring_source": {"game_id": str(game_id), "ply": ply},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _service(session: Session) -> PlaySessionService:
    return PlaySessionService(session, StockfishOpponentProvider(StockfishConfig()))


@pytest.mark.parametrize(
    ("owner", "score", "expected"),
    [
        ("white", {"score_cp": 299}, False),
        ("white", {"score_cp": 300}, True),
        ("black", {"score_cp": 300}, False),
        ("black", {"score_cp": -300}, True),
        ("white", {"score_mate": 1}, True),
        ("black", {"score_mate": 1}, False),
        ("black", {"score_mate": -1}, True),
        ("white", {"score_mate": 0}, False),
        ("white", {"score_cp": True}, False),
        ("white", {"score_cp": float("nan")}, False),
        ("white", {"score_cp": float("inf")}, False),
        ("white", {}, False),
        ("white", {"score_cp": 350, "pv": ["0000"]}, False),
        ("white", {"score_cp": 350, "pv": ["e2e5"]}, False),
    ],
)
def test_winning_eligibility_uses_owner_score_and_legal_pv(
    owner: Literal["white", "black"], score: dict[str, Any], expected: bool
) -> None:
    board = chess.Board()
    game = Game(pgn="*", owner_player_id="owner", owner_color=owner)
    position = Position(game_id=game.id, fen=board.fen(), side_to_move="w", canonical_id="starting")
    analysis = EngineAnalysis(position_id=position.id, raw_result=[{"pv": ["e2e4"], **score}])
    assert winning_position(game, board, position, analysis) is expected


def test_automatic_draw_with_legal_moves_cannot_become_a_sparring_source() -> None:
    board = chess.Board("4k3/8/8/8/8/8/8/R3K3 w - - 150 80")
    game = Game(pgn="*", owner_player_id="owner", owner_color="white")
    position = Position(game_id=game.id, fen=board.fen(), side_to_move="w", canonical_id="draw")
    analysis = EngineAnalysis(
        position_id=position.id, raw_result=[{"score_cp": 900, "pv": ["a1a8"]}]
    )
    assert not winning_position(game, board, position, analysis)


def test_selected_opponent_move_launches_the_owner_winning_reached_position(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    original, review = _source(db_session)
    result = _start(client, auth, original.id)
    source = db_session.get(Position, review.reached_position_id)
    assert source is not None
    assert result["player_color"] == "black"
    assert result["sparring"]["source_fen"] == source.fen
    assert result["sparring"]["source_review_id"] == str(review.id)
    assert result["sparring"]["source_analysis_id"] == str(review.after_analysis_id)
    assert result["sparring"]["source_ply"] == 3
    assert result["opponent_config"]["custom_start"] == "true"
    game = db_session.get(Game, UUID(result["game_id"]))
    assert game is not None and game.id != original.id
    assert game.headers["FEN"] == source.fen and game.moves == []
    assert original.moves == ["f2f3", "e7e5", "g2g4", "b8c6"]
    projection = client.get(f"/v1/games/{original.id}/review", headers=auth)
    assert projection.status_code == 200
    selected = projection.json()["moves"][2]
    assert selected["is_owner_move"] is False and selected["sparring_eligible"] is True
    assert selected["sparring"]["play_session_id"] == result["id"]


def test_attempt_keeps_its_colour_when_ordinary_play_changes_the_source_colour(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    original, _ = _source(db_session)
    conversion = _start(client, auth, original.id)
    ordinary = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "sparring-owner",
            "game_id": str(original.id),
            "player_color": "white",
        },
    )
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()["player_color"] == "white"
    resumed = client.get(f"/v1/play-sessions/{conversion['id']}", headers=auth)
    assert resumed.status_code == 200
    assert resumed.json()["sparring"]["player_color"] == "black"
    review = client.get(f"/v1/games/{original.id}/review", headers=auth)
    assert review.status_code == 200
    assert review.json()["moves"][2]["sparring"]["player_color"] == "black"


def test_completion_keeps_retained_colour_after_ordinary_reuse_of_the_bot_game(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    original, _ = _source(db_session, after_first_move_cp=350)
    conversion = _start(client, auth, original.id, ply=1)
    ordinary = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "sparring-owner",
            "game_id": conversion["game_id"],
            "player_color": "white",
        },
    )
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()["player_color"] == "white"
    assert ordinary.json()["opponent_move"] is not None
    bot_game = db_session.get(Game, UUID(conversion["game_id"]))
    assert bot_game is not None and bot_game.owner_color == "white"
    completed = client.post(f"/v1/play-sessions/{conversion['id']}/resign", headers=auth)
    assert completed.status_code == 200, completed.text
    attempt = completed.json()["sparring"]
    assert (attempt["player_color"], attempt["result"], attempt["outcome"]) == (
        "black",
        "1-0",
        "loss",
    )
    schedule = db_session.get(
        ReviewSchedule,
        (
            "sparring-owner",
            source_item_id(UUID(attempt["source_review_id"]), UUID(attempt["source_position_id"])),
        ),
    )
    assert schedule is not None
    assert schedule.interval_days == 1.0 and schedule.ease_factor == pytest.approx(2.3)
    assert schedule.last_reviewed_at is not None
    before = schedule.model_dump()
    repeated = client.post(f"/v1/play-sessions/{conversion['id']}/resign", headers=auth)
    assert repeated.status_code == 409
    db_session.refresh(schedule)
    assert schedule.model_dump() == before


@pytest.mark.parametrize("ply", [True, 1.5, "3", -1, 20])
def test_invalid_source_ordinal_is_rejected(
    client: TestClient, db_session: Session, ply: object
) -> None:
    auth = _register(client)
    game, _ = _source(db_session)
    response = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "sparring-owner",
            "sparring_source": {"game_id": str(game.id), "ply": ply},
        },
    )
    assert response.status_code == 422
    assert db_session.exec(select(SparringAttempt)).all() == []


def test_foreign_and_nonwinning_review_sources_cannot_launch(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    foreign, _ = _source(db_session, "foreign-owner")
    own, _ = _source(db_session)
    for game, ply, expected in [(foreign, 3, 404), (own, 0, 422), (own, 2, 422)]:
        response = client.post(
            "/v1/play-sessions",
            headers=auth,
            json={
                "player_id": "sparring-owner",
                "sparring_source": {"game_id": str(game.id), "ply": ply},
            },
        )
        assert response.status_code == expected
    assert db_session.exec(select(SparringAttempt)).all() == []


@pytest.mark.parametrize("tamper", ["source_fen", "analysis_position", "review_move", "review_san"])
def test_stored_provenance_mismatch_cannot_launch(
    client: TestClient, db_session: Session, tamper: str
) -> None:
    auth = _register(client)
    game, review = _source(db_session)
    if tamper == "source_fen":
        reached = db_session.get(Position, review.reached_position_id)
        assert reached is not None
        reached.fen = chess.STARTING_FEN
        db_session.add(reached)
    elif tamper == "analysis_position":
        analysis = db_session.get(EngineAnalysis, review.after_analysis_id)
        assert analysis is not None
        analysis.position_id = review.source_position_id
        db_session.add(analysis)
    elif tamper == "review_move":
        review.move_uci = "g2g3"
        db_session.add(review)
    else:
        review.san = "g3"
        db_session.add(review)
    db_session.commit()
    response = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "sparring-owner",
            "sparring_source": {"game_id": str(game.id), "ply": 3},
        },
    )
    assert response.status_code == 422
    assert db_session.exec(select(SparringAttempt)).all() == []


@pytest.mark.asyncio
async def test_terminal_win_persists_outcome_schedule_and_retry_without_strength_credit(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _register(client)
    game, review = _source(db_session)
    seeds = iter([7, 8])
    monkeypatch.setattr("scan64.api.play.secrets.randbits", lambda bits: next(seeds))
    started = _start(client, auth, game.id)
    session_id = UUID(started["id"])
    service = _service(db_session)
    await service.make_move(session_id, "d8h4")
    finished = client.get(f"/v1/play-sessions/{session_id}", headers=auth).json()
    assert finished["status"] == "completed"
    assert finished["sparring"]["result"] == "0-1"
    assert finished["sparring"]["outcome"] == "win"
    assert finished["sparring"]["verification_status"] == "verified"
    assert finished["sparring"]["next_review_at"] is not None
    play = db_session.get(PlaySession, session_id)
    assert play is not None
    bot_game = db_session.get(Game, play.game_id)
    assert bot_game is not None
    assert session_exclusion(bot_game, play) == "started from a studied position"
    item_id = source_item_id(review.id, review.reached_position_id)
    schedule = db_session.get(ReviewSchedule, ("sparring-owner", item_id))
    assert schedule is not None and schedule.last_reviewed_at is not None
    assert _as_utc(schedule.next_review_at) > _as_utc(schedule.last_reviewed_at)
    with Session(db_session.get_bind()) as restarted:
        persisted = restarted.get(PlaySession, session_id)
        assert persisted is not None
        stored = read_sparring(restarted, persisted)
        assert stored is not None and stored.outcome == "win"
        assert stored.next_review_at == _as_utc(schedule.next_review_at)
    before = schedule.next_review_at
    with pytest.raises(PlaySessionNotActive):
        service.resign(session_id)
    db_session.refresh(schedule)
    assert schedule.next_review_at == before
    retry = _start(client, auth, game.id)
    assert retry["id"] != started["id"]
    assert (started["sparring"]["seed"], retry["sparring"]["seed"]) == ("7", "8")
    assert retry["sparring"]["source_fen"] == started["sparring"]["source_fen"]
    assert retry["sparring"]["next_review_at"] == finished["sparring"]["next_review_at"]


def test_resignation_is_an_owner_loss_and_reschedules_once(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    game, review = _source(db_session)
    started = _start(client, auth, game.id)
    _service(db_session).resign(UUID(started["id"]))
    outcome = client.get(f"/v1/play-sessions/{started['id']}", headers=auth).json()["sparring"]
    assert outcome["result"] == "1-0" and outcome["outcome"] == "loss"
    schedule = db_session.get(
        ReviewSchedule, ("sparring-owner", source_item_id(review.id, review.reached_position_id))
    )
    assert schedule is not None and schedule.interval_days == 1.0


@pytest.mark.asyncio
async def test_reset_session_completion_never_resurrects_deleted_learning(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    game, review = _source(db_session)
    started = _start(client, auth, game.id)
    reset = client.post(
        "/v1/players/sparring-owner/progress/reset",
        headers=auth,
        json={"confirmation": "RESET PROGRESS"},
    )
    assert reset.status_code == 200, reset.text
    await _service(db_session).make_move(UUID(started["id"]), "d8h4")
    assert db_session.exec(select(SparringAttempt)).all() == []
    assert (
        db_session.get(
            ReviewSchedule,
            ("sparring-owner", source_item_id(review.id, review.reached_position_id)),
        )
        is None
    )
    persisted = client.get(f"/v1/play-sessions/{started['id']}", headers=auth).json()
    assert persisted["status"] == "completed" and persisted["sparring"] is None


@pytest.mark.asyncio
async def test_continuing_historical_session_cannot_promote_or_reschedule_it(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    game, review = _source(db_session)
    started = _start(client, auth, game.id)
    attempt = db_session.get(SparringAttempt, UUID(started["sparring"]["id"]))
    assert attempt is not None
    attempt.verification_status = "historical"
    item_id = source_item_id(review.id, review.reached_position_id)
    due = datetime(2030, 1, 1, tzinfo=UTC)
    db_session.add(ReviewSchedule(player_id="sparring-owner", item_id=item_id, next_review_at=due))
    db_session.add(attempt)
    db_session.commit()
    await _service(db_session).make_move(UUID(started["id"]), "d8h4")
    result = client.get(f"/v1/play-sessions/{started['id']}", headers=auth).json()["sparring"]
    assert result["outcome"] == "win" and result["verification_status"] == "historical"
    schedule = db_session.get(ReviewSchedule, ("sparring-owner", item_id))
    assert schedule is not None and _as_utc(schedule.next_review_at) == due
    assert schedule.last_reviewed_at is None


def test_null_move_history_cannot_supply_an_otherwise_matching_review_source(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    game = Game(
        pgn="1. -- *", moves=["0000"], owner_player_id="sparring-owner", owner_color="black"
    )
    positions: dict[str, Position] = {}
    analyses: dict[UUID, EngineAnalysis] = {}
    rows = stage_game_review(
        game,
        [
            EngineAnalysis(raw_result=[{"score_cp": 0, "pv": ["e2e4"]}]),
            EngineAnalysis(raw_result=[{"score_cp": 350, "pv": ["e7e5"]}]),
        ],
        positions,
        analyses,
        set(),
    )
    db_session.add(game)
    db_session.flush()
    db_session.add_all(positions.values())
    db_session.flush()
    db_session.add_all(analyses.values())
    db_session.flush()
    db_session.add_all(rows)
    db_session.commit()
    projection = client.get(f"/v1/games/{game.id}/review", headers=auth)
    assert projection.status_code == 200
    assert projection.json()["initial_sparring_eligible"] is False
    assert projection.json()["moves"][0]["sparring_eligible"] is False
    response = client.post(
        "/v1/play-sessions",
        headers=auth,
        json={
            "player_id": "sparring-owner",
            "sparring_source": {"game_id": str(game.id), "ply": 1},
        },
    )
    assert response.status_code == 422
    assert db_session.exec(select(SparringAttempt)).all() == []


def test_opponent_first_source_keeps_owner_colour_and_cannot_undo_the_opening_reply(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    original, _ = _source(db_session)
    result = _start(client, auth, original.id, ply=4)
    assert result["player_color"] == "black"
    assert result["sparring"]["source_fen"].split()[1] == "w"
    assert result["opponent_move"] is not None
    board = chess.Board(result["sparring"]["source_fen"])
    reply = board.parse_uci(result["opponent_move"])
    assert reply in board.legal_moves
    board.push(reply)
    assert board.turn == chess.BLACK
    bot_game = db_session.get(Game, UUID(result["game_id"]))
    assert bot_game is not None and bot_game.id != original.id
    assert bot_game.moves == [reply.uci()]
    assert result["opponent_config"]["initial_ply"] == "1"
    rejected = client.post(f"/v1/play-sessions/{result['id']}/takeback", headers=auth)
    assert rejected.status_code == 409
    db_session.refresh(bot_game)
    assert bot_game.moves == [reply.uci()]

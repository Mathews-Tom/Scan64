from __future__ import annotations

import asyncio
import io
import sqlite3
from datetime import UTC, datetime, timedelta
from threading import get_ident
from typing import Any, Literal
from uuid import UUID, uuid4
from zipfile import ZipFile

import chess
import chess.pgn
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from scan64.api import reports
from scan64.api.models import Player
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedLessonOpportunity,
)
from scan64.chess.games.ingestion import ingest_fen
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import ContentAttempt, ContentItem, LessonAttempt, StudySession
from scan64.content.puzzle_practice import utc_now
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.evidence.composer import compose_candidate_evidence
from scan64.learning.evidence.models import Evidence
from scan64.learning.pattern_recurrence import PatternRecurrenceReport
from scan64.learning.progress import RESET_AT_KEY
from scan64.providers.stockfish.adapter import StockfishAdapter
from scan64.providers.stockfish.pool import PoolBoundAdapter

CODE = "threat_processing.missed_capture"
TRAINING = datetime(2026, 10, 10, 12, tzinfo=UTC)
FEN = "4k3/8/8/8/8/8/q7/R3K1N1 w - - 0 1"


def seed_online_game(
    session: Session,
    owner: str,
    when: datetime,
    *,
    candidates: int = 1,
    owner_color: Literal["white", "black"] = "white",
    moves: int = 20,
    quiet_candidates: bool = False,
    game_id: UUID | None = None,
) -> Game:
    """Synthetic legal PGN + saved analyses, not recorded engine/user outcomes."""
    node = chess.pgn.Game()
    node.setup(chess.Board(FEN))
    node.headers.update(
        {
            "Site": f"https://www.chess.com/game/live/{uuid4().int}",
            "White": "FixtureWhite",
            "Black": "FixtureBlack",
            "Result": "1/2-1/2",
            "UTCDate": when.strftime("%Y.%m.%d"),
            "UTCTime": when.strftime("%H:%M:%S"),
        }
    )
    tail = node.end()
    ucis = ["g1f3", "e8f8", "f3g1", "f8e8"] * ((moves + 1) // 2)
    ucis = ucis[: moves * 2]
    for uci in ucis:
        tail = tail.add_variation(chess.Move.from_uci(uci))
    game = Game(
        id=game_id or uuid4(),
        pgn=str(node),
        headers=dict(node.headers),
        moves=ucis,
        white="FixtureWhite",
        black="FixtureBlack",
        result="1/2-1/2",
        owner_color=owner_color,
        owner_player_id=owner,
        # Every import is after training, including historically played games.
        created_at=TRAINING + timedelta(days=40),
    )
    session.add(game)
    session.flush()
    board = chess.Board(FEN)
    positions = []
    analyses = []
    for index in range(len(ucis) + 1):
        position = ingest_fen(board.fen(), game.id)
        session.add(position)
        positions.append(position)
        best = "a1a2" if board.turn else (ucis[index] if index < len(ucis) else "e8f8")
        if quiet_candidates and index < len(ucis):
            best = ucis[index]
        analysis = EngineAnalysis(
            position_id=position.id,
            config={"nodes": 10000, "multipv": 1},
            raw_result=[
                {"pv": [best], "score_cp": 300 if index % 2 == 0 and index // 2 < candidates else 0}
            ],
        )
        session.add(analysis)
        analyses.append(analysis)
        if index < len(ucis):
            board.push_uci(ucis[index])
    board = chess.Board(FEN)
    history = []
    for index, uci in enumerate(ucis):
        before = board.copy()
        move = chess.Move.from_uci(uci)
        san = board.san(move)
        history.append(san)
        mover = "white" if board.turn else "black"
        board.push(move)
        session.add(
            GameMoveReview(
                game_id=game.id,
                ply=index + 1,
                source_position_id=positions[index].id,
                reached_position_id=positions[index + 1].id,
                before_analysis_id=analyses[index].id,
                after_analysis_id=analyses[index + 1].id,
                move_uci=uci,
                san=san,
                mover=mover,
            )
        )
        if mover == "white" and index // 2 < candidates:
            focused = EngineAnalysis(
                position_id=positions[index + 1].id,
                config={"nodes": 1000000, "multipv": 4},
                raw_result=[{"pv": [ucis[index + 1]], "score_cp": 0}],
            )
            session.add(focused)
            session.add_all(
                compose_candidate_evidence(
                    before_board=before,
                    after_board=board.copy(),
                    history_san=list(history),
                    initial_fen=FEN,
                    position_id=str(positions[index + 1].id),
                    fast_analysis=analyses[index],
                    reply_analysis=analyses[index + 1],
                    focused_analysis=focused,
                    mate_source_analysis=None,
                    played_move=san,
                    swing_cp=300,
                )
            )
    session.add(AnalysisJob(game_id=game.id, status="completed", completed_at=utc_now()))
    session.commit()
    return game


def seed_puzzle(session: Session) -> ContentItem:
    board = chess.Board()
    board.push_uci("e2e4")
    item = ContentItem(
        id=f"lichess:recurrence-{uuid4()}",
        domain="puzzles",
        version="0.2.0",
        provenance="Synthetic recurrence fixture",
        licence="CC0-1.0",
        skill_mapping={CODE: 1.0},
        payload={
            "puzzle_id": "recurrence",
            "fen": chess.STARTING_FEN,
            "moves": ["e2e4", "e7e5", "g1f3", "b8c6"],
            "setup": "e2e4",
            "import_sequence": 1,
            "themes": ["hangingPiece"],
            "lesson": {
                "schema_version": "0.2.0",
                "lesson_id": "fixture-puzzle",
                "source": {"kind": "external", "fen": board.fen()},
                "objective": {"type": "puzzle", "instruction": "Continue."},
                "interaction": {
                    "input": "move",
                    "maximum_attempts": 3,
                    "solution": ["e7e5", "g1f3", "b8c6"],
                },
                "verification": {"status": "source_verified"},
            },
        },
    )
    session.add(item)
    session.commit()
    return item


def complete_failed_puzzle(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    response = client.post("/v1/puzzles/runs", headers=auth)
    assert response.status_code == 200, response.text
    run = response.json()["run"]
    for _ in range(3):
        response = client.post(
            f"/v1/puzzles/runs/{run['run_id']}/moves",
            headers=auth,
            json={
                "move": "a7a6",
                "expected_cursor": run["cursor"],
                "expected_revision": run["revision"],
            },
        )
        assert response.status_code == 200, response.text
        run = response.json()
    assert run["state"] == "failed"
    return run


def auth_player(client: TestClient, owner: str = "recurrence-owner") -> dict[str, str]:
    result = client.post("/v1/players", json={"id": owner})
    assert result.status_code == 200
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


def read_pattern(
    client: TestClient, auth: dict[str, str], owner: str = "recurrence-owner"
) -> dict[str, Any]:
    response = client.get(f"/v1/players/{owner}/progress/pattern-recurrence", headers=auth)
    assert response.status_code == 200, response.text
    return next(row for row in response.json()["patterns"] if row["code"] == CODE)


def train(
    client: TestClient, session: Session, auth: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    seed_puzzle(session)
    monkeypatch.setattr("scan64.content.puzzle_practice.utc_now", lambda: TRAINING)
    return complete_failed_puzzle(client, auth)


def test_uncapped_before_after_actual_play_time_and_read_only(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    for day in (-3, -2, -1, 1, 2, 3):
        seed_online_game(
            db_session,
            "recurrence-owner",
            TRAINING + timedelta(days=day),
            candidates=7 if day < 0 else 1,
        )
    # Duplicate candidate evidence cannot multiply an occurrence.
    original = db_session.exec(
        select(Evidence).where(Evidence.kind == "missed_opportunity")
    ).first()
    assert original is not None
    duplicate = Evidence.model_validate({**original.model_dump(), "evidence_id": str(uuid4())})
    db_session.add(duplicate)
    db_session.commit()
    writes = []

    def no_write(
        _conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _many: bool
    ) -> None:
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    async def no_engine(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("report must not search")

    monkeypatch.setattr(StockfishAdapter, "analyze_position", no_engine)
    monkeypatch.setattr(PoolBoundAdapter, "analyze_position", no_engine)
    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", no_write)
    try:
        row = read_pattern(client, auth)
        assert read_pattern(client, auth) == row
    finally:
        event.remove(engine, "before_cursor_execute", no_write)
    assert writes == []
    assert row["status"] == "comparison"
    assert row["before"]["games"] == row["after"]["games"] == 3
    assert row["before"]["owner_moves"] == row["after"]["owner_moves"] == 60
    assert row["before"]["occurrences"] == 21
    assert row["after"]["occurrences"] == 3
    assert row["before"]["per_100_moves"] == 35
    assert row["after"]["per_100_moves"] == 5
    assert row["before"]["wilson_95_per_100"] == pytest.approx([24.167774, 47.637381], abs=0.000001)


@pytest.mark.parametrize(("swing", "expected_games"), [(149, 3), (150, 3), (151, 2)])
def test_fast_pass_threshold_boundary_keeps_non_candidates_eligible(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    swing: int,
    expected_games: int,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    games = [
        seed_online_game(
            db_session, "recurrence-owner", TRAINING + timedelta(days=day), candidates=0
        )
        for day in (-3, -2, -1, 1, 2, 3)
    ]
    review = db_session.exec(
        select(GameMoveReview).where(GameMoveReview.game_id == games[0].id, GameMoveReview.ply == 1)
    ).one()
    for analysis_id, score in (
        (review.before_analysis_id, 100),
        (review.after_analysis_id, swing - 100),
    ):
        analysis = db_session.get(EngineAnalysis, analysis_id)
        assert analysis is not None and analysis.raw_result
        analysis.raw_result = [{**analysis.raw_result[0], "score_cp": score}]
        db_session.add(analysis)
    db_session.commit()
    row = read_pattern(client, auth)
    assert row["before"]["games"] == expected_games
    assert row["before"]["owner_moves"] == 20 * expected_games
    assert row["before"]["occurrences"] == 0
    assert row["after"]["games"] == 3
    assert row["status"] == ("comparison" if expected_games == 3 else "not_enough_games")
    assert row["before"]["excluded"] == (
        {} if expected_games == 3 else {"unsupported_candidate_evidence": 1}
    )
    assert row["before"]["per_100_moves"] == (0 if expected_games == 3 else None)


@pytest.mark.parametrize("missing_kind", ["unsupported", "missing", "wrong_focused"])
def test_unsupported_missing_and_foreign_joins_never_supply_zero_denominators(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    missing_kind: str,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    games = [
        seed_online_game(db_session, "recurrence-owner", TRAINING + timedelta(days=day))
        for day in (-3, -2, -1, 1, 2, 3)
    ]
    victim = games[0]
    positions = {
        str(row.reached_position_id)
        for row in db_session.exec(
            select(GameMoveReview).where(GameMoveReview.game_id == victim.id)
        ).all()
    }
    for item in db_session.exec(select(Evidence)).all():
        if item.position_id in positions:
            if missing_kind == "missing":
                if item.kind == "engine_analysis":
                    db_session.delete(item)
            else:
                change = (
                    {"diagnosis_rule_version": "unsupported"}
                    if missing_kind == "unsupported"
                    else {"focused_multipv": []}
                )
                item.payload = {**item.payload, **change}
                db_session.add(item)
    db_session.commit()
    row = read_pattern(client, auth)
    assert row["status"] == "not_enough_games"
    assert row["before"]["games"] == 2
    assert row["before"]["excluded"] == {"unsupported_candidate_evidence": 1}
    assert row["before"]["per_100_moves"] is None
    auth_player(client, "foreign-owner")
    foreign = seed_online_game(db_session, "foreign-owner", TRAINING - timedelta(days=4))
    review = db_session.exec(
        select(GameMoveReview).where(GameMoveReview.game_id == games[1].id, GameMoveReview.ply == 1)
    ).one()
    foreign_review = db_session.exec(
        select(GameMoveReview).where(GameMoveReview.game_id == foreign.id, GameMoveReview.ply == 1)
    ).one()
    review.before_analysis_id = foreign_review.before_analysis_id
    db_session.add(review)
    db_session.commit()
    row = read_pattern(client, auth)
    assert row["before"]["games"] == 1
    assert row["before"]["excluded"]["invalid_review_provenance"] == 1
    assert (
        client.get(
            "/v1/players/foreign-owner/progress/pattern-recurrence", headers=auth
        ).status_code
        == 403
    )


def test_timestamp_provenance_short_unfinished_and_opponent_coverage(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    for day in (-1, 1):
        seed_online_game(
            db_session,
            "recurrence-owner",
            TRAINING + timedelta(days=day),
            owner_color="black",
            candidates=0,
        )
    for kind in ("date", "site", "moves", "unfinished", "short"):
        game = seed_online_game(
            db_session,
            "recurrence-owner",
            TRAINING - timedelta(days=2),
            moves=19 if kind == "short" else 20,
        )
        parsed = chess.pgn.read_game(io.StringIO(game.pgn))
        assert parsed is not None
        if kind == "date":
            del parsed.headers["UTCTime"]
        elif kind == "site":
            parsed.headers["Site"] = "https://example.com/game/123"
        elif kind == "unfinished":
            parsed.headers["Result"] = "*"
            game.result = "*"
        elif kind == "moves":
            game.moves = game.moves[:-1]
        game.headers = dict(parsed.headers)
        game.pgn = str(parsed)
        db_session.add(game)
    db_session.commit()
    response = client.get(
        "/v1/players/recurrence-owner/progress/pattern-recurrence", headers=auth
    ).json()
    assert response["coverage"]["ambiguous_played_time"] == 1
    assert response["coverage"]["not_supported_online_source"] == 1
    assert response["coverage"]["invalid_online_provenance"] == 1
    assert response["coverage"]["unfinished"] == 1
    row = next(row for row in response["patterns"] if row["code"] == CODE)
    assert row["before"]["games"] == 1
    assert row["before"]["occurrences"] == 0
    assert row["before"]["excluded"] == {"fewer_than_20_owner_moves": 1}


def test_first_training_verified_failed_puzzle_reset_and_generic_attempt_rejection(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = auth_player(client)
    item = seed_puzzle(db_session)
    study = StudySession(player_id="recurrence-owner", domain="puzzles", started_at=TRAINING)
    db_session.add(study)
    db_session.flush()
    db_session.add(
        ContentAttempt(
            player_id="recurrence-owner",
            item_id=item.id,
            session_id=study.id,
            completed_at=TRAINING - timedelta(days=20),
            success=True,
        )
    )
    db_session.commit()
    assert read_pattern(client, auth)["first_training_at"] is None
    assert read_pattern(client, auth)["before"]["occurrences"] is None
    seed_puzzle(db_session)
    monkeypatch.setattr("scan64.content.puzzle_practice.utc_now", lambda: TRAINING)
    run = complete_failed_puzzle(client, auth)
    assert read_pattern(client, auth)["first_training_at"] == TRAINING.isoformat().replace(
        "+00:00", "Z"
    )
    game = seed_online_game(db_session, "recurrence-owner", TRAINING - timedelta(days=2))
    player = db_session.get(Player, "recurrence-owner")
    assert player is not None
    player.preferences = {
        **player.preferences,
        RESET_AT_KEY: (TRAINING + timedelta(days=1)).isoformat(),
    }
    db_session.add(player)
    db_session.commit()
    assert read_pattern(client, auth)["first_training_at"] is None
    assert db_session.get(Game, game.id) is not None
    # A completed run whose attempt owner is tampered cannot anchor another player.
    attempt = db_session.get(ContentAttempt, run["run_id"])
    assert attempt is not None
    attempt.player_id = "someone-else"
    db_session.add(attempt)
    player.preferences = {}
    db_session.add(player)
    db_session.commit()
    assert read_pattern(client, auth)["first_training_at"] is None


def test_earliest_verified_incorrect_lesson_precedes_puzzle(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    game = seed_online_game(db_session, "recurrence-owner", TRAINING - timedelta(days=3))
    review = db_session.exec(
        select(GameMoveReview).where(GameMoveReview.game_id == game.id, GameMoveReview.ply == 1)
    ).one()
    analysis = db_session.get(EngineAnalysis, review.before_analysis_id)
    assert analysis is not None
    proof = db_session.exec(
        select(Evidence).where(
            Evidence.kind == "missed_opportunity",
            Evidence.position_id == str(review.reached_position_id),
        )
    ).first()
    assert proof is not None
    lesson = PersistedLessonOpportunity(
        player_id="recurrence-owner",
        game_id=game.id,
        source_position_id=review.source_position_id,
        verification_status="verified",
        verification_analysis_id=analysis.id,
        lesson_spec={
            "schema_version": "0.2.0",
            "lesson_id": "fixture-lesson",
            "source": {"kind": "custom", "fen": FEN},
            "diagnosis": {"primary": CODE, "confidence": 1.0, "evidence_refs": [proof.evidence_id]},
            "objective": {"type": "find_best_move", "instruction": "Capture the queen."},
            "interaction": {
                "input": "click",
                "maximum_attempts": 3,
                "accepted_moves": [{"san": "Rxa2"}],
            },
            "verification": {"status": "verified", "engine": "synthetic"},
        },
    )
    when = TRAINING - timedelta(days=1)
    study = StudySession(player_id="recurrence-owner", domain="daily_training", started_at=when)
    db_session.add_all([lesson, study])
    db_session.flush()
    attempt = LessonAttempt(
        session_id=study.id,
        player_id="recurrence-owner",
        lesson_id=str(lesson.id),
        source_kind="persisted_opportunity",
        opportunity_id=lesson.id,
        submitted_move="Nf3",
        elapsed_ms=1000,
        hints_used=2,
        success=False,
        grading_status="ungraded",
        profile_update_result="not_applied",
        completed_at=when,
    )
    db_session.add(attempt)
    db_session.commit()
    assert read_pattern(client, auth)["first_training_at"] == TRAINING.isoformat().replace(
        "+00:00", "Z"
    )
    attempt.grading_status = "verified"
    db_session.add(attempt)
    db_session.commit()
    assert read_pattern(client, auth)["first_training_at"] == when.isoformat().replace(
        "+00:00", "Z"
    )


def test_auth_and_complete_projection_share_one_worker_not_request_loop(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    seed_online_game(db_session, "recurrence-owner", TRAINING - timedelta(days=1))
    assert client.portal is not None
    request_thread = client.portal.call(get_ident)
    request_loop = client.portal.call(asyncio.get_running_loop)
    worker_threads: list[int] = []
    sql_threads: list[int] = []
    original_auth = reports.require_player_token
    original_read = reports.read_pattern_recurrence

    def tracked_auth(request: Any, player_id: str, session: Session) -> Any:
        assert session is db_session  # Preserve dependency overrides.
        worker_threads.append(get_ident())
        return original_auth(request, player_id, session)

    async def tracked_read(player_id: str, session: Session) -> PatternRecurrenceReport:
        assert session is db_session
        assert asyncio.get_running_loop() is not request_loop
        worker_threads.append(get_ident())
        return await original_read(player_id, session)

    def track_sql(*_args: Any) -> None:
        sql_threads.append(get_ident())

    monkeypatch.setattr(reports, "require_player_token", tracked_auth)
    monkeypatch.setattr(reports, "read_pattern_recurrence", tracked_read)
    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", track_sql)
    try:
        assert read_pattern(client, auth)["before"]["occurrences"] == 1
    finally:
        event.remove(engine, "before_cursor_execute", track_sql)
    assert len(worker_threads) == 2
    assert len(set(worker_threads)) == 1
    assert worker_threads[0] != request_thread
    assert sql_threads and set(sql_threads) == set(worker_threads)


@pytest.mark.parametrize("corruption", ["unfinished", "off_line_mate"])
def test_server_accepted_short_alternative_mate_anchors_but_corrupt_runs_do_not(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    auth = auth_player(client)
    item = seed_puzzle(db_session)
    initial = chess.Board("8/7k/5KQ1/8/8/8/8/8 b - - 0 1")
    source = initial.copy()
    source.push_uci("h7h8")
    solution = ["g6h6", "h8g8", "h6g7"]
    item.payload = {
        **item.payload,
        "fen": initial.fen(),
        "setup": "h7h8",
        "moves": ["h7h8", *solution],
        "lesson": {
            **item.payload["lesson"],
            "source": {"kind": "external", "fen": source.fen()},
            "interaction": {"input": "move", "maximum_attempts": 3, "solution": solution},
        },
    }
    db_session.add(item)
    db_session.commit()
    monkeypatch.setattr("scan64.content.puzzle_practice.utc_now", lambda: TRAINING)
    started = client.post("/v1/puzzles/runs", headers=auth)
    assert started.status_code == 200, started.text
    run = started.json()["run"]
    solved = client.post(
        f"/v1/puzzles/runs/{run['run_id']}/moves",
        headers=auth,
        json={
            "move": "g6g7",
            "expected_cursor": run["cursor"],
            "expected_revision": run["revision"],
        },
    )
    assert solved.status_code == 200, solved.text
    assert solved.json()["state"] == "solved"
    assert solved.json()["played_moves"] == ["g6g7"]
    assert solved.json()["cursor"] < len(solution)
    assert read_pattern(client, auth)["first_training_at"] == "2026-10-10T12:00:00Z"
    persisted = db_session.get(PuzzleRun, run["run_id"])
    assert persisted is not None
    if corruption == "unfinished":
        # A legal stored prefix is not a terminal solve unless it mates.
        played = ["g6h6"]
    else:
        # A real final mate cannot excuse an earlier off-solution move.
        played = solution
        item.payload = {
            **item.payload,
            "lesson": {
                **item.payload["lesson"],
                "interaction": {
                    "input": "move",
                    "maximum_attempts": 3,
                    "solution": ["g6h5", "h8g8", "h5h3"],
                },
            },
        }
        db_session.add(item)
    replay = source.copy()
    for move in played:
        replay.push_uci(move)
    assert replay.is_checkmate() is (corruption == "off_line_mate")
    persisted.played_moves = played
    persisted.cursor = len(played)
    persisted.fen = replay.fen()
    db_session.add(persisted)
    db_session.commit()
    assert read_pattern(client, auth)["first_training_at"] is None


@pytest.mark.parametrize(
    ("variant", "eligible"),
    [
        ("Chess", True),
        ("standard", True),
        ("Normal", True),
        ("From Position", True),
        ("Atomic", False),
        ("Chess960", False),
    ],
)
def test_standard_importer_variants_remain_report_eligible(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    eligible: bool,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    game = seed_online_game(db_session, "recurrence-owner", TRAINING - timedelta(days=1))
    parsed = chess.pgn.read_game(io.StringIO(game.pgn))
    assert parsed is not None
    parsed.headers["Variant"] = variant
    game.pgn = str(parsed)
    game.headers = dict(parsed.headers)
    db_session.add(game)
    db_session.commit()
    row = read_pattern(client, auth)
    assert row["before"]["games"] == int(eligible)
    assert row["before"]["occurrences"] == (1 if eligible else None)


def test_missing_detector_witness_removes_game_instead_of_reporting_zero(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    games = [
        seed_online_game(db_session, "recurrence-owner", TRAINING + timedelta(days=day))
        for day in (-3, -2, -1, 1, 2, 3)
    ]
    assert read_pattern(client, auth)["status"] == "comparison"
    victim = db_session.exec(
        select(GameMoveReview).where(GameMoveReview.game_id == games[0].id, GameMoveReview.ply == 1)
    ).one()
    witness = db_session.exec(
        select(Evidence).where(
            Evidence.position_id == str(victim.reached_position_id),
            Evidence.kind == "missed_opportunity",
        )
    ).one()
    assert witness.payload["missed_type"] == "capture"
    assert witness.payload["swing_cp"] == 300
    db_session.delete(witness)
    db_session.commit()
    engine_proof = db_session.exec(
        select(Evidence).where(
            Evidence.position_id == str(victim.reached_position_id),
            Evidence.kind == "engine_analysis",
        )
    ).one()
    assert engine_proof is not None
    row = read_pattern(client, auth)
    assert row["status"] == "not_enough_games"
    assert row["before"]["candidate_games"] == 3
    assert row["before"]["games"] == 2
    assert row["before"]["owner_moves"] == 40
    assert row["before"]["occurrences"] == 2
    assert row["before"]["excluded"] == {"unsupported_candidate_evidence": 1}
    assert row["before"]["per_100_moves"] is None
    assert row["before"]["wilson_95_per_100"] is None
    assert row["after"]["games"] == 3


def test_saved_candidate_verified_abstention_keeps_real_zero(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    for day in (-3, -2, -1, 1, 2, 3):
        seed_online_game(
            db_session,
            "recurrence-owner",
            TRAINING + timedelta(days=day),
            quiet_candidates=True,
        )
    assert (
        db_session.exec(select(Evidence).where(Evidence.kind == "missed_opportunity")).all() == []
    )
    row = read_pattern(client, auth)
    assert row["status"] == "comparison"
    for period in ("before", "after"):
        assert row[period]["games"] == 3
        assert row[period]["owner_moves"] == 60
        assert row[period]["occurrences"] == 0
        assert row[period]["per_100_moves"] == 0
        assert row[period]["excluded"] == {}


def test_owner_library_exceeding_sqlite_bind_limit_is_not_capped(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    for day in (-3, -2, -1, 1, 2, 3):
        seed_online_game(db_session, "recurrence-owner", TRAINING + timedelta(days=day))
    for _ in range(59):
        seed_online_game(
            db_session,
            "recurrence-owner",
            TRAINING + timedelta(days=4),
            moves=1,
        )
    assert len(db_session.exec(select(Game)).all()) == 65
    assert len(db_session.exec(select(Position)).all()) > 64
    connection = db_session.connection().connection.driver_connection
    assert isinstance(connection, sqlite3.Connection)
    previous = connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 64)
    try:
        response = client.get(
            "/v1/players/recurrence-owner/progress/pattern-recurrence", headers=auth
        )
        assert response.status_code == 200, response.text
    finally:
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous)
    report = response.json()
    assert report["coverage"]["owned_saved_games"] == 65
    assert report["coverage"]["dated_finished_online_games"] == 65
    row = next(row for row in report["patterns"] if row["code"] == CODE)
    assert row["status"] == "comparison"
    assert row["before"]["games"] == row["after"]["games"] == 3
    assert row["before"]["owner_moves"] == row["after"]["owner_moves"] == 60
    assert row["before"]["occurrences"] == row["after"]["occurrences"] == 3
    assert row["after"]["candidate_games"] == 62
    assert row["after"]["excluded"] == {"fewer_than_20_owner_moves": 59}


@pytest.mark.parametrize("earlier_copy", ["unanalysed", "unsupported", "eligible"])
def test_online_duplicates_prefer_eligible_representative_before_consuming_identity(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    earlier_copy: str,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    earlier = seed_online_game(
        db_session,
        "recurrence-owner",
        TRAINING - timedelta(days=3),
        candidates=7,
        game_id=UUID(int=1),
    )
    later = seed_online_game(
        db_session,
        "recurrence-owner",
        TRAINING - timedelta(days=3),
        game_id=UUID(int=2),
    )
    parsed = chess.pgn.read_game(io.StringIO(later.pgn))
    assert parsed is not None
    parsed.headers["Site"] = earlier.headers["Site"]
    later.pgn = str(parsed)
    later.headers = dict(parsed.headers)
    db_session.add(later)
    if earlier_copy == "unanalysed":
        job = db_session.exec(select(AnalysisJob).where(AnalysisJob.game_id == earlier.id)).one()
        job.status = "queued"
        job.completed_at = None
        db_session.add(job)
    elif earlier_copy == "unsupported":
        review = db_session.exec(
            select(GameMoveReview).where(
                GameMoveReview.game_id == earlier.id, GameMoveReview.ply == 1
            )
        ).one()
        proof = db_session.exec(
            select(Evidence).where(
                Evidence.position_id == str(review.reached_position_id),
                Evidence.kind == "engine_analysis",
            )
        ).one()
        db_session.delete(proof)
    db_session.commit()
    for day in (-2, -1, 1, 2, 3):
        seed_online_game(db_session, "recurrence-owner", TRAINING + timedelta(days=day))
    response = client.get("/v1/players/recurrence-owner/progress/pattern-recurrence", headers=auth)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["coverage"]["owned_saved_games"] == 7
    assert report["coverage"]["duplicate_online_game"] == 1
    assert report["coverage"]["dated_finished_online_games"] == 6
    row = next(row for row in report["patterns"] if row["code"] == CODE)
    assert row["status"] == "comparison"
    assert row["before"]["candidate_games"] == row["before"]["games"] == 3
    assert row["before"]["owner_moves"] == 60
    assert row["before"]["occurrences"] == (9 if earlier_copy == "eligible" else 3)
    assert row["before"]["excluded"] == {}
    assert row["after"]["games"] == 3


@pytest.mark.parametrize("missing_input", ["witness", "source"])
def test_allowed_mate_requires_saved_witness_and_producer_source_search(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    missing_input: str,
) -> None:
    code = "threat_processing.allowed_mate"
    # Moving the knight instead of capturing the queen allows rook-protected ...Qxg2#.
    monkeypatch.setitem(globals(), "FEN", "4k1r1/8/8/8/8/8/q5PP/R5NK w - - 0 1")
    auth = auth_player(client)
    puzzle = seed_puzzle(db_session)
    puzzle.skill_mapping = {code: 1.0}
    db_session.add(puzzle)
    db_session.commit()
    monkeypatch.setattr("scan64.content.puzzle_practice.utc_now", lambda: TRAINING)
    complete_failed_puzzle(client, auth)
    games = [
        seed_online_game(db_session, "recurrence-owner", TRAINING + timedelta(days=day))
        for day in (-3, -2, -1, 1, 2, 3)
    ]
    review = db_session.exec(
        select(GameMoveReview).where(GameMoveReview.game_id == games[0].id, GameMoveReview.ply == 1)
    ).one()
    source = db_session.get(Position, review.source_position_id)
    reached = db_session.get(Position, review.reached_position_id)
    before = db_session.get(EngineAnalysis, review.before_analysis_id)
    after = db_session.get(EngineAnalysis, review.after_analysis_id)
    assert source is not None and reached is not None and before is not None and after is not None
    after.raw_result = [{"pv": ["a2g2"], "score_mate": 1}]
    db_session.add(after)
    focused = db_session.exec(
        select(EngineAnalysis).where(
            EngineAnalysis.position_id == reached.id,
            EngineAnalysis.id != after.id,
        )
    ).one()
    focused.raw_result = [{"pv": ["a2g2"], "score_mate": 1}]
    db_session.add(focused)
    mate_source = EngineAnalysis(
        position_id=source.id,
        config={"nodes": 1_000_000, "multipv": 4},
        raw_result=[{"pv": ["a1a2"], "score_cp": 300}],
    )
    db_session.add(mate_source)
    for proof in db_session.exec(
        select(Evidence).where(Evidence.position_id == str(reached.id))
    ).all():
        db_session.delete(proof)
    witnesses = compose_candidate_evidence(
        before_board=chess.Board(source.fen),
        after_board=chess.Board(reached.fen),
        history_san=[review.san],
        initial_fen=FEN,
        position_id=str(reached.id),
        fast_analysis=before,
        reply_analysis=after,
        focused_analysis=focused,
        mate_source_analysis=mate_source,
        played_move=review.san,
        swing_cp=10_290,
    )
    db_session.add_all(witnesses)
    db_session.commit()
    response = client.get("/v1/players/recurrence-owner/progress/pattern-recurrence", headers=auth)
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["patterns"] if row["code"] == code)
    assert row["status"] == "comparison"
    assert row["before"]["games"] == row["after"]["games"] == 3
    assert row["before"]["owner_moves"] == 60
    assert row["before"]["occurrences"] == 1
    assert row["after"]["occurrences"] == 0
    assert row["before"]["excluded"] == {}
    if missing_input == "witness":
        proof = next(
            proof for proof in witnesses if proof.payload.get("blunder_type") == "allowed_mate"
        )
        db_session.delete(proof)
    else:
        db_session.delete(mate_source)
    db_session.commit()
    response = client.get("/v1/players/recurrence-owner/progress/pattern-recurrence", headers=auth)
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["patterns"] if row["code"] == code)
    assert row["status"] == "not_enough_games"
    assert row["before"]["candidate_games"] == 3
    assert row["before"]["games"] == 2
    assert row["before"]["owner_moves"] == 40
    assert row["before"]["occurrences"] == 0
    assert row["before"]["excluded"] == {"unsupported_candidate_evidence": 1}
    assert row["before"]["per_100_moves"] is None
    assert row["before"]["wilson_95_per_100"] is None


def _unpack_coach_packet(client: TestClient, auth: dict[str, str]) -> tuple[str, str]:
    response = client.get("/v1/players/recurrence-owner/progress/coach-packet", headers=auth)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="scan64-coach-packet.zip"'
    )
    assert response.headers["cache-control"] == "private, no-store"
    with ZipFile(io.BytesIO(response.content)) as packet:
        assert packet.namelist() == ["summary.md", "evidence.pgn"]
        assert packet.testzip() is None
        return packet.read("summary.md").decode(), packet.read("evidence.pgn").decode()


def test_coach_packet_exact_uncapped_cohort_pgn_and_private_read_only_download(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    games = [
        seed_online_game(
            db_session,
            "recurrence-owner",
            TRAINING + timedelta(days=day),
            candidates=7 if day < 0 else 1,
        )
        for day in (-3, -2, -1, 1, 2, 3)
    ]
    auth_player(client, "other-owner")
    seed_online_game(db_session, "other-owner", TRAINING - timedelta(days=1), candidates=8)
    # Arbitrary owner metadata is not part of the public evidence projection.
    for game in games:
        parsed = chess.pgn.read_game(io.StringIO(game.pgn))
        assert parsed is not None
        parsed.headers["Event"] = "PRIVATE EVENT / NOT AN EXPORT HEADER"
        parsed.headers["Annotator"] = auth["Authorization"]
        game.headers = dict(parsed.headers)
        game.pgn = str(parsed)
        db_session.add(game)
    db_session.commit()
    expected = read_pattern(client, auth)
    writes: list[str] = []

    def no_write(
        _conn: Any, _cursor: Any, statement: str, _parameters: Any, _context: Any, _many: bool
    ) -> None:
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    async def no_engine(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("coach packet must use saved evidence, not search")

    monkeypatch.setattr(StockfishAdapter, "analyze_position", no_engine)
    monkeypatch.setattr(PoolBoundAdapter, "analyze_position", no_engine)
    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", no_write)
    try:
        summary, pgn = _unpack_coach_packet(client, auth)
    finally:
        event.remove(engine, "before_cursor_execute", no_write)
    assert writes == []
    assert "Descriptive association only" in summary
    assert "moves within games are correlated" in summary
    assert "Before/after comparison" in summary
    assert "at least 3 finished, fully analysed games with at least 20 owner moves" in summary
    for period in ("before", "after"):
        row = expected[period]
        assert f"Eligible games: {row['games']} / {row['candidate_games']}" in summary
        assert f"Owner moves (denominator): {row['owner_moves']}" in summary
        assert f"Occurrences: {row['occurrences']}" in summary
        assert f"Occurrences per 100 owner moves: {row['per_100_moves']}" in summary
        assert f"{row['wilson_95_per_100'][0]} to {row['wilson_95_per_100'][1]}" in summary
    for private in (
        "PRIVATE EVENT",
        auth["Authorization"],
        auth["Authorization"].removeprefix("Bearer "),
        "FixtureWhite",
        "FixtureBlack",
        "chess.com",
        "other-owner",
        *(str(game.id) for game in games),
    ):
        assert private not in summary + pgn
    refs = {
        f"G{index:04d}": game
        for index, game in enumerate(sorted(games, key=lambda g: str(g.id)), 1)
    }
    counts = {"before": 0, "after": 0}
    stream = io.StringIO(pgn)
    while (parsed := chess.pgn.read_game(stream)) is not None:
        assert parsed.errors == []
        assert parsed.headers["SetUp"] == "1"
        board = parsed.board()
        assert board.is_valid()
        assert parsed.headers["FEN"] == board.fen()
        source_game = refs[parsed.headers["GameReference"]]
        ply = int(parsed.headers["SourcePly"])
        review = db_session.exec(
            select(GameMoveReview).where(
                GameMoveReview.game_id == source_game.id, GameMoveReview.ply == ply
            )
        ).one()
        position = db_session.get(Position, review.source_position_id)
        assert position is not None and board.fen() == position.fen
        assert [move.uci() for move in parsed.mainline_moves()] == [review.move_uci]
        assert parsed.headers["Pattern"] == CODE
        assert CODE in parsed.comment
        assert f"Saved trusted primary diagnosis: {expected['name']}" in parsed.end().comment
        assert parsed.headers["EvidenceReference"] in summary
        assert f"{parsed.headers['GameReference']}, source ply {ply}" in summary
        counts[parsed.headers["Period"]] += 1
    assert counts == {
        "before": expected["before"]["occurrences"],
        "after": expected["after"]["occurrences"],
    }
    assert counts == {"before": 21, "after": 3}


def test_coach_packet_sparse_and_no_anchor_never_invent_evidence(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = auth_player(client)
    summary, pgn = _unpack_coach_packet(client, auth)
    assert pgn == ""
    assert "Not enough games" in summary
    assert "Occurrences: unavailable (not zero)" in summary
    seed_online_game(db_session, "recurrence-owner", TRAINING - timedelta(days=1))
    summary, pgn = _unpack_coach_packet(client, auth)
    assert pgn == ""  # No training anchor means no M95 sample membership.
    train(client, db_session, auth, monkeypatch)
    summary, pgn = _unpack_coach_packet(client, auth)
    assert "Not enough games" in summary
    assert "Eligible games: 1 / 1" in summary
    assert "Owner moves (denominator): 20" in summary
    assert "Occurrences: 1" in summary
    assert "Occurrences per 100 owner moves: unavailable" in summary
    assert "95% Wilson interval per 100 owner moves: unavailable" in summary
    stream = io.StringIO(pgn)
    assert chess.pgn.read_game(stream) is not None
    assert chess.pgn.read_game(stream) is None


@pytest.mark.parametrize("corruption", ["unverified", "mismatched_analysis", "other_owner"])
def test_coach_packet_excludes_unproven_or_unowned_positions(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    auth = auth_player(client)
    train(client, db_session, auth, monkeypatch)
    game = seed_online_game(
        db_session, "recurrence-owner", TRAINING - timedelta(days=1), candidates=2
    )
    if corruption == "other_owner":
        auth_player(client, "other-owner")
        game.owner_player_id = "other-owner"
        db_session.add(game)
    else:
        source = db_session.exec(
            select(GameMoveReview).where(
                GameMoveReview.game_id == game.id,
                # Fail after a valid occurrence was collected: it must not leak
                # from a game later excluded by any of the report's gates.
                GameMoveReview.ply == (3 if corruption == "unverified" else 40),
            )
        ).one()
        if corruption == "unverified":
            for item in db_session.exec(
                select(Evidence).where(Evidence.position_id == str(source.reached_position_id))
            ).all():
                db_session.delete(item)
        else:
            source.before_analysis_id = source.after_analysis_id
            db_session.add(source)
    db_session.commit()
    summary, pgn = _unpack_coach_packet(client, auth)
    assert pgn == ""
    assert str(game.id) not in summary
    assert "No included evidence positions for this pattern." in summary
    if corruption == "unverified":
        assert "unsupported candidate evidence: 1" in summary
    elif corruption == "mismatched_analysis":
        assert "invalid review provenance: 1" in summary


def test_coach_packet_requires_exact_owner_bearer(client: TestClient) -> None:
    auth_player(client)
    other = auth_player(client, "other-owner")
    path = "/v1/players/recurrence-owner/progress/coach-packet"
    for headers, status in (
        ({}, 401),
        ({"Authorization": "Bearer wrong-token"}, 403),
        (other, 403),
    ):
        assert client.get(path, headers=headers).status_code == status

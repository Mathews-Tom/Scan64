from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import chess
import chess.pgn
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from scan64.api.models import Player
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedLessonOpportunity,
)
from scan64.chess.games.ingestion import ingest_fen
from scan64.chess.games.models import Game
from scan64.content.models import ContentAttempt, ContentItem, LessonAttempt, StudySession
from scan64.content.puzzle_practice import utc_now
from scan64.learning.evidence.composer import compose_candidate_evidence
from scan64.learning.evidence.models import Evidence
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

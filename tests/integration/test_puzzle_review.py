from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import chess
import chess.engine
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from scan64.api.app import app
from scan64.chess.analysis.models import EngineAnalysis
from scan64.content import puzzle_practice
from scan64.content.models import ContentAttempt, LessonAttempt
from scan64.content.puzzle_practice_models import (
    PuzzleDailySet,
    PuzzleDailySlot,
    PuzzleRating,
    PuzzleRatingEvent,
)
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from tests.integration.test_puzzle_runs import _action, _auth, _safe, _seed, _start


def _analysis(line: list[str], score: int | None = 0) -> EngineAnalysis:
    result: dict[str, Any] = {"pv": line}
    if score is not None:
        result["score_cp"] = score
    return EngineAnalysis(raw_result=[result])


def _engine(monkeypatch: pytest.MonkeyPatch, results: list[EngineAnalysis]) -> AsyncMock:
    analyze = AsyncMock(side_effect=results)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    return analyze


def _fail(client: TestClient, auth: dict[str, str], run: dict[str, Any]) -> dict[str, Any]:
    for _ in range(3):
        response = _action(client, auth, run, "moves", move="a7a6")
        assert response.status_code == 200, response.text
        run = response.json()
    assert run["state"] == "failed"
    return run


@pytest.mark.parametrize("solved", [False, True])
def test_owned_terminal_source_review_is_legal_and_never_scores_again(
    client: TestClient, db_session: Session, solved: bool
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    url = f"/v1/puzzles/runs/{run['run_id']}/review"
    assert client.get(url, headers=auth).status_code == 409
    if solved:
        run = _action(client, auth, run, "moves", move="e7e5").json()
        run = _action(client, auth, run, "moves", move="b8c6").json()
    else:
        run = _fail(client, auth, run)
    before = client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=auth).json()
    for _ in range(4):
        response = client.get(url, headers=auth)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"run_id", "initial_fen", "solution"}
        assert body["run_id"] == run["run_id"]
        assert body["solution"] == ["e7e5", "g1f3", "b8c6"]
        board = chess.Board(body["initial_fen"])
        assert board.turn == chess.BLACK  # The opponent setup is already played.
        for uci in body["solution"]:
            assert chess.Move.from_uci(uci) in board.legal_moves
            board.push_uci(uci)
    assert client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=auth).json() == before
    assert len(db_session.exec(select(ContentAttempt)).all()) == 1
    assert len(db_session.exec(select(PuzzleRatingEvent)).all()) == 1


def test_review_and_explanation_auth_precede_engine_work(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _auth(client)
    other = _auth(client, "other-review-player")
    _seed(db_session)
    run = _start(client, auth)
    analyze = _engine(monkeypatch, [])
    url = f"/v1/puzzles/runs/{run['run_id']}"
    assert client.get(f"{url}/review").status_code == 401
    assert (
        client.post(
            f"{url}/explanations",
            json={"expected_cursor": 0, "expected_revision": 0, "move": "a7a6"},
        ).status_code
        == 401
    )
    for run_id in (run["run_id"], "missing"):
        url = f"/v1/puzzles/runs/{run_id}"
        assert client.get(f"{url}/review", headers=other).status_code == 404
        response = client.post(
            f"{url}/explanations",
            headers=other,
            json={"expected_cursor": 0, "expected_revision": 0, "move": "a7a6"},
        )
        assert response.status_code == 404
    analyze.assert_not_awaited()


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"move": "e7e7"}, 422),
        ({"move": "e4"}, 422),
        ({"move": "a1a8"}, 422),
        ({"move": "a7a6", "review_cursor": 0}, 422),
        ({"move": "a7a6", "expected_revision": 9}, 409),
        ({"move": "a7a6", "expected_cursor": 2}, 409),
        ({"move": "a7a6", "fen": chess.STARTING_FEN}, 422),
    ],
)
def test_invalid_or_stale_active_feedback_never_searches_or_charges_assistance(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fields: dict[str, Any],
    status: int,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    analyze = _engine(monkeypatch, [])
    response = _action(client, auth, run, "explanations", **fields)
    assert response.status_code == status, response.text
    analyze.assert_not_awaited()
    assert client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=auth).json() == run
    assert not db_session.exec(select(ContentAttempt)).all()


@pytest.mark.parametrize(
    ("baseline", "branch", "verdict"),
    [(0, 0, "alternative"), (100, 0, "uncertain"), (None, 0, "uncertain"), (500, 500, "mistake")],
)
def test_active_explanation_compares_both_povs_and_records_only_assistance(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    baseline: int | None,
    branch: int,
    verdict: str,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    analyze = _engine(
        monkeypatch,
        [
            _analysis(["e7e5", "g1f3", "b8c6"], baseline),
            _analysis(["g1f3", "b8c6"], branch),
        ],
    )
    response = _action(client, auth, run, "explanations", move="a7a6")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "run",
        "move",
        "fen_before",
        "fen_after",
        "verdict",
        "summary",
        "reply_line",
        "visualizations",
    }
    assert body["verdict"] == verdict
    assert body["fen_before"] == run["fen"] and body["move"] == "a7a6"
    board = chess.Board(run["fen"])
    board.push_uci("a7a6")
    assert body["fen_after"] == board.fen()
    assert body["reply_line"] == ["g1f3", "b8c6"]
    for uci in body["reply_line"]:
        assert chess.Move.from_uci(uci) in board.legal_moves
        board.push_uci(uci)
    assert [call.args[0] for call in analyze.await_args_list] == [
        body["fen_before"],
        body["fen_after"],
    ]
    assisted = body["run"]
    _safe(assisted)
    assert assisted["hint_step"] == 3 and assisted["revision"] == 1
    assert assisted["cursor"] == assisted["wrong_attempts"] == 0
    assert assisted["fen"] == run["fen"] and assisted["played_moves"] == []
    assert not db_session.exec(select(ContentAttempt)).all()
    assert not db_session.exec(select(PuzzleRatingEvent)).all()
    assisted = _action(client, auth, assisted, "moves", move="e7e5").json()
    assert _action(client, auth, assisted, "moves", move="b8c6").json()["state"] == "solved"
    assert db_session.exec(select(ContentAttempt)).one().hint_assisted


@pytest.mark.parametrize("failure", [TimeoutError(), chess.engine.EngineError("failed"), OSError()])
def test_failed_engine_does_not_mark_active_assistance(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    analyze = AsyncMock(side_effect=failure)
    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = _action(client, auth, run, "explanations", move="a7a6")
    assert response.status_code == 503
    assert "verdict" not in response.json()
    assert client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=auth).json() == run


@pytest.mark.parametrize("bad_line", [["g1f3", "a8a1"], []])
def test_illegal_or_missing_reply_is_engine_failure_not_a_fabricated_refutation(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    bad_line: list[str],
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    _engine(monkeypatch, [_analysis(["e7e5"]), _analysis(bad_line, 900)])
    response = _action(client, auth, run, "explanations", move="a7a6")
    assert response.status_code == 503
    assert client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=auth).json() == run


@pytest.mark.parametrize("cursor", [1, 3, 4])
def test_terminal_review_cursor_rejects_non_solver_positions_before_search(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    cursor: int,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _fail(client, auth, _start(client, auth))
    analyze = _engine(monkeypatch, [])
    assert (
        _action(
            client,
            auth,
            run,
            "explanations",
            move="a7a6",
            review_cursor=cursor,
        ).status_code
        == 422
    )
    analyze.assert_not_awaited()


def test_terminal_retries_derive_later_positions_and_never_duplicate_results(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _fail(client, auth, _start(client, auth))
    schedules = [row.model_dump() for row in db_session.exec(select(ReviewSchedule)).all()]
    analyze = _engine(
        monkeypatch,
        [
            _analysis(["b8c6", "f1b5"]),
            _analysis(["f1c4", "b8c6"]),
        ]
        * 5,
    )
    for _ in range(5):
        response = _action(
            client,
            auth,
            run,
            "explanations",
            move="a7a6",
            review_cursor=2,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run"] == run
        before = chess.Board(run["fen"])
        before.push_uci("e7e5")
        before.push_uci("g1f3")
        assert body["fen_before"] == before.fen()
        before.push_uci("a7a6")
        assert body["fen_after"] == before.fen()
        for uci in body["reply_line"]:
            assert chess.Move.from_uci(uci) in before.legal_moves
            before.push_uci(uci)
    assert analyze.await_count == 10
    stored = db_session.get(PuzzleRun, run["run_id"])
    assert stored is not None and stored.revision == 3 and stored.hint_steps == {}
    assert len(db_session.exec(select(ContentAttempt)).all()) == 1
    assert len(db_session.exec(select(PuzzleRatingEvent)).all()) == 1
    assert [row.model_dump() for row in db_session.exec(select(ReviewSchedule)).all()] == schedules
    for model in (SkillState, ProfileObservation, LessonAttempt):
        assert not db_session.exec(select(model)).all()


def test_explanation_releases_transaction_and_rejects_a_newer_run_after_await(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    calls = 0

    async def analyze(_fen: str) -> EngineAnalysis:
        nonlocal calls
        assert not db_session.in_transaction()
        calls += 1
        if calls == 1:
            moved = await asyncio.to_thread(_action, client, auth, run, "moves", move="a7a6")
            assert moved.status_code == 200
            # The concurrent request's serializer also reads; close its read transaction.
            db_session.rollback()
            return _analysis(["e7e5", "g1f3"])
        return _analysis(["g1f3", "b8c6"])

    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = _action(client, auth, run, "explanations", move="a7a6")
    assert response.status_code == 409, response.text
    latest = response.json()["detail"]["run"]
    assert latest["revision"] == latest["wrong_attempts"] == 1
    assert latest["hint_step"] == 0 and latest["hints"] == []
    assert not db_session.exec(select(ContentAttempt)).all()


def test_terminal_explanation_does_not_create_new_daily_membership_on_rollover(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _fail(client, auth, _start(client, auth))
    next_day = puzzle_practice.utc_now() + timedelta(days=1)
    monkeypatch.setattr(puzzle_practice, "utc_now", lambda: next_day)
    _engine(monkeypatch, [_analysis(["e7e5"]), _analysis(["g1f3", "b8c6"])])
    writes: list[str] = []

    def record_write(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _many: bool,
    ) -> None:
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record_write)
    try:
        response = _action(client, auth, run, "explanations", move="a7a6")
        assert response.status_code == 200, response.text
        assert not writes
        assert response.json()["run"]["revision"] == run["revision"]
    finally:
        event.remove(bind, "before_cursor_execute", record_write)


def test_verified_mate_refutation_has_a_legal_opponent_arrow(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    original = chess.Board()
    original.push_uci("f2f3")
    _seed(
        db_session,
        original_fen=original.fen(),
        moves=["e7e5", "e2e4", "b8c6", "b1c3"],
    )
    run = _start(client, auth)
    _engine(
        monkeypatch,
        [
            _analysis(["e2e4", "b8c6"]),
            EngineAnalysis(raw_result=[{"pv": ["d8h4"], "score_mate": 1}]),
        ],
    )
    response = _action(client, auth, run, "explanations", move="g2g4")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == "mistake" and "checkmate" in body["summary"]
    assert body["reply_line"] == ["d8h4"]
    after = chess.Board(body["fen_after"])
    assert chess.Move.from_uci(body["move"]) not in after.legal_moves
    after.push_uci(body["reply_line"][0])
    assert after.is_checkmate()
    arrows = [v for v in body["visualizations"] if v["command"] == "draw_arrow"]
    assert arrows[0]["origin"] == "d8" and arrows[0]["target"] == "h4"


def test_solved_run_can_explain_an_earlier_source_position_without_mutation(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    _seed(db_session, moves=["e2e4", "e7e5"])
    run = _start(client, auth)
    run = _action(client, auth, run, "moves", move="e7e5").json()
    analyze = _engine(monkeypatch, [_analysis(["e7e5"]), _analysis(["g1f3"])])
    assert _action(client, auth, run, "explanations", move="a7a6").status_code == 422
    response = _action(client, auth, run, "explanations", move="a7a6", review_cursor=0)
    assert response.status_code == 200, response.text
    assert response.json()["run"] == run
    assert analyze.await_count == 2
    assert len(db_session.exec(select(ContentAttempt)).all()) == 1
    assert len(db_session.exec(select(PuzzleRatingEvent)).all()) == 1


def test_terminal_stale_and_abandoned_feedback_do_not_reach_engine(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    analyze = _engine(monkeypatch, [])
    abandoned = _action(client, auth, run, "abandon").json()
    assert _action(client, auth, abandoned, "explanations", move="a7a6").status_code == 409
    assert (
        client.get(
            f"/v1/puzzles/runs/{run['run_id']}/review",
            headers=auth,
        ).status_code
        == 409
    )
    other = _auth(client, "terminal-stale-player")
    failed = _fail(client, other, _start(client, other))
    assert (
        _action(
            client,
            other,
            failed,
            "explanations",
            move="a7a6",
            expected_revision=0,
        ).status_code
        == 409
    )
    analyze.assert_not_awaited()


def test_missing_engine_is_explicit_and_does_not_reveal_active_answers(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    monkeypatch.setattr(app.state, "engine_pool_manager", None)
    response = _action(client, auth, run, "explanations", move="a7a6")
    assert response.status_code == 503
    assert "e7e5" not in response.text
    assert client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=auth).json() == run


def test_automatic_draw_is_reported_as_a_fact_without_inventing_a_refutation(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    _seed(
        db_session,
        original_fen="7k/P7/8/8/8/8/8/7K b - - 0 1",
        moves=["h8g8", "a7a8q"],
    )
    run = _start(client, auth)
    analyze = _engine(monkeypatch, [])
    response = _action(client, auth, run, "explanations", move="a7a8n")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == "uncertain"
    assert "insufficient material" in body["summary"]
    assert body["reply_line"] == body["visualizations"] == []
    assert chess.Board(body["fen_after"]).is_insufficient_material()
    assert body["run"]["hint_step"] == 3
    analyze.assert_not_awaited()


def test_read_only_daily_summary_preserves_default_reservation_behavior(
    client: TestClient,
    db_session: Session,
) -> None:
    _auth(client)
    _seed(db_session)
    summary = puzzle_practice.daily_summary("puzzle-player", db_session, read_only=True)
    assert summary["total"] == summary["completed"] == 0
    assert summary["rating"] == 1200 and summary["provisional"]
    assert "not been reserved" in summary["message"]
    assert not db_session.exec(select(PuzzleDailySet)).all()
    assert not db_session.exec(select(PuzzleRating)).all()
    reserved = puzzle_practice.daily_summary("puzzle-player", db_session)
    assert reserved["total"] == 1
    assert (
        puzzle_practice.daily_summary(
            "puzzle-player",
            db_session,
            read_only=True,
        )
        == reserved
    )
    assert len(db_session.exec(select(PuzzleDailySet)).all()) == 1
    assert len(db_session.exec(select(PuzzleRating)).all()) == 1


def test_reset_disappearing_run_after_analysis_cannot_recreate_progress(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    calls = 0

    async def analyze(_fen: str) -> EngineAnalysis:
        nonlocal calls
        assert not db_session.in_transaction()
        calls += 1
        if calls == 1:
            slot = db_session.exec(
                select(PuzzleDailySlot).where(PuzzleDailySlot.run_id == run["run_id"])
            ).one()
            slot.run_id = None
            db_session.add(slot)
            db_session.flush()
            stored = db_session.get(PuzzleRun, run["run_id"])
            assert stored is not None
            db_session.delete(stored)
            db_session.commit()
            return _analysis(["e7e5"])
        return _analysis(["g1f3", "b8c6"])

    monkeypatch.setattr(app.state.engine_pool_manager, "analyze_hint", analyze)
    response = _action(client, auth, run, "explanations", move="a7a6")
    assert response.status_code == 404, response.text
    assert db_session.get(PuzzleRun, run["run_id"]) is None
    assert not db_session.exec(select(ContentAttempt)).all()
    assert not db_session.exec(select(PuzzleRatingEvent)).all()


def test_mate_score_without_mating_witness_cannot_claim_checkmate(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    _engine(
        monkeypatch,
        [
            _analysis(["e7e5"]),
            EngineAnalysis(raw_result=[{"pv": ["g1f3", "b8c6"], "score_mate": 3}]),
        ],
    )
    response = _action(client, auth, run, "explanations", move="a7a6")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply_line"] == ["g1f3", "b8c6"]
    assert "checkmate" not in body["summary"].lower()
    assert all("checkmate" not in v["description"].lower() for v in body["visualizations"])

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

import scan64.api.puzzles as puzzles
from scan64.api.app import app
from scan64.content.models import ContentAttempt, ContentItem, LessonAttempt
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import get_session


def _auth(client: TestClient, player: str = "puzzle-player") -> dict[str, str]:
    response = client.post("/v1/players", json={"id": player})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed(
    session: Session,
    *,
    puzzle_id: str = "test",
    sequence: int = 1,
    original_fen: str = chess.STARTING_FEN,
    moves: list[str] | None = None,
) -> ContentItem:
    moves = moves or ["e2e4", "e7e5", "g1f3", "b8c6"]
    board = chess.Board(original_fen)
    board.push_uci(moves[0])
    item = ContentItem(
        id=f"lichess:{puzzle_id}",
        domain="puzzles",
        version="0.2.0",
        provenance="Lichess puzzle database",
        licence="CC0-1.0",
        skill_mapping={},
        payload={
            "puzzle_id": puzzle_id,
            "fen": original_fen,
            "moves": moves,
            "setup": moves[0],
            "import_sequence": sequence,
            "lesson": {
                "schema_version": "0.2.0",
                "lesson_id": f"lichess:{puzzle_id}",
                "source": {"kind": "external", "fen": board.fen()},
                "objective": {"type": "puzzle", "instruction": "Find the continuation."},
                "interaction": {"input": "move", "maximum_attempts": 3, "solution": moves[1:]},
                "verification": {"status": "source_verified"},
            },
        },
    )
    session.add(item)
    session.commit()
    return item


def _start(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    response = client.post("/v1/puzzles/runs", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ready"
    return response.json()["run"]


def _action(
    client: TestClient, auth: dict[str, str], run: dict[str, Any], action: str, **fields: Any
):
    return client.post(
        f"/v1/puzzles/runs/{run['run_id']}/{action}",
        headers=auth,
        json={"expected_cursor": run["cursor"], "expected_revision": run["revision"], **fields},
    )


def _safe(response: dict[str, Any]) -> None:
    assert set(response) == {
        "run_id",
        "puzzle_id",
        "fen",
        "setup",
        "played_moves",
        "cursor",
        "revision",
        "wrong_attempts",
        "maximum_wrong_attempts",
        "state",
        "hint_step",
        "hints",
    }
    text = json.dumps(response)
    for hidden in ("solution", "accepted_moves", "skill_mapping", "engine", "diagnosis"):
        assert f'"{hidden}"' not in text


def test_empty_exhausted_and_import_order(client: TestClient, db_session: Session) -> None:
    auth = _auth(client)
    empty = client.post("/v1/puzzles/runs", headers=auth).json()
    assert empty["status"] == "empty" and empty["run"] is None
    assert "scan64-cli puzzles import --download" in empty["message"]
    assert "docker exec" in empty["message"]
    _seed(db_session, puzzle_id="alphabetically-first", sequence=2, moves=["e2e4", "e7e5"])
    _seed(db_session, puzzle_id="z-first-imported", sequence=1, moves=["e2e4", "e7e5"])
    run = _start(client, auth)
    assert run["puzzle_id"] == "lichess:z-first-imported"
    assert _action(client, auth, run, "moves", move="e7e5").json()["state"] == "solved"
    run = _start(client, auth)
    assert run["puzzle_id"] == "lichess:alphabetically-first"
    assert _action(client, auth, run, "abandon").json()["state"] == "abandoned"
    exhausted = client.post("/v1/puzzles/runs", headers=auth).json()
    assert exhausted["status"] == "exhausted" and exhausted["run"] is None


def test_solve_auto_reply_resume_and_single_terminal_attempt(
    client: TestClient,
    db_session: Session,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    _safe(run)
    assert run["played_moves"] == [] and run["setup"] == "e2e4"
    assert run["cursor"] == run["revision"] == run["hint_step"] == 0
    assert run["hints"] == []
    assert "e7e5" not in json.dumps(run)
    response = _action(client, auth, run, "moves", move="e7e5")
    assert response.status_code == 200, response.text
    advanced = response.json()
    assert advanced["played_moves"] == ["e7e5", "g1f3"]
    assert advanced["cursor"] == 2 and advanced["revision"] == 1
    assert advanced["state"] == "active" and "b8c6" not in json.dumps(advanced)
    assert _start(client, auth) == advanced
    assert client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=auth).json() == advanced
    solved = _action(client, auth, advanced, "moves", move="b8c6")
    assert solved.status_code == 200 and solved.json()["state"] == "solved"
    duplicate = _action(client, auth, advanced, "moves", move="b8c6")
    assert duplicate.status_code == 409
    _safe(duplicate.json()["detail"]["run"])
    db_session.expire_all()
    attempts = db_session.exec(select(ContentAttempt)).all()
    assert len(attempts) == 1
    assert attempts[0].id == run["run_id"] and attempts[0].success
    assert not attempts[0].hint_assisted
    for model in (SkillState, ProfileObservation, ReviewSchedule, LessonAttempt):
        assert not db_session.exec(select(model)).all()


def test_wrong_submission_budget_and_stale_requests_do_not_spend_it(
    client: TestClient,
    db_session: Session,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    original = run = _start(client, auth)
    for wrong in range(1, 4):
        response = _action(client, auth, run, "moves", move="a7a6")
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["wrong_attempts"] == wrong and run["revision"] == wrong
        assert run["fen"] == original["fen"] and run["cursor"] == 0
        assert run["played_moves"] == []
        assert run["state"] == ("failed" if wrong == 3 else "active")
        stale = _action(client, auth, original, "moves", move="a7a6")
        assert stale.status_code == 409
        assert stale.json()["detail"]["run"] == run
    db_session.expire_all()
    attempts = db_session.exec(select(ContentAttempt)).all()
    assert len(attempts) == 1 and not attempts[0].success
    assert not attempts[0].hint_assisted


def test_requested_hints_are_scoped_to_current_turn_and_mark_assistance(
    client: TestClient,
    db_session: Session,
) -> None:
    auth = _auth(client)
    _seed(db_session)
    run = _start(client, auth)
    stale = run
    for step in (1, 2, 3):
        response = _action(client, auth, run, "hints", step=step)
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["hint_step"] == step and run["revision"] == step
        assert len(run["hints"]) == step
        if step < 3:
            serialized = json.dumps(run["hints"])
            for hidden in ('"line"', '"candidates"', '"move"', '"draw_arrow"', "e7e5", "b8c6"):
                assert hidden not in serialized
        _safe(run)
    assert run["hints"][2]["candidates"][0]["line"] == ["e7e5", "g1f3", "b8c6"]
    conflict = _action(client, auth, stale, "moves", move="a7a6")
    assert conflict.status_code == 409 and conflict.json()["detail"]["run"] == run
    run = _action(client, auth, run, "moves", move="e7e5").json()
    assert run["hint_step"] == 0 and run["hints"] == []
    assert "b8c6" not in json.dumps(run)
    run = _action(client, auth, run, "moves", move="b8c6").json()
    assert run["state"] == "solved"
    db_session.expire_all()
    assert db_session.exec(select(ContentAttempt)).one().hint_assisted


def test_any_legal_mate_and_exact_underpromotion(client: TestClient, db_session: Session) -> None:
    auth = _auth(client)
    _seed(db_session, original_fen="8/5K1k/6Q1/8/8/8/8/8 b - - 0 1", moves=["h7h8", "g6g7"])
    run = _start(client, auth)
    solved = _action(client, auth, run, "moves", move="g6h6")
    assert solved.status_code == 200 and solved.json()["state"] == "solved"
    _seed(
        db_session,
        puzzle_id="promotion",
        sequence=2,
        original_fen="7k/P7/8/8/8/8/8/7K b - - 0 1",
        moves=["h8g8", "a7a8n"],
    )
    run = _start(client, auth)
    wrong = _action(client, auth, run, "moves", move="a7a8q").json()
    assert wrong["wrong_attempts"] == 1 and wrong["state"] == "active"
    assert _action(client, auth, wrong, "moves", move="a7a8n").json()["state"] == "solved"


def test_ownership_payload_validation_and_no_attempt_on_abandon(
    client: TestClient,
    db_session: Session,
) -> None:
    auth = _auth(client)
    other = _auth(client, "other-puzzle-player")
    _seed(db_session)
    assert client.post("/v1/puzzles/runs").status_code == 401
    run = _start(client, auth)
    for run_id in (run["run_id"], "does-not-exist"):
        url = f"/v1/puzzles/runs/{run_id}"
        assert client.get(url, headers=other).json() == {"detail": "Puzzle run not found"}
        for action, fields in (
            ("moves", {"move": "e7e5"}),
            ("hints", {"step": 3}),
            ("abandon", {}),
        ):
            response = client.post(
                f"{url}/{action}",
                headers=other,
                json={"expected_cursor": 0, "expected_revision": 0, **fields},
            )
            assert response.status_code == 404
    invalid = _action(client, auth, run, "moves", move="e5", solution=["e7e5"])
    assert invalid.status_code == 422
    assert _action(client, auth, run, "moves", move="e7e7").status_code == 422
    assert _action(client, auth, run, "hints", step=4).status_code == 422
    assert client.get(f"/v1/puzzles/runs/{run['run_id']}", headers=auth).json() == run
    abandoned = _action(client, auth, run, "abandon")
    assert abandoned.status_code == 200 and abandoned.json()["state"] == "abandoned"
    assert not db_session.exec(select(ContentAttempt)).all()


def test_concurrent_wrong_submissions_use_atomic_revision_check(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'puzzle-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    previous = app.dependency_overrides[get_session]

    def sessions():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    try:
        auth = _auth(client, "racing-player")
        with Session(engine) as session:
            _seed(session)
        run = _start(client, auth)
        barrier = Barrier(2)
        original = puzzles._expect_active

        def synchronized(row, expected, session):
            original(row, expected, session)
            barrier.wait(timeout=10)

        monkeypatch.setattr(puzzles, "_expect_active", synchronized)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(lambda _: _action(client, auth, run, "moves", move="a7a6"), range(2))
            )
        assert sorted(response.status_code for response in responses) == [200, 409]
        with Session(engine) as session:
            stored = session.exec(select(PuzzleRun)).one()
            assert stored.wrong_attempts == stored.revision == 1
            assert not session.exec(select(ContentAttempt)).all()
    finally:
        app.dependency_overrides[get_session] = previous
        engine.dispose()

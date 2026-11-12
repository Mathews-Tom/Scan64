from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

import scan64.content.puzzle_practice as practice
from scan64.content.models import ContentAttempt, StudySession
from scan64.content.puzzle_practice_models import (
    PuzzleDailySet,
    PuzzleDailySlot,
    PuzzleRating,
    PuzzleRatingEvent,
    elo_after,
)
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from tests.integration.test_puzzle_runs import _action, _auth, _seed, _start

NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)


def seed(
    session: Session,
    name: str,
    rating: int = 1200,
    themes: list[str] | None = None,
    sequence: int = 1,
) -> None:
    item = _seed(session, puzzle_id=name, sequence=sequence, moves=["e2e4", "e7e5"])
    item.difficulty_estimate = rating
    item.payload = {**item.payload, "rating": rating, "themes": themes or []}
    session.add(item)
    session.commit()


def clock(monkeypatch: pytest.MonkeyPatch, now: datetime = NOW) -> None:
    monkeypatch.setattr(practice, "utc_now", lambda: now)


@pytest.mark.parametrize(
    "outcome,score,schedule",
    [
        ("solve", 1, False),
        ("wrong_then_solve", 1, False),
        ("hint", 0, True),
        ("fail", 0, True),
        ("abandon", 0, False),
    ],
)
def test_first_exposure_terminal_rating_and_review_atomic(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    score: int,
    schedule: bool,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    seed(db_session, "rated")
    run = _start(client, auth)
    assert run["rated"] and run["daily"]["rating"] == 1200
    assert not db_session.exec(select(PuzzleRatingEvent)).all()
    if outcome == "hint":
        run = _action(client, auth, run, "hints", step=1).json()
        assert _start(client, auth)["run_id"] == run["run_id"]
    if outcome == "wrong_then_solve":
        run = _action(client, auth, run, "moves", move="a7a6").json()
    before = run
    if outcome == "fail":
        for _ in range(2):
            run = _action(client, auth, run, "moves", move="a7a6").json()
        before = run
        response = _action(client, auth, run, "moves", move="a7a6")
    elif outcome == "abandon":
        response = _action(client, auth, run, "abandon")
    else:
        response = _action(client, auth, run, "moves", move="e7e5")
    assert response.status_code == 200, response.text
    terminal = response.json()
    assert terminal["daily"]["completed"] == 1 and terminal["daily"]["remaining"] == 0
    assert terminal["daily"]["rated_count"] == 1 and terminal["daily"]["provisional"]
    db_session.expire_all()
    ledger = db_session.exec(select(PuzzleRatingEvent)).one()
    assert (ledger.before, ledger.after, ledger.score, ledger.sequence) == (
        1200,
        1216 if score else 1184,
        score,
        1,
    )
    assert db_session.get(PuzzleRating, "puzzle-player").rating == ledger.after
    assert bool(db_session.exec(select(ReviewSchedule)).all()) == schedule
    assert bool(db_session.exec(select(ContentAttempt)).all()) == (outcome != "abandon")
    assert not db_session.exec(select(SkillState)).all()
    duplicate = _action(client, auth, before, "abandon")
    assert duplicate.status_code == 409
    assert len(db_session.exec(select(PuzzleRatingEvent)).all()) == 1
    assert client.post("/v1/puzzles/runs", headers=auth).json()["status"] == "exhausted"


def test_set_snapshot_stability_reimport_and_midnight_resume(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch, NOW.replace(hour=23, minute=59))
    auth = _auth(client)
    seed(db_session, "snapshot", 1200)
    first = client.get("/v1/puzzles/daily", headers=auth).json()
    slot = db_session.exec(select(PuzzleDailySlot)).one()
    item_id = slot.item_id
    from scan64.content.models import ContentItem

    item = db_session.get(ContentItem, item_id)
    item.payload = {**item.payload, "rating": 1800}
    item.difficulty_estimate = 1800
    db_session.add(item)
    db_session.commit()
    seed(db_session, "late-import", 1200)
    assert client.get("/v1/puzzles/daily", headers=auth).json() == first
    assert len(db_session.exec(select(PuzzleDailySlot)).all()) == 1
    run = _start(client, auth)
    clock(monkeypatch, NOW + timedelta(days=1))
    resumed = _start(client, auth)
    assert resumed["run_id"] == run["run_id"]
    assert resumed["daily"]["utc_day"] == "2026-11-14"
    assert resumed["daily"]["completed"] == 0
    solved = _action(client, auth, resumed, "moves", move="e7e5").json()
    assert solved["daily"]["rating"] == 1216
    db_session.expire_all()
    assert db_session.exec(select(PuzzleRatingEvent)).one().item_rating == 1200
    assert _start(client, auth)["puzzle_id"] == "lichess:late-import"


def test_failed_review_backlog_and_independent_retirement(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    seed(db_session, "review")
    run = _start(client, auth)
    run = _action(client, auth, run, "hints", step=1).json()
    _action(client, auth, run, "moves", move="e7e5")
    clock(monkeypatch, NOW + timedelta(days=1, seconds=1))
    daily = client.get("/v1/puzzles/daily", headers=auth).json()
    assert daily["overdue_count"] == 1 and daily["actual_mix"]["due"] == 1
    review = _start(client, auth)
    assert not review["rated"] and review["selection_reason"] == "due"
    review = _action(client, auth, review, "hints", step=1).json()
    _action(client, auth, review, "moves", move="e7e5")
    db_session.expire_all()
    schedule = db_session.exec(select(ReviewSchedule)).one()
    assert schedule.retired_at is None and schedule.ease_factor == pytest.approx(2.1)
    assert len(db_session.exec(select(PuzzleRatingEvent)).all()) == 1
    clock(monkeypatch, NOW + timedelta(days=3))
    review = _start(client, auth)
    solved = _action(client, auth, review, "moves", move="e7e5").json()
    assert solved["daily"]["overdue_count"] == 0
    db_session.expire_all()
    schedule = db_session.exec(select(ReviewSchedule)).one()
    assert schedule.retirement_reason == "puzzle_review_passed" and schedule.retired_at
    clock(monkeypatch, NOW + timedelta(days=5))
    assert client.post("/v1/puzzles/runs", headers=auth).json()["status"] == "exhausted"


def test_legacy_active_run_is_unrated_and_unscheduled(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    seed(db_session, "legacy")
    study = StudySession(player_id="puzzle-player", domain="puzzles")
    db_session.add(study)
    db_session.flush()
    from scan64.content.models import ContentItem

    item = db_session.get(ContentItem, "lichess:legacy")
    run = PuzzleRun(
        player_id="puzzle-player",
        item_id=item.id,
        session_id=study.id,
        fen=item.payload["lesson"]["source"]["fen"],
    )
    db_session.add(run)
    db_session.commit()
    safe = _start(client, auth)
    assert not safe["rated"] and safe["selection_reason"] is None
    safe = _action(client, auth, safe, "hints", step=1).json()
    _action(client, auth, safe, "moves", move="e7e5")
    assert not db_session.exec(select(PuzzleRatingEvent)).all()
    assert not db_session.exec(select(ReviewSchedule)).all()


def test_daily_mix_bounded_and_neutral_routing(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    db_session.add(
        SkillState(
            player_id="puzzle-player",
            concept_code="threat_processing.missed_capture",
            alpha=1,
            beta=4,
        )
    )
    db_session.add(
        SkillState(player_id="puzzle-player", concept_code="threat_processing.missed_check")
    )
    db_session.add(
        SkillState(
            player_id="puzzle-player",
            concept_code="threat_processing.missed_direct_threat",
            alpha=1,
            beta=9,
            retired_at=NOW,
        )
    )
    db_session.commit()
    for index in range(8):
        seed(db_session, f"due{index}", 1800, sequence=index)
        study = StudySession(player_id="puzzle-player", domain="puzzles")
        db_session.add(study)
        db_session.flush()
        db_session.add(
            PuzzleRun(
                player_id="puzzle-player",
                item_id=f"lichess:due{index}",
                session_id=study.id,
                fen="unused legacy terminal",
                state="abandoned",
                completed_at=NOW,
            )
        )
        db_session.add(
            ReviewSchedule(
                player_id="puzzle-player",
                item_id=f"lichess:due{index}",
                next_review_at=NOW - timedelta(days=8 - index),
            )
        )
        db_session.commit()
    for index in range(40):
        seed(db_session, f"weak{index}", 1200 + index, ["hangingPiece"], index)
        seed(db_session, f"explore{index}", 1200 + index, ["mateIn1", "unknown"], index)
    statements: list[str] = []

    def capture(
        _conn: Any, _cursor: Any, statement: str, _params: Any, _context: Any, _executemany: Any
    ) -> None:
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", capture)
    try:
        summary = client.get("/v1/puzzles/daily", headers=auth).json()
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture)
    assert summary["actual_mix"] == {"due": 4, "weak": 5, "exploration": 1}
    assert summary["overdue_count"] == 8
    slots = db_session.exec(select(PuzzleDailySlot).order_by(PuzzleDailySlot.position)).all()
    assert [slot.item_id for slot in slots[:4]] == [f"lichess:due{i}" for i in range(4)]
    assert {slot.item_id for slot in slots if slot.reason == "weak"} == {
        f"lichess:weak{i}" for i in range(5)
    }
    catalog_queries = [sql for sql in statements if "FROM contentitem" in sql]
    assert catalog_queries and all("LIMIT" in sql for sql in catalog_queries)
    assert not any(
        slot.item_id.startswith("lichess:weak") and slot.reason == "exploration" for slot in slots
    )


def test_widening_exploration_floor_and_underfilled_day(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    db_session.add(
        SkillState(
            player_id="puzzle-player", concept_code="board_awareness.hanging_piece", alpha=1, beta=3
        )
    )
    db_session.commit()
    seed(db_session, "near", 1200, ["hangingPiece"])
    seed(db_session, "wide", 1500, ["hangingPiece"])
    seed(db_session, "catalog", 1800, ["hangingPiece"])
    seed(db_session, "unknown", 1800, ["unmapped"])
    summary = client.get("/v1/puzzles/daily", headers=auth).json()
    assert summary["total"] == 4 and "limited eligible supply" in summary["message"]
    slots = db_session.exec(select(PuzzleDailySlot)).all()
    assert {slot.item_id: slot.widening for slot in slots} == {
        "lichess:near": "200",
        "lichess:wide": "400",
        "lichess:catalog": "catalog",
        "lichess:unknown": "catalog",
    }
    assert summary["actual_mix"] == {"due": 0, "weak": 3, "exploration": 1}
    assert practice.THEME_SKILLS["hangingPiece"] == frozenset(
        {"board_awareness.hanging_piece", "threat_processing.missed_capture"}
    )


def test_twenty_rated_results_end_provisional_without_changing_profile(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = _auth(client)
    for index in range(20):
        seed(db_session, f"p{index}", sequence=index)
    expected = 1200.0
    for index in range(20):
        clock(monkeypatch, NOW + timedelta(days=index // 10))
        run = _start(client, auth)
        result = _action(client, auth, run, "moves", move="e7e5").json()
        expected = elo_after(expected, 1200, 1)
        assert result["daily"]["provisional"] == (index < 19)
    db_session.expire_all()
    rating = db_session.get(PuzzleRating, "puzzle-player")
    assert rating.rating == pytest.approx(expected) and rating.rated_count == 20
    assert len(db_session.exec(select(PuzzleRatingEvent)).all()) == 20
    assert len(db_session.exec(select(PuzzleDailySet)).all()) == 2
    assert not db_session.exec(select(SkillState)).all()


def test_guarded_rating_write_rolls_back_terminal_attempt_and_slot(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    seed(db_session, "guard")
    run = _start(client, auth)
    execute = db_session.execute

    class LostRace:
        def scalar_one_or_none(self) -> None:
            return None

    def race(statement: Any, *args: Any, **kwargs: Any) -> Any:
        if getattr(getattr(statement, "table", None), "name", None) == "puzzlerating":
            return LostRace()
        return execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", race)
    response = _action(client, auth, run, "moves", move="e7e5")
    assert response.status_code == 409
    db_session.expire_all()
    assert db_session.get(PuzzleRun, run["run_id"]).state == "active"
    assert db_session.exec(select(PuzzleDailySlot)).one().closed_at is None
    assert not db_session.exec(select(ContentAttempt)).all()
    assert not db_session.exec(select(PuzzleRatingEvent)).all()


def test_daily_summary_is_authenticated_and_player_scoped(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock(monkeypatch)
    assert client.get("/v1/puzzles/daily").status_code == 401
    owner = _auth(client, "daily-owner")
    other = _auth(client, "daily-other")
    seed(db_session, "owned")
    run = _start(client, owner)
    _action(client, owner, run, "abandon")
    summary = client.get("/v1/puzzles/daily", headers=other).json()
    assert summary["rating"] == 1200 and summary["rated_count"] == 0
    assert summary["completed"] == 0 and summary["remaining"] == 1


@pytest.mark.parametrize(
    "code,theme,beta,retired,expected",
    [
        ("threat_processing.missed_check", "mateIn1", 4, False, "weak"),
        ("threat_processing.missed_direct_threat", "defensiveMove", 4, False, "weak"),
        ("threat_processing.missed_check", "mateIn1", 1, False, "exploration"),
        ("threat_processing.missed_check", "mateIn1", 4, True, "exploration"),
        ("untrusted.fake", "mateIn1", 10, False, "exploration"),
        ("threat_processing.missed_check", "unknown", 10, False, "exploration"),
    ],
)
def test_theme_routing_is_trusted_nonretired_above_neutral_only(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    theme: str,
    beta: int,
    retired: bool,
    expected: str,
) -> None:
    clock(monkeypatch)
    auth = _auth(client)
    db_session.add(
        SkillState(
            player_id="puzzle-player",
            concept_code=code,
            alpha=1,
            beta=beta,
            retired_at=NOW if retired else None,
        )
    )
    db_session.commit()
    seed(db_session, "theme", themes=[theme])
    run = _start(client, auth)
    assert run["selection_reason"] == expected
    if expected == "weak":
        assert "Exploration shortfall" in run["daily"]["message"]

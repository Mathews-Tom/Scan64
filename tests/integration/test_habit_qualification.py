from copy import deepcopy

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import ContentAttempt
from scan64.content.puzzle_runs import matches_completed_solution
from scan64.learning.habit_models import HabitCredit
from tests.integration.test_habit_api import _owner
from tests.integration.test_lesson_attempts import create_persisted_lesson, create_study_session
from tests.integration.test_puzzle_lifecycle import puzzle_item, seed_puzzle_run


def _lesson(session: Session, player_id: str) -> tuple[str, str]:
    opportunity = create_persisted_lesson(session, player_id)
    game = session.get(Game, opportunity.game_id)
    game.owner_color = "white"
    game.moves = ["e2e4"]
    analysis = EngineAnalysis(
        position_id=opportunity.source_position_id,
        raw_result={"score": {"cp": 0}, "pv": ["e2e4"]},
    )
    session.add(analysis)
    session.flush()
    spec = deepcopy(opportunity.lesson_spec)
    spec["source"]["kind"] = "player_game"
    position = session.get(Position, opportunity.source_position_id)
    position.canonical_id = chess.Board(position.fen).epd()
    session.add(position)
    opportunity.lesson_spec = spec
    opportunity.verification_status = "verified"
    opportunity.verification_analysis_id = analysis.id
    session.add_all([opportunity, game])
    study = create_study_session(session, player_id)
    session.commit()
    return str(opportunity.id), study.id


def _submit(
    client: TestClient,
    headers: dict[str, str],
    lesson: tuple[str, str],
    move: str = "e2e4",
    hints: int = 0,
):
    return client.post(
        "/v1/learning/lesson-attempts",
        headers=headers,
        json={
            "session_id": lesson[1],
            "lesson_id": lesson[0],
            "source_kind": "persisted_opportunity",
            "submitted_move": move,
            "hints_used": hints,
            "elapsed_ms": 100,
        },
    )


@pytest.mark.parametrize("legacy_labels", [False, True])
def test_first_server_graded_lesson_success_credits_once_and_epd_dedupes(
    client: TestClient, db_session: Session, legacy_labels: bool
) -> None:
    headers = _owner(client)
    lesson = _lesson(db_session, "habit-api-owner")
    if legacy_labels:
        from uuid import UUID

        from scan64.chess.analysis.models import PersistedLessonOpportunity

        opportunity = db_session.get(PersistedLessonOpportunity, UUID(lesson[0]))
        spec = deepcopy(opportunity.lesson_spec)
        spec["source"]["kind"] = "custom"
        opportunity.lesson_spec = spec
        position = db_session.get(Position, opportunity.source_position_id)
        position.canonical_id = "initial"
        db_session.add_all([opportunity, position])
        db_session.commit()
    first = _submit(client, headers, lesson)
    assert first.status_code == 200 and first.json()["success"] is True, first.text
    credits = db_session.exec(select(HabitCredit)).all()
    assert len(credits) == 1 and credits[0].source_kind == "lesson"
    second = _submit(client, headers, lesson)
    assert second.status_code == 200
    duplicate = _submit(client, headers, _lesson(db_session, "habit-api-owner"))
    assert duplicate.status_code == 200
    assert len(db_session.exec(select(HabitCredit)).all()) == 1


@pytest.mark.parametrize("first_move,hints", [("d2d4", 0), ("e2e4", 1)])
def test_wrong_first_or_app_reported_hint_precludes_lesson_credit(
    client: TestClient, db_session: Session, first_move: str, hints: int
) -> None:
    headers = _owner(client)
    lesson = _lesson(db_session, "habit-api-owner")
    first = _submit(client, headers, lesson, first_move, hints)
    assert first.status_code == 200, first.text
    second = _submit(client, headers, lesson)
    assert second.status_code == 200, second.text
    assert db_session.exec(select(HabitCredit)).all() == []


def test_foreign_lesson_or_client_claimed_success_never_credits(
    client: TestClient, db_session: Session
) -> None:
    headers = _owner(client)
    foreign = _owner(client, "habit-api-other")
    lesson = _lesson(db_session, "habit-api-owner")
    assert _submit(client, foreign, lesson).status_code == 404
    forged = client.post(
        "/v1/learning/lesson-attempts",
        headers=headers,
        json={
            "session_id": lesson[1],
            "lesson_id": lesson[0],
            "source_kind": "persisted_opportunity",
            "submitted_move": "d2d4",
            "hints_used": 0,
            "elapsed_ms": 100,
            "success": True,
        },
    )
    assert forged.status_code == 422
    assert db_session.exec(select(HabitCredit)).all() == []


def test_terminal_puzzle_credits_inside_outcome_transaction_and_replay_does_not(
    client: TestClient, db_session: Session
) -> None:
    headers = _owner(client)
    db_session.add(puzzle_item())
    db_session.commit()
    run = seed_puzzle_run(db_session, "habit-api-owner")
    path = f"/v1/puzzles/runs/{run.id}/moves"
    body = {"move": "e7e5", "expected_cursor": 0, "expected_revision": 0}
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "solved"
    db_session.expire_all()
    assert db_session.get(ContentAttempt, run.id).success is True
    assert len(db_session.exec(select(HabitCredit)).all()) == 1
    assert client.post(path, headers=headers, json=body).status_code == 409
    assert len(db_session.exec(select(HabitCredit)).all()) == 1


@pytest.mark.parametrize("hinted", [False, True])
def test_alternative_final_checkmate_credits_and_roundtrips_without_weakening_hint_rules(
    client: TestClient,
    db_session: Session,
    hinted: bool,
) -> None:
    headers = _owner(client)
    item = puzzle_item()
    initial = chess.Board("8/5K1k/6Q1/8/8/8/8/8 b - - 0 1")
    source = initial.copy()
    source.push_uci("h7h8")
    payload = deepcopy(item.payload)
    payload.update(fen=initial.fen(), setup="h7h8", moves=["h7h8", "g6g7"])
    payload["lesson"]["source"]["fen"] = source.fen()
    payload["lesson"]["interaction"]["solution"] = ["g6g7"]
    item.payload = payload
    db_session.add(item)
    db_session.commit()
    run = seed_puzzle_run(db_session, "habit-api-owner")
    if hinted:
        hint = client.post(
            f"/v1/puzzles/runs/{run.id}/hints",
            headers=headers,
            json={"expected_cursor": 0, "expected_revision": 0, "step": 1},
        )
        assert hint.status_code == 200, hint.text
    solved = client.post(
        f"/v1/puzzles/runs/{run.id}/moves",
        headers=headers,
        json={"move": "g6h6", "expected_cursor": 0, "expected_revision": int(hinted)},
    )
    assert solved.status_code == 200, solved.text
    assert solved.json()["state"] == "solved"
    assert solved.json()["played_moves"] == ["g6h6"]
    assert chess.Board(solved.json()["fen"]).is_checkmate()
    expected_credits = 0 if hinted else 1
    assert len(db_session.exec(select(HabitCredit)).all()) == expected_credits
    exported = client.post("/v1/exports", headers=headers, json={"player_id": "habit-api-owner"})
    assert exported.status_code == 200, exported.text
    deleted = client.request(
        "DELETE",
        "/v1/players/habit-api-owner/data",
        headers=headers,
        json={"dry_run": False, "confirmation": "delete-habit-api-owner"},
    )
    assert deleted.status_code == 200, deleted.text
    restored = client.post("/v1/imports", headers=headers, json=exported.json())
    assert restored.status_code == 200, restored.text
    assert len(db_session.exec(select(HabitCredit)).all()) == expected_credits


@pytest.mark.parametrize(
    "played,solution,expected",
    [
        (["g6g7"], ["g6g7"], True),
        (["g6h6"], ["g6g7"], True),
        (["g6g5"], ["g6g7"], False),
        (["g6h6"], ["g6g5", "h8h7", "g5h6"], False),
        (["g6g5", "h8h7", "g5h5"], ["g6g5", "h8h7", "g5h4"], True),
        (["g6g5", "h8h7", "g5h5"], ["g6g4", "h8h7", "g4h4"], False),
    ],
)
def test_puzzle_completion_requires_the_exact_prefix_and_only_allows_final_mate(
    played: list[str],
    solution: list[str],
    expected: bool,
) -> None:
    board = chess.Board("7k/5K2/6Q1/8/8/8/8/8 w - - 1 2")
    for move in played:
        board.push_uci(move)
    assert matches_completed_solution(played, solution, board) is expected


def test_server_revealed_puzzle_hint_excludes_even_if_client_claims_unhinted(
    client: TestClient, db_session: Session
) -> None:
    headers = _owner(client)
    run = seed_puzzle_run(db_session, "habit-api-owner")
    hinted = client.post(
        f"/v1/puzzles/runs/{run.id}/hints",
        headers=headers,
        json={
            "expected_cursor": 0,
            "expected_revision": 0,
            "step": 1,
        },
    )
    assert hinted.status_code == 200, hinted.text
    solved = client.post(
        f"/v1/puzzles/runs/{run.id}/moves",
        headers=headers,
        json={
            "move": "e7e5",
            "expected_cursor": 0,
            "expected_revision": 1,
        },
    )
    assert solved.status_code == 200, solved.text
    assert db_session.exec(select(HabitCredit)).all() == []


@pytest.mark.parametrize("retirement", ["schedule", "skill", "verification", "trust"])
def test_retired_invalid_or_invisible_lesson_cannot_earn_credit(
    client: TestClient, db_session: Session, retirement: str
) -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    from scan64.chess.analysis.models import PersistedLessonOpportunity
    from scan64.learning.profiling.models import SkillState
    from scan64.learning.scheduling.spaced_repetition import ReviewSchedule

    headers = _owner(client)
    lesson = _lesson(db_session, "habit-api-owner")
    opportunity = db_session.get(PersistedLessonOpportunity, UUID(lesson[0]))
    if retirement == "schedule":
        schedule = db_session.get(ReviewSchedule, ("habit-api-owner", lesson[0]))
        schedule.retired_at = datetime.now(UTC)
        schedule.retirement_reason = "reviewed"
        db_session.add(schedule)
    elif retirement == "skill":
        db_session.add(
            SkillState(
                player_id="habit-api-owner",
                concept_code="threat_processing.missed_capture",
                retired_at=datetime.now(UTC),
                retirement_reason="reviewed",
            )
        )
    elif retirement == "verification":
        opportunity.verification_status = "invalid"
        db_session.add(opportunity)
    else:
        spec = deepcopy(opportunity.lesson_spec)
        spec["diagnosis"]["evidence_refs"] = []
        opportunity.lesson_spec = spec
        db_session.add(opportunity)
    db_session.commit()
    response = _submit(client, headers, lesson)
    assert response.status_code in (200, 409), response.text
    assert db_session.exec(select(HabitCredit)).all() == []


def test_exhausted_lesson_cannot_create_reward_from_fourth_submission(
    client: TestClient, db_session: Session
) -> None:
    headers = _owner(client)
    lesson = _lesson(db_session, "habit-api-owner")
    for _ in range(3):
        assert _submit(client, headers, lesson, "d2d4").status_code == 200
    assert _submit(client, headers, lesson).status_code == 409
    assert db_session.exec(select(HabitCredit)).all() == []


def test_puzzle_outcome_and_habit_credit_roll_back_together(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scan64.content.puzzle_runs import PuzzleRun

    headers = _owner(client)
    run = seed_puzzle_run(db_session, "habit-api-owner")
    run_id = run.id

    def fail_commit() -> None:
        raise RuntimeError("injected habit transaction failure")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected habit transaction failure"):
            client.post(
                f"/v1/puzzles/runs/{run_id}/moves",
                headers=headers,
                json={
                    "move": "e7e5",
                    "expected_cursor": 0,
                    "expected_revision": 0,
                },
            )
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(PuzzleRun, run_id).state == "active"
    assert db_session.get(ContentAttempt, run_id) is None
    assert db_session.exec(select(HabitCredit)).all() == []

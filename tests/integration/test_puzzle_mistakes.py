from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import chess
import pytest
from chess_lesson_spec import LessonSpec
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.api.game_review as game_review_api
import scan64.api.learning as learning_api
import scan64.api.puzzle_mistakes as mistakes_api
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.analysis.review import stage_game_review
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import ContentAttempt, ContentItem, LessonAttempt, StudySession
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION
from scan64.learning.evidence.models import Evidence
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule

PLAYER = "mistakes-owner"
SKILL = "threat_processing.missed_capture"
PATH = "/v1/puzzles/mistakes"


def _register(client: TestClient, player_id: str = PLAYER) -> dict[str, str]:
    response = client.post("/v1/players", json={"id": player_id})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _lesson(
    session: Session,
    *,
    player_id: str = PLAYER,
    maximum_attempts: int = 3,
    moves: list[str] | None = None,
    ply: int = 1,
    owner_color: Literal["white", "black"] = "white",
    best_move: str = "e2e4",
    created_at: datetime | None = None,
) -> PersistedLessonOpportunity:
    game = Game(
        pgn="",
        moves=moves if moves is not None else ["d2d4"],
        owner_player_id=player_id,
        owner_color=owner_color,
    )
    session.add(game)
    evaluations = [
        EngineAnalysis(raw_result=[{"score_cp": 200}]) for _ in range(len(game.moves) + 1)
    ]
    positions: dict[str, Position] = {}
    analyses: dict[UUID, EngineAnalysis] = {}
    rows = stage_game_review(game, evaluations, positions, analyses, set())
    session.add_all(positions.values())
    session.add_all(analyses.values())
    session.add_all(rows)
    source = next(
        position
        for position in positions.values()
        if position.id == rows[ply - 1].source_position_id
    )
    board = chess.Board(source.fen)
    accepted = board.san(chess.Move.from_uci(best_move))
    proof = EngineAnalysis(
        position_id=source.id,
        raw_result=[{"pv": [best_move], "score_cp": 200}],
        config={"nodes": 100000},
    )
    evidence = Evidence(
        evidence_id=f"mistake-proof-{source.id}",
        kind="missed_opportunity",
        position_id=str(source.id),
        engine_analysis_id=str(proof.id),
        claim="Private diagnosis evidence must never appear in list responses",
        payload={"diagnosis_rule_version": DIAGNOSIS_RULE_VERSION},
    )
    lesson = PersistedLessonOpportunity(
        game_id=game.id,
        player_id=player_id,
        source_position_id=source.id,
        verification_analysis_id=proof.id,
        verification_status="verified",
        created_at=created_at if created_at is not None else datetime.now(UTC),
        lesson_spec={
            "schema_version": "0.2.0",
            "lesson_id": "generated-private-lesson",
            "source": {"kind": "player_game", "fen": source.fen},
            "diagnosis": {
                "primary": SKILL,
                "secondary": ["calculation.stopped_too_early"],
                "confidence": 1.0,
                "evidence_refs": [evidence.evidence_id],
            },
            "objective": {"type": "find_best_move", "instruction": "Private lesson instruction"},
            "interaction": {
                "input": "move",
                "maximum_attempts": maximum_attempts,
                "accepted_moves": [{"san": accepted}],
            },
            "verification": {"status": "verified", "engine": "recorded-fixture"},
            "hints": [],
            "explanation": {"text": "Private explanation"},
        },
    )
    session.add_all([proof, evidence, lesson])
    session.add(
        ReviewSchedule(
            player_id=player_id,
            item_id=str(lesson.id),
            skill_id=SKILL,
            next_review_at=datetime(2026, 12, 1, tzinfo=UTC),
        )
    )
    session.commit()
    return lesson


def _attempts(
    session: Session,
    lesson: PersistedLessonOpportunity,
    count: int,
    *,
    player_id: str = PLAYER,
) -> None:
    # Different sessions and outcomes must all share the same lifetime budget.
    for index in range(count):
        study = StudySession(player_id=player_id, domain=f"old-training:{index}")
        session.add(study)
        session.add(
            LessonAttempt(
                player_id=player_id,
                session_id=study.id,
                lesson_id=str(lesson.id),
                opportunity_id=lesson.id,
                source_kind="persisted_opportunity",
                elapsed_ms=100,
                hints_used=index % 2,
                success=index % 2 == 0,
                grading_status="verified",
                profile_update_result="applied",
            )
        )
    session.commit()


def _schedule(session: Session, lesson: PersistedLessonOpportunity) -> ReviewSchedule:
    schedule = session.get(ReviewSchedule, (lesson.player_id, str(lesson.id)))
    assert schedule is not None
    return schedule


def _forbidden(*_: Any, **__: Any) -> Any:
    raise AssertionError("Mistake discovery or retry admission must never run engine analysis")


def _snapshot(session: Session) -> dict[str, list[dict[str, Any]]]:
    models = [
        PersistedLessonOpportunity,
        ReviewSchedule,
        StudySession,
        LessonAttempt,
        ContentItem,
        ContentAttempt,
        EngineAnalysis,
        SkillState,
    ]
    return {
        model.__name__: [row.model_dump(mode="json") for row in session.exec(select(model)).all()]
        for model in models
    }


def test_list_is_engine_free_write_free_and_spoiler_free(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _register(client)
    lesson = _lesson(db_session)
    _attempts(db_session, lesson, 1)
    before = _snapshot(db_session)
    monkeypatch.setattr(mistakes_api, "_reverify_persisted_lesson", _forbidden)
    monkeypatch.setattr(game_review_api, "_key_moments", _forbidden)
    monkeypatch.setattr(game_review_api, "build_game_review", _forbidden)
    monkeypatch.setattr(learning_api.StockfishAdapter, "analyze_position", _forbidden)
    monkeypatch.setattr(learning_api, "analyze_lesson_source", _forbidden)

    response = client.get(PATH, headers=auth)
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "lesson_id": str(lesson.id),
                "game_id": str(lesson.game_id),
                "ply": 1,
                "attempts_remaining": 2,
                "diagnosis": SKILL,
            }
        ],
        "excluded": {"no_review_schedule": 0, "retired": 0, "untrusted": 0, "exhausted": 0},
        "offset": 0,
        "limit": 20,
        "has_more": False,
    }
    assert _snapshot(db_session) == before
    assert "Private" not in response.text
    assert "secondary" not in response.text
    assert "evidence_refs" not in response.text
    assert "accepted_moves" not in response.text
    assert "fen" not in response.text


def test_list_exclusions_are_page_local_reason_counts_without_hidden_diagnoses(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    eligible = _lesson(db_session)
    missing = _lesson(db_session)
    retired = _lesson(db_session)
    untrusted = _lesson(db_session)
    exhausted = _lesson(db_session)
    db_session.delete(_schedule(db_session, missing))
    schedule = _schedule(db_session, retired)
    schedule.retired_at = datetime.now(UTC)
    schedule.retirement_reason = "Private retirement diagnosis"
    db_session.add(schedule)
    untrusted.lesson_spec = {
        **untrusted.lesson_spec,
        "diagnosis": {"primary": "private.untrusted", "confidence": 1.0},
    }
    db_session.add(untrusted)
    _attempts(db_session, exhausted, 3)
    foreign_auth = _register(client, "foreign-mistakes")
    foreign = _lesson(db_session, player_id="foreign-mistakes")

    response = client.get(PATH, headers=auth)
    assert response.status_code == 200
    assert [item["lesson_id"] for item in response.json()["items"]] == [str(eligible.id)]
    assert response.json()["excluded"] == {
        "no_review_schedule": 1,
        "retired": 1,
        "untrusted": 1,
        "exhausted": 1,
    }
    assert "private" not in response.text.lower()
    assert str(foreign.id) not in response.text
    foreign_list = client.get(PATH, headers=foreign_auth).json()
    assert [item["lesson_id"] for item in foreign_list["items"]] == [str(foreign.id)]
    assert not any(foreign_list["excluded"].values())


def test_list_pages_bounded_candidates_before_visibility_and_can_have_empty_items(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _register(client)
    lessons = [
        _lesson(db_session, created_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(days=index))
        for index in range(5)
    ]
    visible = mistakes_api.visible_game_lesson
    calls = []

    def counted(opportunity: PersistedLessonOpportunity, session: Session) -> LessonSpec | None:
        calls.append(opportunity.id)
        return visible(opportunity, session)

    monkeypatch.setattr(mistakes_api, "visible_game_lesson", counted)
    pages = [
        client.get(PATH, params={"offset": offset, "limit": 2}, headers=auth).json()
        for offset in (0, 2, 4)
    ]
    assert [page["has_more"] for page in pages] == [True, True, False]
    assert [page["offset"] for page in pages] == [0, 2, 4]
    assert all(page["limit"] == 2 for page in pages)
    expected = [str(lesson.id) for lesson in reversed(lessons)]
    assert [item["lesson_id"] for page in pages for item in page["items"]] == expected
    assert [str(lesson_id) for lesson_id in calls] == expected
    for lesson in lessons[-2:]:
        db_session.delete(_schedule(db_session, lesson))
    db_session.commit()
    calls.clear()
    empty = client.get(PATH, params={"limit": 2}, headers=auth).json()
    assert empty["items"] == []
    assert empty["excluded"] == {
        "no_review_schedule": 2,
        "retired": 0,
        "untrusted": 0,
        "exhausted": 0,
    }
    assert empty["has_more"]
    assert calls == []
    beyond = client.get(PATH, params={"offset": 5}, headers=auth).json()
    assert beyond["items"] == []
    assert not beyond["has_more"]
    assert not any(beyond["excluded"].values())


@pytest.mark.parametrize("params", [{"offset": -1}, {"limit": 0}, {"limit": 51}])
def test_list_rejects_unbounded_or_negative_pagination(
    client: TestClient, params: dict[str, int]
) -> None:
    auth = _register(client)
    assert client.get(PATH, params=params, headers=auth).status_code == 422
    assert client.get(PATH, params={"limit": 50}, headers=auth).status_code == 200


def test_mistake_routes_require_auth_and_share_owned_not_found(
    client: TestClient, db_session: Session
) -> None:
    owner = _register(client)
    foreign = _register(client, "not-the-owner")
    lesson = _lesson(db_session)
    start = f"{PATH}/{lesson.id}/start"
    assert client.get(PATH).status_code == 401
    assert client.post(start).status_code == 401
    assert client.get(PATH, headers={"Authorization": "Bearer unknown"}).status_code == 401
    denied = client.post(start, headers=foreign)
    missing = client.post(f"{PATH}/{uuid4()}/start", headers=owner)
    malformed = client.post(f"{PATH}/not-a-lesson/start", headers=owner)
    assert denied.status_code == missing.status_code == malformed.status_code == 404
    assert denied.json() == missing.json() == malformed.json()
    assert client.get(PATH, headers=foreign).json()["items"] == []
    game = db_session.get(Game, lesson.game_id)
    assert game is not None
    game.owner_player_id = "not-the-owner"
    db_session.add(game)
    db_session.commit()
    assert client.get(PATH, headers=owner).json()["items"] == []
    assert client.post(start, headers=owner).json() == missing.json()
    assert client.post(start, headers=foreign).json() == missing.json()


@pytest.mark.parametrize("maximum_attempts", [1, 3, 5])
def test_start_reverifies_once_reuses_review_session_and_preserves_lifetime_assisted_grading(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    maximum_attempts: int,
) -> None:
    auth = _register(client)
    lesson = _lesson(db_session, maximum_attempts=maximum_attempts)
    _attempts(db_session, lesson, maximum_attempts - 1)
    old = StudySession(
        player_id=PLAYER,
        domain=f"game_analysis:{lesson.game_id}",
        started_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    latest = StudySession(
        player_id=PLAYER,
        domain=f"game_analysis:{lesson.game_id}",
        started_at=datetime(2026, 11, 2, tzinfo=UTC),
    )
    db_session.add_all([old, latest])
    db_session.commit()
    schedule_before = _schedule(db_session, lesson).model_dump(mode="json")
    snapshot_before = _snapshot(db_session)
    reverify = mistakes_api._reverify_persisted_lesson
    calls = []

    def counted(
        opportunity: PersistedLessonOpportunity, session: Session, *, allow_fallback: bool
    ) -> tuple[LessonSpec | None, bool]:
        calls.append((opportunity.id, allow_fallback))
        return reverify(opportunity, session, allow_fallback=allow_fallback)

    monkeypatch.setattr(mistakes_api, "_reverify_persisted_lesson", counted)
    monkeypatch.setattr(learning_api.StockfishAdapter, "analyze_position", _forbidden)
    monkeypatch.setattr(learning_api, "analyze_lesson_source", _forbidden)
    response = client.post(f"{PATH}/{lesson.id}/start", headers=auth)
    assert response.status_code == 200
    started = response.json()
    assert calls == [(lesson.id, False)]
    assert started["session_id"] == latest.id
    assert started["moment"]["ply"] == 1
    assert started["moment"]["lesson_id"] == str(lesson.id)
    assert started["moment"]["lesson"]["lesson_id"] == str(lesson.id)
    assert started["moment"]["lesson"]["diagnosis"]["secondary"] == []
    assert len(started["moment"]["lesson"]["hints"]) == 3
    assert started["moment"]["attempts_used"] == maximum_attempts - 1
    assert started["moment"]["attempts_remaining"] == 1
    assert _schedule(db_session, lesson).model_dump(mode="json") == schedule_before
    snapshot_after = _snapshot(db_session)
    for name in snapshot_before:
        if name != "PersistedLessonOpportunity":
            assert snapshot_after[name] == snapshot_before[name]
    resumed = client.post(f"{PATH}/{lesson.id}/start", headers=auth).json()
    assert resumed["session_id"] == latest.id
    assert resumed["moment"]["attempts_remaining"] == 1
    review = client.get(f"/v1/games/{lesson.game_id}/review", headers=auth).json()
    assert review["session_id"] == latest.id
    assert review["key_moments"][0]["attempts_remaining"] == 1
    attempt = {
        "session_id": started["session_id"],
        "lesson_id": str(lesson.id),
        "source_kind": "persisted_opportunity",
        "submitted_move": "e2e4",
        "elapsed_ms": 100,
        "hints_used": 1,
    }
    graded = client.post("/v1/learning/lesson-attempts", json=attempt, headers=auth)
    assert graded.status_code == 200
    assert graded.json()["success"]
    assert graded.json()["grading_status"] == "verified"
    state = db_session.get(SkillState, (PLAYER, SKILL))
    assert state is not None
    assert state.alpha == pytest.approx(state.prior_alpha + 0.5)
    assert state.beta == pytest.approx(state.prior_beta)
    assert (
        client.post("/v1/learning/lesson-attempts", json=attempt, headers=auth).status_code == 409
    )
    exhausted = client.post(f"{PATH}/{lesson.id}/start", headers=auth)
    assert exhausted.status_code == 409
    assert exhausted.json()["detail"] == "Maximum attempts reached for persisted lesson"
    listed = client.get(PATH, headers=auth).json()
    assert listed["items"] == []
    assert listed["excluded"]["exhausted"] == 1


def test_start_creates_game_analysis_session_and_returns_black_source_game_ply(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    lesson = _lesson(
        db_session,
        moves=["e2e4", "d7d5", "e4d5"],
        ply=2,
        owner_color="black",
        best_move="c7c5",
    )
    _attempts(db_session, lesson, 2, player_id="unrelated-player")
    before = _schedule(db_session, lesson).model_dump(mode="json")
    listed = client.get(PATH, headers=auth).json()["items"]
    assert listed == [
        {
            "lesson_id": str(lesson.id),
            "game_id": str(lesson.game_id),
            "ply": 2,
            "attempts_remaining": 3,
        }
    ]
    response = client.post(f"{PATH}/{lesson.id}/start", headers=auth)
    assert response.status_code == 200
    assert response.json()["moment"]["ply"] == 2
    assert response.json()["moment"]["attempts_used"] == 0
    study = db_session.get(StudySession, response.json()["session_id"])
    assert study is not None
    assert study.player_id == PLAYER
    assert study.domain == f"game_analysis:{lesson.game_id}"
    assert _schedule(db_session, lesson).model_dump(mode="json") == before


@pytest.mark.parametrize("change", ["no_review_schedule", "retired", "untrusted", "exhausted"])
def test_start_rechecks_listed_visibility_and_budget_before_reverification(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    auth = _register(client)
    lesson = _lesson(db_session)
    assert client.get(PATH, headers=auth).json()["items"]
    if change == "no_review_schedule":
        db_session.delete(_schedule(db_session, lesson))
    elif change == "retired":
        schedule = _schedule(db_session, lesson)
        schedule.retired_at = datetime.now(UTC)
        db_session.add(schedule)
    elif change == "untrusted":
        evidence = db_session.get(Evidence, lesson.lesson_spec["diagnosis"]["evidence_refs"][0])
        assert evidence is not None
        evidence.payload = {"diagnosis_rule_version": "obsolete"}
        db_session.add(evidence)
    else:
        _attempts(db_session, lesson, 3)
    db_session.commit()
    before = _snapshot(db_session)
    monkeypatch.setattr(mistakes_api, "_reverify_persisted_lesson", _forbidden)
    response = client.post(f"{PATH}/{lesson.id}/start", headers=auth)
    assert response.status_code == 409
    assert _snapshot(db_session) == before
    listed = client.get(PATH, headers=auth).json()
    assert listed["items"] == []
    assert listed["excluded"][change] == 1


@pytest.mark.parametrize(
    "change",
    [
        "unverified",
        "unavailable",
        "invalid",
        "missing_reference",
        "empty_analysis",
        "wrong_analysis_source",
        "malformed_spec",
        "wrong_source_fen",
        "missing_source_game",
    ],
)
def test_untrusted_stored_state_is_excluded_without_repair_or_fallback(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    auth = _register(client)
    lesson = _lesson(db_session)
    original_analysis_id = lesson.verification_analysis_id
    proof = db_session.get(EngineAnalysis, original_analysis_id)
    assert proof is not None
    # A newer valid analysis must not silently rescue a missing stored proof.
    db_session.add(
        EngineAnalysis(
            position_id=lesson.source_position_id,
            raw_result=[{"pv": ["e2e4"], "score_cp": 500}],
        )
    )
    if change in {"unverified", "unavailable", "invalid"}:
        lesson.verification_status = change
    elif change == "missing_reference":
        lesson.verification_analysis_id = None
    elif change == "empty_analysis":
        proof.raw_result = []
        db_session.add(proof)
    elif change == "wrong_analysis_source":
        proof.position_id = uuid4()
        db_session.add(proof)
    elif change == "malformed_spec":
        lesson.lesson_spec = {**lesson.lesson_spec, "interaction": {"maximum_attempts": "invalid"}}
    elif change == "wrong_source_fen":
        lesson.lesson_spec = {
            **lesson.lesson_spec,
            "source": {"kind": "player_game", "fen": "private-invalid-fen"},
        }
    else:
        source = db_session.get(Position, lesson.source_position_id)
        assert source is not None
        source.game_id = None
        db_session.add(source)
    db_session.add(lesson)
    db_session.commit()
    before = _snapshot(db_session)
    monkeypatch.setattr(mistakes_api, "_reverify_persisted_lesson", _forbidden)
    monkeypatch.setattr(learning_api, "analyze_lesson_source", _forbidden)
    response = client.get(PATH, headers=auth)
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["excluded"]["untrusted"] == 1
    assert "private" not in response.text
    assert client.post(f"{PATH}/{lesson.id}/start", headers=auth).status_code == 409
    assert _snapshot(db_session) == before


def test_opponent_source_cannot_be_offered_as_owned_mistake(
    client: TestClient, db_session: Session
) -> None:
    auth = _register(client)
    lesson = _lesson(
        db_session,
        moves=["e2e4", "d7d5"],
        ply=2,
        owner_color="white",
        best_move="c7c5",
    )
    listed = client.get(PATH, headers=auth).json()
    assert listed["items"] == []
    assert listed["excluded"]["untrusted"] == 1
    assert client.post(f"{PATH}/{lesson.id}/start", headers=auth).status_code == 409


def test_start_reverifies_only_on_explicit_start_and_persists_failed_verification(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _register(client)
    lesson = _lesson(db_session)
    lesson.lesson_spec = {
        **lesson.lesson_spec,
        "interaction": {**lesson.lesson_spec["interaction"], "accepted_moves": [{"san": "Qa8"}]},
    }
    db_session.add(lesson)
    db_session.commit()
    before = _schedule(db_session, lesson).model_dump(mode="json")
    monkeypatch.setattr(learning_api.StockfishAdapter, "analyze_position", _forbidden)
    monkeypatch.setattr(learning_api, "analyze_lesson_source", _forbidden)
    # Discovery trusts stored verification status; only Start audits the answer.
    assert client.get(PATH, headers=auth).json()["items"]
    response = client.post(f"{PATH}/{lesson.id}/start", headers=auth)
    assert response.status_code == 409
    db_session.refresh(lesson)
    assert lesson.verification_status == "invalid"
    assert lesson.verification_error
    assert _schedule(db_session, lesson).model_dump(mode="json") == before
    assert not db_session.exec(select(StudySession).where(StudySession.player_id == PLAYER)).all()
    listed = client.get(PATH, headers=auth)
    assert listed.json()["items"] == []
    assert listed.json()["excluded"]["untrusted"] == 1
    assert "Qa8" not in listed.text
    assert "verification_error" not in listed.text


def test_mistake_start_serializes_and_requires_finishing_active_puzzle_first(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _register(client)
    lesson = _lesson(db_session)
    item = ContentItem(domain="puzzles", provenance="recorded-fixture", licence="CC0")
    study = StudySession(player_id=PLAYER, domain="puzzles")
    active = PuzzleRun(
        player_id=PLAYER,
        item_id=item.id,
        session_id=study.id,
        fen=lesson.lesson_spec["source"]["fen"],
    )
    db_session.add_all([item, study, active])
    db_session.commit()
    lock = mistakes_api.lock_puzzle_player
    eligible = mistakes_api._eligible_mistake
    locks = []

    def counted_lock(player_id: str, session: Session) -> None:
        locks.append(player_id)
        lock(player_id, session)

    monkeypatch.setattr(mistakes_api, "lock_puzzle_player", counted_lock)
    before = _snapshot(db_session)
    assert client.get(PATH, headers=auth).json()["items"]
    assert locks == []
    assert _snapshot(db_session) == before
    monkeypatch.setattr(mistakes_api, "_eligible_mistake", _forbidden)
    response = client.post(f"{PATH}/{lesson.id}/start", headers=auth)
    assert response.status_code == 409
    assert "Finish or abandon" in response.json()["detail"]
    assert locks == [PLAYER]
    assert _snapshot(db_session) == before
    db_session.refresh(active)
    assert active.state == "active"
    active.state = "abandoned"
    db_session.add(active)
    db_session.commit()
    monkeypatch.setattr(mistakes_api, "_eligible_mistake", eligible)
    admitted = client.post(f"{PATH}/{lesson.id}/start", headers=auth)
    assert admitted.status_code == 200
    assert locks == [PLAYER, PLAYER]

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import Player, PlayerCredential, PlayerProfile, issue_player_token
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import LessonAttempt, StudySession
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.session_state import load_player_session_state
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule


def lesson_spec() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "lesson_id": "generated-lesson",
        "source": {
            "kind": "custom",
            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        },
        "diagnosis": {"primary": "tactics.fork", "confidence": 1.0},
        "objective": {"type": "find_best_move", "instruction": "Play e4."},
        "interaction": {
            "input": "click",
            "maximum_attempts": 3,
            "accepted_moves": [{"san": "e4"}],
        },
        "verification": {"status": "verified", "engine": "test"},
        "hints": [],
        "explanation": {"text": "Develop the centre."},
    }


def create_persisted_lesson(
    db_session: Session,
    player_id: str,
    *,
    skill_id: str | None = "tactics.fork",
    next_review_at: datetime | None = None,
    review_retired_at: datetime | None = None,
) -> PersistedLessonOpportunity:
    game = Game(pgn="", owner_player_id=player_id)
    db_session.add(game)
    db_session.flush()
    source_position = Position(
        game_id=game.id,
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="initial",
    )
    db_session.add(source_position)
    db_session.flush()
    db_session.add(
        EngineAnalysis(
            position_id=source_position.id,
            raw_result=[{"pv": ["e2e4", "e7e5"]}],
        )
    )
    opportunity = PersistedLessonOpportunity(
        game_id=game.id,
        source_position_id=source_position.id,
        player_id=player_id,
        lesson_spec=lesson_spec(),
    )
    db_session.add(opportunity)
    db_session.flush()
    db_session.add(
        ReviewSchedule(
            player_id=player_id,
            item_id=str(opportunity.id),
            skill_id=skill_id,
            next_review_at=next_review_at or datetime.now(UTC) + timedelta(days=1),
            retired_at=review_retired_at,
        )
    )
    db_session.commit()
    return opportunity


def set_skill_mastery(
    db_session: Session,
    player_id: str,
    concept_code: str,
    *,
    alpha: float,
    beta: float,
    retired_at: datetime | None = None,
) -> SkillState:
    skill = SkillState(
        player_id=player_id,
        concept_code=concept_code,
        alpha=alpha,
        beta=beta,
        retired_at=retired_at,
    )
    db_session.add(skill)
    db_session.commit()
    return skill


def authorize_new_player(
    client: TestClient, db_session: Session, player_id: str, *, rating: int = 1500
) -> None:
    token, token_hash = issue_player_token()
    db_session.add(Player(id=player_id))
    db_session.add(PlayerProfile(player_id=player_id, rating=rating))
    db_session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    db_session.commit()
    client.headers["Authorization"] = f"Bearer {token}"


def test_session_loads_state_excludes_retired_review_schedule(
    client: TestClient, db_session: Session
) -> None:
    player_id = "retired-schedule-player"
    authorize_new_player(client, db_session, player_id)
    create_persisted_lesson(
        db_session,
        player_id,
        next_review_at=datetime.now(UTC) - timedelta(days=1),
        review_retired_at=datetime.now(UTC),
    )

    served = client.get(f"/v1/learning/session?player_id={player_id}")

    assert served.status_code == 200
    assert served.json()["lessons"] == []


def test_session_loads_state_excludes_retired_skill_state(db_session: Session) -> None:
    player_id = "retired-skill-player"
    db_session.add(Player(id=player_id))
    db_session.commit()
    set_skill_mastery(
        db_session, player_id, "tactics.fork", alpha=9.0, beta=1.0, retired_at=datetime.now(UTC)
    )
    set_skill_mastery(db_session, player_id, "tactics.pin", alpha=2.0, beta=2.0)

    state = load_player_session_state(db_session, player_id)

    assert state.skill_for("tactics.fork") is None
    assert state.skill_for("tactics.pin") is not None


def test_session_loads_state_recent_verified_attempts_excludes_ungraded_and_stale(
    db_session: Session,
) -> None:
    player_id = "recent-attempts-player"
    db_session.add(Player(id=player_id))
    db_session.commit()
    study_session = StudySession(player_id=player_id, domain="daily_training")
    db_session.add(study_session)
    db_session.commit()

    now = datetime.now(UTC)
    db_session.add(
        LessonAttempt(
            session_id=study_session.id,
            player_id=player_id,
            lesson_id="verified-recent",
            source_kind="persisted_opportunity",
            elapsed_ms=1000,
            hints_used=0,
            success=False,
            grading_status="verified",
            profile_update_result="applied",
            completed_at=now - timedelta(minutes=10),
        )
    )
    db_session.add(
        LessonAttempt(
            session_id=study_session.id,
            player_id=player_id,
            lesson_id="ungraded-recent",
            source_kind="opening_mission",
            elapsed_ms=1000,
            hints_used=0,
            success=None,
            grading_status="ungraded",
            profile_update_result="not_applicable",
            completed_at=now - timedelta(minutes=5),
        )
    )
    db_session.add(
        LessonAttempt(
            session_id=study_session.id,
            player_id=player_id,
            lesson_id="verified-stale",
            source_kind="persisted_opportunity",
            elapsed_ms=1000,
            hints_used=0,
            success=True,
            grading_status="verified",
            profile_update_result="applied",
            completed_at=now - timedelta(hours=6),
        )
    )
    db_session.commit()

    state = load_player_session_state(db_session, player_id, now)

    assert [attempt.lesson_id for attempt in state.recent_verified_attempts] == ["verified-recent"]


def test_session_priority_ranks_low_mastery_above_high_mastery(
    client: TestClient, db_session: Session
) -> None:
    player_id = "mastery-ranking-player"
    authorize_new_player(client, db_session, player_id)
    set_skill_mastery(db_session, player_id, "tactics.low-mastery", alpha=1.0, beta=9.0)
    set_skill_mastery(db_session, player_id, "tactics.high-mastery", alpha=9.0, beta=1.0)
    non_due = datetime.now(UTC) + timedelta(days=3)
    low_mastery_opportunity = create_persisted_lesson(
        db_session, player_id, skill_id="tactics.low-mastery", next_review_at=non_due
    )
    create_persisted_lesson(
        db_session, player_id, skill_id="tactics.high-mastery", next_review_at=non_due
    )

    served = client.get(f"/v1/learning/session?player_id={player_id}")
    lesson_ids = [lesson["lesson_id"] for lesson in served.json()["lessons"]]

    assert lesson_ids[0] == str(low_mastery_opportunity.id)


def test_session_priority_ranks_overdue_review_above_non_due_review(
    client: TestClient, db_session: Session
) -> None:
    player_id = "due-ranking-player"
    authorize_new_player(client, db_session, player_id)
    set_skill_mastery(db_session, player_id, "tactics.fork", alpha=5.0, beta=5.0)
    due_opportunity = create_persisted_lesson(
        db_session,
        player_id,
        skill_id="tactics.fork",
        next_review_at=datetime.now(UTC) - timedelta(days=1),
    )
    create_persisted_lesson(
        db_session,
        player_id,
        skill_id="tactics.fork",
        next_review_at=datetime.now(UTC) + timedelta(days=3),
    )

    served = client.get(f"/v1/learning/session?player_id={player_id}")
    lesson_ids = [lesson["lesson_id"] for lesson in served.json()["lessons"]]

    assert lesson_ids[0] == str(due_opportunity.id)


def test_session_fatigue_shifts_composition_after_high_error_history(
    client: TestClient, db_session: Session
) -> None:
    player_id = "fatigue-player"
    authorize_new_player(client, db_session, player_id)
    # All three stay above NEUTRAL_WEAKNESS_SEVERITY (weakness > 0.5) so they
    # remain in the single "mistakes" bucket after PR-4's classification;
    # the flooring mechanism below relies on sharing one bucket.
    set_skill_mastery(db_session, player_id, "tactics.rarely-missed", alpha=4.0, beta=6.0)
    set_skill_mastery(db_session, player_id, "tactics.mixed", alpha=2.0, beta=8.0)
    set_skill_mastery(db_session, player_id, "tactics.often-missed", alpha=1.0, beta=19.0)

    non_due = datetime.now(UTC) + timedelta(days=3)
    low_weakness = create_persisted_lesson(
        db_session, player_id, skill_id="tactics.rarely-missed", next_review_at=non_due
    )
    create_persisted_lesson(db_session, player_id, skill_id="tactics.mixed", next_review_at=non_due)
    high_weakness = create_persisted_lesson(
        db_session, player_id, skill_id="tactics.often-missed", next_review_at=non_due
    )

    rested = client.get(f"/v1/learning/session?player_id={player_id}")
    rested_order = [lesson["lesson_id"] for lesson in rested.json()["lessons"]]
    assert rested_order[0] == str(high_weakness.id)
    assert rested_order[-1] == str(low_weakness.id)

    # Simulate a long, high-error session: 20 recent server-verified failures
    # hits both the volume cap and a 100% error rate, driving fatigue to 1.0.
    filler_session = StudySession(player_id=player_id, domain="daily_training")
    db_session.add(filler_session)
    db_session.commit()
    now = datetime.now(UTC)
    for index in range(20):
        db_session.add(
            LessonAttempt(
                session_id=filler_session.id,
                player_id=player_id,
                lesson_id=f"fatigue-filler-{index}",
                source_kind="persisted_opportunity",
                elapsed_ms=1000,
                hints_used=0,
                success=False,
                grading_status="verified",
                profile_update_result="applied",
                completed_at=now - timedelta(minutes=index),
            )
        )
    db_session.commit()

    fatigued = client.get(f"/v1/learning/session?player_id={player_id}")
    fatigued_order = [lesson["lesson_id"] for lesson in fatigued.json()["lessons"]]
    assert fatigued_order[0] == str(low_weakness.id)
    assert fatigued_order[-1] == str(high_weakness.id)


def test_session_exploration_floor_guarantees_a_non_weakness_item(
    client: TestClient, db_session: Session
) -> None:
    player_id = "exploration-floor-player"
    authorize_new_player(client, db_session, player_id)
    non_due = datetime.now(UTC) + timedelta(days=3)

    for concept in ("weak-a", "weak-b", "weak-c", "weak-d", "weak-e"):
        set_skill_mastery(db_session, player_id, f"tactics.{concept}", alpha=1.0, beta=9.0)
        create_persisted_lesson(
            db_session, player_id, skill_id=f"tactics.{concept}", next_review_at=non_due
        )
    set_skill_mastery(db_session, player_id, "tactics.mastered", alpha=9.0, beta=1.0)
    exploration_opportunity = create_persisted_lesson(
        db_session, player_id, skill_id="tactics.mastered", next_review_at=non_due
    )

    served = client.get(f"/v1/learning/session?player_id={player_id}")
    lesson_ids = {lesson["lesson_id"] for lesson in served.json()["lessons"]}

    assert str(exploration_opportunity.id) in lesson_ids


def test_session_marks_and_excludes_reverified_invalid_lesson(
    client: TestClient, db_session: Session
) -> None:
    player_id = "invalid-lesson-player"
    authorize_new_player(client, db_session, player_id)
    opportunity = create_persisted_lesson(db_session, player_id)
    opportunity.lesson_spec = {
        **opportunity.lesson_spec,
        "interaction": {
            **opportunity.lesson_spec["interaction"],
            "accepted_moves": [{"san": "d4"}],
        },
    }
    db_session.add(opportunity)
    db_session.commit()

    served = client.get(f"/v1/learning/session?player_id={player_id}")

    assert served.status_code == 200
    assert served.json()["lessons"] == []
    db_session.refresh(opportunity)
    assert opportunity.verification_status == "invalid"
    assert opportunity.verification_error is not None


def test_session_reuses_persisted_engine_analysis(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    player_id = "persisted-proof-player"
    authorize_new_player(client, db_session, player_id)
    opportunity = create_persisted_lesson(db_session, player_id)

    def unexpected_stockfish_adapter(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("re-verification invoked Stockfish despite persisted analysis")

    monkeypatch.setattr("scan64.api.learning.StockfishAdapter", unexpected_stockfish_adapter)
    served = client.get(f"/v1/learning/session?player_id={player_id}")

    assert served.status_code == 200
    assert [lesson["lesson_id"] for lesson in served.json()["lessons"]] == [str(opportunity.id)]

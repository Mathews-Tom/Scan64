from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from scan64.api.models import Player, PlayerCredential, PlayerProfile, issue_player_token
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game
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
    opportunity = PersistedLessonOpportunity(
        game_id=game.id, player_id=player_id, lesson_spec=lesson_spec()
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

    assert [attempt.lesson_id for attempt in state.recent_verified_attempts] == [
        "verified-recent"
    ]

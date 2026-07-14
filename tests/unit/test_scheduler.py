from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from scan64.api.learning import get_training_session
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.positions.models import Position  # noqa: F401
from scan64.learning.scheduling.priority import PriorityFactors, compute_session_fatigue
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule


def test_priority_ranking_formula_bounds():
    factors = PriorityFactors(
        review_due=1.0,
        weakness_severity=1.0,
        recurrence_probability=1.0,
        curriculum_relevance=1.0,
        transfer_need=1.0,
        user_interest=1.0,
        recent_overexposure=0.5,
    )

    score = factors.compute_priority(session_fatigue=0.5)
    # 6 - 0.5 - 0.5 = 5.0
    assert score == 5.0

    factors = PriorityFactors()
    assert factors.compute_priority(session_fatigue=1.0) == 0.0


def test_session_fatigue_increases_measurably():
    # Base state
    fatigue_0 = compute_session_fatigue(
        consecutive_lessons=0, baseline_response_time_ms=1000.0, rolling_response_time_ms=1000.0
    )
    assert fatigue_0 == 0.0

    # More lessons completed
    fatigue_5 = compute_session_fatigue(
        consecutive_lessons=5, baseline_response_time_ms=1000.0, rolling_response_time_ms=1000.0
    )
    assert fatigue_5 > fatigue_0

    # More lessons + degrading response time
    fatigue_5_degraded = compute_session_fatigue(
        consecutive_lessons=5, baseline_response_time_ms=1000.0, rolling_response_time_ms=1500.0
    )
    assert fatigue_5_degraded > fatigue_5

    # Max lessons + high degradation
    fatigue_max = compute_session_fatigue(
        consecutive_lessons=30, baseline_response_time_ms=1000.0, rolling_response_time_ms=3000.0
    )
    assert fatigue_max == 1.0


def test_review_schedule_due_selection():
    now = datetime(2026, 7, 14, 12, 0, 0)
    schedule = ReviewSchedule(
        player_id="player1", item_id="item1", next_review_at=now - timedelta(hours=1)
    )

    assert schedule.is_due(now)

    schedule.update(success=True, current_time=now)
    assert not schedule.is_due(now)
    assert schedule.interval_days > 1.0
    assert schedule.next_review_at > now

    schedule_not_due = ReviewSchedule(
        player_id="player1", item_id="item2", next_review_at=now + timedelta(hours=1)
    )
    assert not schedule_not_due.is_due(now)



@pytest.fixture(name="db_session")
def session_fixture():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session

def test_scheduler_draws_from_all_content_sources(db_session: Session):
    """
    Integration test verifying that the scheduler can accept and compose a session
    drawing from M15 (endgame tablebase), M16 (openings), M17 (famous games), and M9 (exercises).
    """
    from scan64.chess.games.models import Game, PlaySession

    game = Game(pgn="", white="w", black="b", result="*")
    db_session.add(game)
    db_session.commit()
    db_session.refresh(game)

    ps = PlaySession(player_id="player1", game_id=game.id)
    db_session.add(ps)
    db_session.commit()

    opp = PersistedLessonOpportunity(
        game_id=game.id,
        lesson_spec={
            "schema_version": "1.0",
            "lesson_id": "opp1",
            "source": {
                "kind": "custom",
                "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
            },
            "diagnosis": {"primary": "blunder", "confidence": 1.0},
            "objective": {"type": "find_best_move", "instruction": "Find a better move."},
            "interaction": {
                "input": "click",
                "maximum_attempts": 1,
                "accepted_moves": [{"san": "e4"}]
            },
            "verification": {"status": "verified", "engine": "stockfish"}
        }
    )
    db_session.add(opp)
    db_session.commit()

    session = get_training_session(player_id="player1", db=db_session)

    # We want to ensure it's capable of selecting across the sources based on type and priority.
    # And we expect 5 items total in the daily session
    assert len(session) == 5

    # Because of our exploration floor and priority mechanisms, we should see
    # a mix of content types in the composed session.
    sources = set()
    for spec in session:
        if "endgame" in spec.diagnosis.primary:
            sources.add("m15_tablebase")
        elif "opening" in spec.diagnosis.primary:
            sources.add("m16_opening")
        elif "tactics" in spec.diagnosis.primary:
            sources.add("m17_famous_game")
        elif "blunder" in spec.diagnosis.primary:
            sources.add("m9_exercise")

    # Verify it draws from multiple actual sources successfully, including our persisted M9
    assert "m9_exercise" in sources
    assert len(sources) >= 3

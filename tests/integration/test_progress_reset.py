from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import chess
import pytest
from chess_lesson_spec import LessonSpec
from fastapi import Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, col, select
from starlette.responses import StreamingResponse

import scan64.api.onboarding as onboarding_api
import scan64.api.puzzles as puzzle_api
import scan64.chess.analysis.jobs as jobs
from scan64.api.app import app
from scan64.api.middleware import IdempotencyMiddleware, IdempotencyRecord
from scan64.api.models import Player, PlayerCredential, PlayerOnboarding, PlayerProfile
from scan64.api.progress import CLEARED_MODELS
from scan64.chess.analysis.coach_interruption import (
    CoachInterruption,
    PreparedCoachInterruption,
)
from scan64.chess.analysis.credit_models import PositiveMoveCredit
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedLessonOpportunity,
)
from scan64.chess.analysis.orchestration import FastPassResult
from scan64.chess.games.assistance import SessionHintReveal
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.coach.models import CoachStudentLink
from scan64.content.models import ContentItem, LessonAttempt, StudySession
from scan64.content.puzzle_practice_models import INITIAL_RATING, PuzzleRating
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.transfer import TransferPosition
from scan64.learning.habit_models import (
    HabitCredit,
    HabitDay,
    HabitGoal,
    HabitPolicy,
    PatternAward,
    PatternAwardEvidence,
    PatternTarget,
)
from scan64.learning.plugins.registry import PluginRegistry
from scan64.learning.profiling.models import SkillState
from scan64.learning.progress import RESET_AT_KEY, RESET_GENERATION_KEY, game_counts_for_progress
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import get_session
from tests.integration.test_data_lifecycle_completeness import _seed_player_lifecycle_records
from tests.integration.test_lesson_attempts import (
    create_persisted_lesson,
    create_study_session,
    lesson_spec,
)
from tests.integration.test_positive_move_credit import RECORDING, _stage
from tests.integration.test_puzzle_lifecycle import puzzle_item, seed_puzzle_run

OWNER = "reset-owner"
OTHER = "reset-other"
PATH = f"/v1/players/{OWNER}/progress/reset"


def register(client: TestClient, player_id: str) -> dict[str, str]:
    response = client.post(
        "/v1/players",
        json={
            "id": player_id,
            "display_name": "Keep my name",
            "preferences": {"appearance": {"palette": "wood"}, "manual_bot_level": 5},
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def snapshot(session: Session, models: tuple[type[SQLModel], ...]) -> dict[str, list[str]]:
    session.expire_all()
    return {
        str(model.__tablename__): sorted(
            json.dumps(row.model_dump(mode="json"), sort_keys=True)
            for row in session.exec(select(model)).all()
        )
        for model in models
    }


def seed(client: TestClient, session: Session) -> tuple[dict[str, str], dict[str, str]]:
    owner_headers = register(client, OWNER)
    other_headers = register(client, OTHER)
    records = _seed_player_lifecycle_records(session, OWNER, OTHER)
    game_id = records["game_id"]
    assert isinstance(game_id, UUID)
    play = session.exec(select(PlaySession).where(PlaySession.game_id == game_id)).one()
    job = session.exec(select(AnalysisJob).where(AnalysisJob.game_id == game_id)).one()
    analysis_id = records["analysis_id"]
    assert isinstance(analysis_id, UUID)
    profile = session.get(PlayerProfile, OWNER)
    assert profile is not None
    profile.rating = 2100
    onboarding = session.get(PlayerOnboarding, OWNER)
    assert onboarding is not None
    onboarding.status = "completed"
    onboarding.declared_level = "club"
    onboarding.suggested_bot_level = 5
    session.add_all([profile, onboarding])
    target = PatternTarget(player_id=OWNER, code="tactics.hanging_piece")
    session.add(target)
    session.flush()
    award = PatternAward(
        player_id=OWNER,
        target_id=target.id,
        code=target.code,
        window_start="2026-01-01",
        awarded_at=datetime.now(UTC),
    )
    session.add(award)
    session.flush()
    session.add_all(
        [
            HabitGoal(player_id=OWNER, effective_day="2026-01-01", goal=7),
            HabitGoal(player_id=OWNER, effective_day="2099-01-01", goal=9),
            HabitDay(player_id=OWNER, utc_day="2026-01-01", goal=7, met_at=datetime.now(UTC)),
            HabitCredit(
                player_id=OWNER,
                utc_day="2026-01-01",
                source_kind="puzzle",
                source_id="old-solve",
                source_key="old-solve",
                source_epd="old-epd",
                completed_at=datetime.now(UTC),
            ),
            PatternAwardEvidence(
                player_id=OWNER,
                award_id=award.id,
                ordinal=1,
                game_id=game_id,
                analysis_job_id=job.id,
                game_created_at=datetime.now(UTC),
                job_completed_at=datetime.now(UTC),
            ),
            PositiveMoveCredit(
                player_id=OWNER,
                game_id=game_id,
                canonical_id="historic-proof",
                skill_id="board_awareness.hanging_piece",
                ply=1,
                source_analysis_id=analysis_id,
                reached_analysis_id=analysis_id,
                proof={"historical": True},
            ),
            SessionHintReveal(
                session_id=play.id,
                player_id=OWNER,
                prefix_hash="historic-hint",
                canonical_id="historic-position",
                ply=1,
                initial_fen=chess.STARTING_FEN,
                moves=[],
                highest_step=2,
            ),
            SkillState(
                player_id=OTHER, concept_code="board_awareness.hanging_piece", alpha=8, beta=3
            ),
            HabitDay(player_id=OTHER, utc_day="2026-01-01", goal=3, met_at=datetime.now(UTC)),
            PuzzleRating(player_id=OTHER, rating=1450, rated_count=9),
        ]
    )
    session.commit()
    seed_puzzle_run(session, OTHER, solved=True)
    # Exercise an active run as well as the completed puzzle/lesson ledgers.
    seed_puzzle_run(session, OWNER)
    return owner_headers, other_headers


PRESERVED = (
    PlayerCredential,
    PlayerOnboarding,
    Game,
    PlaySession,
    Position,
    EngineAnalysis,
    GameMoveReview,
    AnalysisJob,
    PersistedLessonOpportunity,
    Evidence,
    TransferPosition,
    CoachStudentLink,
    ContentItem,
    PositiveMoveCredit,
    SessionHintReveal,
    HabitGoal,
)


def test_reset_clears_learning_preserves_sources_settings_and_other_player(
    client: TestClient,
    db_session: Session,
) -> None:
    # This is an isolated in-memory test engine, never the application's real DB.
    db_session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
    headers, _ = seed(client, db_session)
    preserved = snapshot(db_session, PRESERVED)
    player = db_session.get(Player, OWNER)
    assert player is not None
    preferences = dict(player.preferences)
    other_before = {
        name: [
            row.model_dump(mode="json")
            for row in db_session.exec(
                select(model).where(col(getattr(model, "player_id")) == OTHER)
            ).all()
        ]
        for name, model in CLEARED_MODELS
    }
    before_counts = {
        name: len(
            db_session.exec(select(model).where(col(getattr(model, "player_id")) == OWNER)).all()
        )
        for name, model in CLEARED_MODELS
    }
    assert all(before_counts.values()), before_counts
    response = client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["player_id"] == OWNER
    assert datetime.fromisoformat(data["reset_at"]).tzinfo is not None
    assert data["cleared"] == {**before_counts, "profile_rating": 1}
    assert snapshot(db_session, PRESERVED) == preserved
    for name, model in CLEARED_MODELS:
        assert (
            db_session.exec(select(model).where(col(getattr(model, "player_id")) == OWNER)).all()
            == []
        )
        assert [
            row.model_dump(mode="json")
            for row in db_session.exec(
                select(model).where(col(getattr(model, "player_id")) == OTHER)
            ).all()
        ] == other_before[name]
    profile = db_session.get(PlayerProfile, OWNER)
    assert profile is not None and profile.rating == 1500 and profile.display_name == "Keep my name"
    db_session.refresh(player)
    assert {
        key: value
        for key, value in player.preferences.items()
        if key not in {RESET_AT_KEY, RESET_GENERATION_KEY}
    } == preferences
    assert datetime.fromisoformat(player.preferences[RESET_AT_KEY]) == datetime.fromisoformat(
        data["reset_at"]
    )
    policy = db_session.get(HabitPolicy, OWNER)
    assert policy is not None
    assert policy.effective_at.replace(tzinfo=UTC) == datetime.fromisoformat(data["reset_at"])
    strength = client.get(f"/v1/players/{OWNER}/strength", headers=headers)
    assert strength.status_code == 200
    assert strength.json()["puzzle_rating"] == INITIAL_RATING
    assert strength.json()["puzzle_rated_count"] == 0
    assert strength.json()["puzzle_events"] == []
    assert strength.json()["declared_level"] == "club"
    habit = client.get(f"/v1/players/{OWNER}/habit", headers=headers)
    assert habit.status_code == 200
    assert all(day["qualifying_solves"] == 0 and not day["met"] for day in habit.json()["days"])
    assert habit.json()["days"][-1]["goal"] == 7


@pytest.mark.parametrize(
    "confirmation", [None, "", "reset progress", " RESET PROGRESS", "RESET PROGRESS ", 1]
)
def test_confirmation_must_be_exact_without_mutation(
    client: TestClient,
    db_session: Session,
    confirmation: object,
) -> None:
    headers = register(client, OWNER)
    seed_puzzle_run(db_session, OWNER, solved=True)
    models = tuple(model for _, model in CLEARED_MODELS) + (Player, PlayerProfile, HabitPolicy)
    before = snapshot(db_session, models)
    response = client.post(PATH, headers=headers, json={"confirmation": confirmation})
    assert response.status_code == 422
    assert snapshot(db_session, models) == before


def test_missing_or_other_owner_token_cannot_reset(client: TestClient, db_session: Session) -> None:
    register(client, OWNER)
    other = register(client, OTHER)
    seed_puzzle_run(db_session, OWNER, solved=True)
    models = tuple(model for _, model in CLEARED_MODELS) + (Player, PlayerProfile, HabitPolicy)
    before = snapshot(db_session, models)
    assert client.post(PATH, json={"confirmation": "RESET PROGRESS"}).status_code == 401
    assert (
        client.post(PATH, headers=other, json={"confirmation": "RESET PROGRESS"}).status_code == 404
    )
    assert snapshot(db_session, models) == before


def test_failure_rolls_back_deletions_rating_boundary_and_policy(
    client: TestClient,
    db_session: Session,
) -> None:
    headers, _ = seed(client, db_session)
    models = tuple(model for _, model in CLEARED_MODELS) + (Player, PlayerProfile, HabitPolicy)
    before = snapshot(db_session, models)
    # Fail a real SQL statement after earlier child DELETEs have executed.
    connection = db_session.connection()

    def fail_mid_transaction(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if statement.startswith("UPDATE player SET preferences"):
            raise RuntimeError("isolated reset rollback probe")

    event.listen(connection, "before_cursor_execute", fail_mid_transaction)
    try:
        with pytest.raises(RuntimeError, match="isolated reset rollback probe"):
            client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"})
    finally:
        event.remove(connection, "before_cursor_execute", fail_mid_transaction)
    assert snapshot(db_session, models) == before


def test_deleted_active_runs_stay_missing_and_new_puzzle_earns_fresh_rating(
    client: TestClient,
    db_session: Session,
) -> None:
    headers = register(client, OWNER)
    old_run = seed_puzzle_run(db_session, OWNER)
    old_id, study_id = old_run.id, old_run.session_id
    assert (
        client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"}).status_code
        == 200
    )
    base = "/v1/puzzles"
    assert client.get(f"{base}/runs/{old_id}", headers=headers).status_code == 404
    assert (
        client.post(
            f"{base}/runs/{old_id}/moves",
            headers=headers,
            json={"move": "e7e5", "expected_cursor": 0, "expected_revision": 0},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/v1/learning/lesson-attempts",
            headers=headers,
            json={
                "session_id": study_id,
                "lesson_id": "old-opening",
                "source_kind": "opening_mission",
                "elapsed_ms": 10,
                "hints_used": 0,
            },
        ).status_code
        == 404
    )
    start = client.post(f"{base}/runs", headers=headers, json={"mode": "daily"})
    assert start.status_code == 200, start.text
    assert start.json()["daily"]["rating"] == INITIAL_RATING
    run = start.json()["run"]
    assert run is not None and run["run_id"] != old_id
    solved = client.post(
        f"{base}/runs/{run['run_id']}/moves",
        headers=headers,
        json={
            "move": "e7e5",
            "expected_cursor": run["cursor"],
            "expected_revision": run["revision"],
        },
    )
    assert solved.status_code == 200, solved.text
    assert solved.json()["state"] == "solved"
    rating = db_session.get(PuzzleRating, OWNER, populate_existing=True)
    assert rating is not None and rating.rated_count == 1 and rating.rating > INITIAL_RATING
    assert db_session.get(PuzzleRun, old_id, populate_existing=True) is None
    assert db_session.get(StudySession, study_id, populate_existing=True) is None
    skills = db_session.exec(select(SkillState).where(SkillState.player_id == OWNER)).all()
    # This fixture has no skill mapping; a fresh rating must not require one.
    assert skills == []


def test_reset_drops_learning_replay_cache_but_keeps_game_move_cache(
    client: TestClient,
    db_session: Session,
) -> None:
    headers = register(client, OWNER)
    principal = sha256(headers["Authorization"].encode()).hexdigest()
    learning_key = f"POST:/v1/puzzles/runs:{principal}:start"
    move_key = f"POST:/v1/play-sessions/saved/moves:{principal}:move"
    for key in (learning_key, move_key):
        db_session.add(
            IdempotencyRecord(
                idempotency_key=key, status_code=200, response_body="{}", headers="{}"
            )
        )
    db_session.commit()
    assert (
        client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"}).status_code
        == 200
    )
    assert db_session.get(IdempotencyRecord, learning_key) is None
    assert db_session.get(IdempotencyRecord, move_key) is not None


def test_strength_excludes_preserved_pre_reset_games_and_counts_new_games(
    client: TestClient,
    db_session: Session,
) -> None:
    headers = register(client, OWNER)
    old = Game(pgn="1. e4", owner_player_id=OWNER, owner_color="white", result="1-0")
    db_session.add(old)
    db_session.flush()
    db_session.add(
        PlaySession(
            player_id=OWNER,
            game_id=old.id,
            status="completed",
            assistance_tracking_version=1,
            opponent_config={"provider": "stockfish", "bot_level": "5", "player_color": "white"},
        )
    )
    db_session.commit()
    assert (
        client.get(f"/v1/players/{OWNER}/strength", headers=headers).json()["bot_cohorts"][0][
            "sample_size"
        ]
        == 1
    )
    assert (
        client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"}).status_code
        == 200
    )
    assert client.get(f"/v1/players/{OWNER}/strength", headers=headers).json()["bot_cohorts"] == []
    assert db_session.get(Game, old.id) is not None
    assert not game_counts_for_progress(old, OWNER, db_session)
    new = Game(
        pgn="1. d4",
        owner_player_id=OWNER,
        owner_color="white",
        result="1-0",
        created_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    db_session.add(new)
    db_session.flush()
    db_session.add(
        PlaySession(
            player_id=OWNER,
            game_id=new.id,
            status="completed",
            assistance_tracking_version=1,
            opponent_config={"provider": "stockfish", "bot_level": "5", "player_color": "white"},
        )
    )
    db_session.commit()
    assert game_counts_for_progress(new, OWNER, db_session)
    cohorts = client.get(f"/v1/players/{OWNER}/strength", headers=headers).json()["bot_cohorts"]
    assert len(cohorts) == 1 and cohorts[0]["sample_size"] == 1


def test_in_flight_puzzle_cannot_recreate_a_deleted_run(
    client: TestClient,
    db_session: Session,
) -> None:
    headers = register(client, OWNER)
    original = seed_puzzle_run(db_session, OWNER)
    run_id = original.id
    # Real requests have independent identity maps. Keep the fully loaded
    # pre-analysis snapshot alive while another request commits the reset.
    with Session(db_session.get_bind()) as in_flight:
        stale = in_flight.get(PuzzleRun, run_id)
        assert stale is not None
        fen = stale.fen
        assert (
            client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"}).status_code
            == 200
        )
        with pytest.raises(puzzle_api.HTTPException) as error:
            puzzle_api._update_run(
                stale,
                puzzle_api.ExpectedPuzzleState(expected_cursor=0, expected_revision=0),
                in_flight,
                fen=fen,
                cursor=1,
                played_moves=["e7e5"],
                wrong_attempts=0,
                state="solved",
                hint_steps={},
            )
    assert error.value.status_code == 404
    assert db_session.get(PuzzleRating, OWNER) is None
    assert db_session.exec(select(PuzzleRun).where(PuzzleRun.player_id == OWNER)).all() == []


def test_new_lesson_after_reset_uses_fresh_mastery_priors(
    client: TestClient,
    db_session: Session,
) -> None:
    headers = register(client, OWNER)
    db_session.add(
        SkillState(
            player_id=OWNER,
            concept_code="threat_processing.missed_capture",
            alpha=30,
            beta=2,
        )
    )
    db_session.commit()
    assert (
        client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"}).status_code
        == 200
    )
    opportunity = create_persisted_lesson(db_session, OWNER)
    opportunity.verification_status = "verified"
    db_session.add(opportunity)
    db_session.commit()
    study = create_study_session(db_session, OWNER)
    response = client.post(
        "/v1/learning/lesson-attempts",
        headers=headers,
        json={
            "session_id": study.id,
            "lesson_id": str(opportunity.id),
            "source_kind": "persisted_opportunity",
            "submitted_move": "e2e4",
            "elapsed_ms": 500,
            "hints_used": 0,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert response.json()["profile_update_result"] == "applied"
    skill = db_session.get(SkillState, (OWNER, "threat_processing.missed_capture"))
    assert skill is not None and (skill.alpha, skill.beta) == (4, 2)
    assert (skill.prior_alpha, skill.prior_beta) == (3, 2)


@pytest.mark.asyncio
async def test_pre_reset_analysis_finishes_saved_review_without_rebuilding_mastery(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = register(client, "credit-owner")
    game, play, row, _ = _stage(db_session)
    case = RECORDING["cases"]["hung_white"]

    async def finish_after_reset(*args: object, **kwargs: object) -> FastPassResult:
        response = client.post(
            "/v1/players/credit-owner/progress/reset",
            headers=headers,
            json={"confirmation": "RESET PROGRESS"},
        )
        assert response.status_code == 200, response.text
        evaluations = [
            EngineAnalysis(position_id=uuid4(), raw_result=[{"score_cp": 0, "pv": []}])
            for _ in range(len(game.moves) + 1)
        ]
        evaluations[row.ply - 1].raw_result = copy.deepcopy(case["raw_result"])
        return FastPassResult(candidates=[], evaluations=evaluations)

    monkeypatch.setattr(jobs.FastPassOrchestrator, "run_fast_pass", finish_after_reset)
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    await jobs.run_analysis_for_game(game, db_session, registry=registry)
    assert db_session.get(Game, game.id) is not None
    assert db_session.exec(select(GameMoveReview).where(GameMoveReview.game_id == game.id)).all()
    assert (
        db_session.exec(select(SkillState).where(SkillState.player_id == "credit-owner")).all()
        == []
    )
    assert db_session.exec(select(PositiveMoveCredit)).all() == []
    assert db_session.exec(select(ReviewSchedule)).all() == []
    db_session.refresh(play)
    assert play.credit_analysis_status is None


def test_prepared_coach_work_does_not_restore_deleted_session_or_schedule(
    client: TestClient,
    db_session: Session,
) -> None:
    headers = register(client, OWNER)
    opportunity = create_persisted_lesson(db_session, OWNER)
    study = create_study_session(db_session, OWNER)
    source = db_session.get(Position, opportunity.source_position_id)
    schedule = db_session.get(ReviewSchedule, (OWNER, str(opportunity.id)))
    assert source is not None and schedule is not None
    before = EngineAnalysis(position_id=source.id)
    after = EngineAnalysis(position_id=source.id)
    staged = PreparedCoachInterruption(
        interruption=CoachInterruption(
            lesson=LessonSpec.model_validate(lesson_spec()),
            opportunity_id=opportunity.id,
            study_session_id=study.id,
        ),
        player_id=OWNER,
        game_id=opportunity.game_id,
        skill_id="threat_processing.missed_capture",
        observed_at=datetime.now(UTC),
        source_position=source,
        result_position=source,
        before_analysis=before,
        after_analysis=after,
        evidence=(),
        opportunity=opportunity,
        schedule=schedule,
        study_session=study,
    )
    assert (
        client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"}).status_code
        == 200
    )
    staged.add_to(db_session)
    db_session.commit()
    assert db_session.get(EngineAnalysis, before.id) is not None
    assert db_session.get(PersistedLessonOpportunity, opportunity.id) is not None
    assert db_session.get(StudySession, study.id) is None
    assert db_session.get(ReviewSchedule, (OWNER, str(opportunity.id))) is None
    assert db_session.exec(select(SkillState).where(SkillState.player_id == OWNER)).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["puzzle", "lesson"])
async def test_delayed_success_cannot_publish_learning_cache_after_reset(
    client: TestClient,
    db_session: Session,
    kind: str,
) -> None:
    headers = register(client, OWNER)
    if kind == "puzzle":
        db_session.add(puzzle_item())
        db_session.commit()
        path = "/v1/puzzles/runs"
        payload: dict[str, Any] = {"mode": "daily"}
    else:
        study = create_study_session(db_session, OWNER)
        path = "/v1/learning/lesson-attempts"
        payload = {
            "session_id": study.id,
            "lesson_id": "new-opening",
            "source_kind": "opening_mission",
            "elapsed_ms": 10,
            "hints_used": 0,
        }
    sessions: list[Session] = []

    def cache_sessions():
        with Session(db_session.get_bind()) as session:
            sessions.append(session)
            yield session

    middleware = IdempotencyMiddleware(app, get_session_callable=cache_sessions)
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "query_string": b"",
            "server": ("testserver", 80),
            "headers": [
                (b"authorization", headers["Authorization"].encode()),
                (b"idempotency-key", b"held-response"),
            ],
        }
    )
    original_data: dict[str, Any] = {}

    async def call_next(_: Request) -> Response:
        assert sessions and all(not session.in_transaction() for session in sessions)
        # Exercise the actual production handler and its real transaction,
        # without its outer cache (the middleware under test owns that cache).
        response = client.post(path, headers=headers, json=payload)
        assert response.status_code == 200, response.text
        original_data.update(response.json())

        async def delayed_body():
            # Reset after endpoint commit but before the cache publication.
            assert all(not session.in_transaction() for session in sessions)
            reset = client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"})
            assert reset.status_code == 200, reset.text
            yield response.content

        return StreamingResponse(delayed_body(), status_code=200, media_type="application/json")

    result = await middleware.dispatch(request, call_next)
    assert result.status_code == 200
    db_session.expire_all()
    principal = sha256(headers["Authorization"].encode()).hexdigest()
    old_key = f"POST:{path}:{principal}:held-response"
    assert (
        db_session.exec(
            select(IdempotencyRecord).where(
                col(IdempotencyRecord.idempotency_key).startswith(old_key)
            )
        ).all()
        == []
    )
    retry_headers = {**headers, "Idempotency-Key": "held-response"}
    retry = client.post(path, headers=retry_headers, json=payload)
    if kind == "puzzle":
        assert retry.status_code == 200, retry.text
        old_id = original_data["run"]["run_id"]
        new_id = retry.json()["run"]["run_id"]
        assert new_id != old_id
        assert db_session.get(PuzzleRun, old_id) is None
        assert db_session.get(PuzzleRun, new_id) is not None
        assert client.post(path, headers=retry_headers, json=payload).json() == retry.json()
    else:
        assert retry.status_code == 404, retry.text
        assert db_session.get(LessonAttempt, original_data["id"]) is None


@pytest.mark.parametrize("prior_reset", [False, True])
def test_reset_generation_ignores_obsolete_production_puzzle_cache_on_retry(
    client: TestClient,
    db_session: Session,
    prior_reset: bool,
) -> None:
    headers = register(client, OWNER)
    if prior_reset:
        assert (
            client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"}).status_code
            == 200
        )
    db_session.add(puzzle_item())
    db_session.commit()
    keyed = {**headers, "Idempotency-Key": "same-start"}
    started = client.post("/v1/puzzles/runs", headers=keyed, json={"mode": "daily"})
    assert started.status_code == 200, started.text
    old_id = started.json()["run"]["run_id"]
    principal = sha256(headers["Authorization"].encode()).hexdigest()
    old_key = f"POST:/v1/puzzles/runs:{principal}:same-start"
    player = db_session.get(Player, OWNER, populate_existing=True)
    assert player is not None
    if prior_reset:
        old_key += f":progress:{player.preferences[RESET_GENERATION_KEY]}"
    db_session.expire_all()
    assert db_session.get(IdempotencyRecord, old_key) is not None
    assert (
        client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"}).status_code
        == 200
    )
    # A delayed legacy writer or an old imported cache must remain unreachable,
    # even when the point-in-time deletion has already completed.
    db_session.add(
        IdempotencyRecord(
            idempotency_key=old_key,
            status_code=200,
            response_body=started.text,
            headers="{}",
        )
    )
    db_session.commit()
    retry = client.post("/v1/puzzles/runs", headers=keyed, json={"mode": "daily"})
    assert retry.status_code == 200, retry.text
    new_id = retry.json()["run"]["run_id"]
    assert new_id != old_id
    db_session.expire_all()
    assert db_session.get(PuzzleRun, old_id) is None
    assert db_session.get(PuzzleRun, new_id) is not None


def test_bot_choice_refreshes_stale_preferences_after_concurrent_reset(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = register(client, OWNER)
    game = Game(pgn="", owner_player_id=OWNER)
    db_session.add(game)
    db_session.commit()
    previous_override = app.dependency_overrides[get_session]

    def independent_sessions():
        with Session(db_session.get_bind()) as session:
            yield session

    app.dependency_overrides[get_session] = independent_sessions
    owned_player = onboarding_api.owned_player
    reset_result: dict[str, Any] = {}

    def reset_after_read(request: Request, player_id: str, session: Session) -> Player:
        player = owned_player(request, player_id, session)
        if request.url.path.endswith("/bot-choice"):
            assert RESET_AT_KEY not in player.preferences
            response = client.post(PATH, headers=headers, json={"confirmation": "RESET PROGRESS"})
            assert response.status_code == 200, response.text
            reset_result.update(response.json())
            # A separate identity map still contains the pre-reset dictionary.
            assert RESET_AT_KEY not in player.preferences
        return player

    monkeypatch.setattr(onboarding_api, "owned_player", reset_after_read)
    try:
        response = client.put(
            f"/v1/players/{OWNER}/bot-choice", headers=headers, json={"bot_level": 6}
        )
        assert response.status_code == 200, response.text
        assert response.json()["manual_bot_level"] == 6
    finally:
        app.dependency_overrides[get_session] = previous_override
    db_session.expire_all()
    player = db_session.get(Player, OWNER)
    assert player is not None
    assert player.preferences["manual_bot_level"] == 6
    assert player.preferences["appearance"] == {"palette": "wood"}
    assert datetime.fromisoformat(player.preferences[RESET_AT_KEY]) == datetime.fromisoformat(
        reset_result["reset_at"]
    )
    assert player.preferences[RESET_GENERATION_KEY]
    assert not game_counts_for_progress(game, OWNER, db_session)

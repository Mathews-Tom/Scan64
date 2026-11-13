from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import chess
import pytest
from chess_lesson_spec import LessonSpec
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from scan64.api.models import Player
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.analysis.review import stage_game_review
from scan64.chess.games.ingestion import ingest_fen
from scan64.chess.games.models import Game, PlaySession
from scan64.content.models import ContentAttempt, ContentItem, LessonAttempt, StudySession
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION
from scan64.learning.evidence.models import Evidence
from scan64.learning.habit import (
    advance_targets,
    edit_goal,
    ensure_policy,
    habit_summary,
    record_lesson_credit,
    record_puzzle_credit,
    start_target,
    target_invalidity,
)
from scan64.learning.habit_models import (
    HabitCredit,
    HabitDay,
    HabitGoal,
    HabitPolicy,
    PatternAward,
    PatternAwardEvidence,
    PatternTarget,
)
from scan64.learning.profiling.models import ProfileObservation
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.models import load_models

PLAYER = "habit-player"
CODE = "threat_processing.missed_check"


@pytest.fixture
def session() -> Iterator[Session]:
    load_models()
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Player(id=PLAYER))
        db.add(HabitPolicy(player_id=PLAYER, effective_at=datetime.now(UTC) - timedelta(days=30)))
        db.commit()
        yield db
    engine.dispose()


def _lesson(session: Session, prefix: tuple[str, ...] = ()) -> PersistedLessonOpportunity:
    board = chess.Board()
    for uci in prefix:
        board.push_uci(uci)
    move = next(iter(board.legal_moves))
    game = Game(
        pgn="",
        headers={"FEN": board.fen()},
        moves=[move.uci()],
        owner_player_id=PLAYER,
        owner_color="white" if board.turn else "black",
        created_at=datetime.now(UTC) - timedelta(days=2),
    )
    position = ingest_fen(board.fen(), game.id)
    analysis = EngineAnalysis(
        position_id=position.id,
        raw_result=[{"score_cp": 0, "pv": [move.uci()]}],
    )
    evidence = Evidence(
        evidence_id=str(uuid4()),
        kind="diagnosis",
        position_id=str(position.id),
        engine_analysis_id=str(analysis.id),
        claim="Retained test diagnosis",
        payload={"diagnosis_rule_version": DIAGNOSIS_RULE_VERSION},
        producer={"name": "fixture", "version": "1"},
    )
    spec = LessonSpec.model_validate(
        {
            "schema_version": "0.2.0",
            "lesson_id": str(uuid4()),
            "source": {"kind": "player_game", "fen": board.fen()},
            "diagnosis": {
                "primary": CODE,
                "confidence": 1.0,
                "evidence_refs": [evidence.evidence_id],
            },
            "objective": {"type": "exact_replay", "instruction": "Find a sound move."},
            "interaction": {
                "input": "move",
                "maximum_attempts": 3,
                "accepted_moves": [{"san": board.san(move)}],
            },
            "verification": {"status": "verified", "engine": "stockfish"},
        }
    )
    opportunity = PersistedLessonOpportunity(
        game_id=game.id,
        source_position_id=position.id,
        player_id=PLAYER,
        verification_status="verified",
        verification_analysis_id=analysis.id,
        lesson_spec=spec.model_dump(mode="json"),
    )
    session.add_all(
        [
            game,
            position,
            analysis,
            evidence,
            opportunity,
            AnalysisJob(game_id=game.id, status="completed", completed_at=datetime.now(UTC)),
            ReviewSchedule(
                player_id=PLAYER,
                item_id=str(opportunity.id),
                skill_id=CODE,
                next_review_at=datetime.now(UTC),
            ),
        ]
    )
    session.flush()
    return opportunity


def _attempt(
    session: Session,
    opportunity: PersistedLessonOpportunity,
    *,
    completed_at: datetime | None = None,
    success: bool = True,
    hints: int = 0,
    player_id: str = PLAYER,
) -> LessonAttempt:
    study = StudySession(player_id=player_id, domain="lessons")
    session.add(study)
    session.flush()
    spec = LessonSpec.model_validate(opportunity.lesson_spec)
    attempt = LessonAttempt(
        player_id=player_id,
        session_id=study.id,
        lesson_id=str(opportunity.id),
        source_kind="persisted_opportunity",
        opportunity_id=opportunity.id,
        submitted_move=spec.interaction.accepted_moves[0].san,
        elapsed_ms=100,
        hints_used=hints,
        success=success,
        grading_status="verified",
        profile_update_result="applied",
        completed_at=completed_at or datetime.now(UTC),
    )
    session.add(attempt)
    session.flush()
    record_lesson_credit(attempt, session)
    return attempt


def _puzzle(
    session: Session,
    *,
    fen: str = chess.STARTING_FEN,
    completed_at: datetime | None = None,
    hinted: bool = False,
    matching_attempt: bool = True,
) -> PuzzleRun:
    completed = completed_at or datetime.now(UTC)
    source = chess.Board(fen)
    move = next(iter(source.legal_moves)).uci()
    item = ContentItem(
        domain="puzzles",
        provenance="test source",
        licence="CC0",
        payload={
            "lesson": {
                "schema_version": "0.2.0",
                "lesson_id": str(uuid4()),
                "source": {"kind": "external", "fen": fen},
                "objective": {"type": "puzzle", "instruction": "Play the continuation."},
                "interaction": {"input": "move", "maximum_attempts": 3, "solution": [move]},
                "verification": {"status": "source_verified"},
            }
        },
    )
    study = StudySession(player_id=PLAYER, domain="puzzles")
    source.push_uci(move)
    run = PuzzleRun(
        player_id=PLAYER,
        item_id=item.id,
        session_id=study.id,
        state="solved",
        fen=source.fen(),
        cursor=1,
        played_moves=[move],
        hint_steps={"0": 1} if hinted else {},
        started_at=completed - timedelta(seconds=5),
        completed_at=completed,
    )
    session.add_all([item, study, run])
    if matching_attempt:
        session.add(
            ContentAttempt(
                id=run.id,
                item_id=item.id,
                session_id=study.id,
                player_id=PLAYER,
                success=True,
                hint_assisted=hinted,
                started_at=run.started_at,
                completed_at=completed,
                response_payload={"run_id": run.id, "wrong_attempts": 0},
            )
        )
    session.flush()
    record_puzzle_credit(run, session)
    return run


def _prepared_target(session: Session) -> PatternTarget:
    opportunities = [
        _lesson(session),
        _lesson(session, ("e2e4", "e7e5")),
        _lesson(session, ("d2d4", "d7d5")),
    ]
    target = start_target(PLAYER, CODE, session)
    # Persist historical server rows rather than introducing a runtime clock
    # override. Observation games can then be created and completed afterwards.
    target.started_at = datetime.now(UTC) - timedelta(hours=1)
    session.add(target)
    for index, opportunity in enumerate(opportunities):
        _attempt(
            session, opportunity, completed_at=target.started_at + timedelta(minutes=10 + index)
        )
    assert target.practice_completed_at is not None
    return target


def _scored_game(session: Session, created_at: datetime, *, imported: bool = False) -> AnalysisJob:
    board = chess.Board()
    moves = []
    for _ in range(40):
        for move in sorted(board.legal_moves, key=lambda candidate: candidate.uci()):
            board.push(move)
            if not board.is_game_over(claim_draw=True) and not board.is_repetition(2):
                moves.append(move.uci())
                break
            board.pop()
        else:
            raise AssertionError("Fixture must retain a legal nonterminal continuation")
    game = Game(
        pgn="",
        moves=moves,
        result="1/2-1/2",
        owner_player_id=PLAYER,
        owner_color="white",
        created_at=created_at,
    )
    analyses = [
        EngineAnalysis(position_id=uuid4(), raw_result=[{"score_cp": 0, "pv": []}])
        for _ in range(len(moves) + 1)
    ]
    positions = {}
    staged = {}
    reviews = stage_game_review(game, analyses, positions, staged, set())
    job = AnalysisJob(game_id=game.id, status="pending")
    session.add_all([game, job, *positions.values(), *staged.values(), *reviews])
    if not imported:
        session.add(PlaySession(player_id=PLAYER, game_id=game.id, status="completed"))
    session.flush()
    return job


def _complete(job: AnalysisJob, session: Session) -> None:
    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    session.add(job)
    session.flush()
    advance_targets(PLAYER, session)


def test_summary_is_read_only_even_without_a_policy(session: Session) -> None:
    player = Player(id="uninitialized")
    session.add(player)
    session.commit()
    before = session.connection().connection.total_changes
    summary = habit_summary(player.id, session)
    assert summary["goal"] == 3
    assert summary["day_status"] == "in_progress"
    assert summary["supply"]["remaining_daily_slots"] == 0
    assert session.connection().connection.total_changes == before
    assert session.get(HabitPolicy, player.id) is None
    assert session.get(HabitDay, (player.id, summary["utc_day"])) is None


def test_policy_initialization_never_backdates_to_old_outcomes(session: Session) -> None:
    policy = session.get(HabitPolicy, PLAYER)
    assert policy is not None
    policy.effective_at = datetime.now(UTC)
    _puzzle(session, completed_at=policy.effective_at - timedelta(seconds=1))
    assert not session.exec(select(HabitCredit)).all()
    assert ensure_policy(PLAYER, session).effective_at == policy.effective_at


def test_next_day_goal_does_not_change_an_existing_day(session: Session) -> None:
    _puzzle(session)
    today = datetime.now(UTC).date().isoformat()
    day = session.get(HabitDay, (PLAYER, today))
    assert day is not None and day.goal == 3
    edit_goal(PLAYER, 7, session)
    session.flush()
    summary = habit_summary(PLAYER, session)
    assert summary["goal"] == 3
    assert summary["pending_goal"] == 7
    assert (
        summary["pending_effective_day"]
        == (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    )
    assert day.goal == 3
    with pytest.raises(ValueError):
        edit_goal(PLAYER, 0, session)


def test_lesson_requires_first_recorded_success_and_app_reported_zero_hints(
    session: Session,
) -> None:
    wrong = _lesson(session)
    _attempt(session, wrong, success=False)
    _attempt(session, wrong)
    _attempt(session, _lesson(session, ("e2e4", "e7e5")), hints=1)
    assert not session.exec(select(HabitCredit)).all()
    qualified = _attempt(session, _lesson(session, ("d2d4", "d7d5")))
    record_lesson_credit(qualified, session)
    credits = session.exec(select(HabitCredit)).all()
    assert len(credits) == 1
    assert credits[0].trust_snapshot["hints_authority"] == "app_reported"


@pytest.mark.parametrize("hinted,matching", [(True, True), (False, False)])
def test_puzzle_requires_server_hint_free_matching_terminal_attempt(
    session: Session,
    hinted: bool,
    matching: bool,
) -> None:
    _puzzle(session, hinted=hinted, matching_attempt=matching)
    assert not session.exec(select(HabitCredit)).all()


def test_epd_deduplicates_lesson_and_puzzle_in_the_same_utc_day(session: Session) -> None:
    opportunity = _lesson(session)
    _puzzle(session, fen=opportunity.lesson_spec["source"]["fen"])
    _attempt(session, opportunity)
    assert len(session.exec(select(HabitCredit)).all()) == 1
    # A different puzzle id at the same source position also cannot multiply credit.
    _puzzle(session, fen=opportunity.lesson_spec["source"]["fen"])
    assert len(session.exec(select(HabitCredit)).all()) == 1


def test_completed_utc_day_controls_credit_and_closed_calendar_streak(session: Session) -> None:
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    yesterday_day = yesterday.date().isoformat()
    session.add(HabitGoal(player_id=PLAYER, effective_day=yesterday_day, goal=1))
    _puzzle(session, completed_at=yesterday)
    _puzzle(session, completed_at=now)
    summary = habit_summary(PLAYER, session)
    assert summary["current_streak"] == 1
    assert summary["best_streak"] == 1  # Today remains unclosed, even when already met.
    assert summary["qualifying_solves"] == 1
    assert summary["day_status"] == "in_progress"
    assert session.get(HabitDay, (PLAYER, yesterday_day)) is not None


def test_withdrawal_annotates_but_does_not_remove_met_days(session: Session) -> None:
    yesterday = datetime.now(UTC) - timedelta(days=1)
    session.add(HabitGoal(player_id=PLAYER, effective_day=yesterday.date().isoformat(), goal=1))
    opportunity = _lesson(session)
    _attempt(session, opportunity, completed_at=yesterday)
    schedule = session.get(ReviewSchedule, (PLAYER, str(opportunity.id)))
    assert schedule is not None
    schedule.retired_at = datetime.now(UTC)
    session.add(schedule)
    session.flush()
    summary = habit_summary(PLAYER, session)
    row = next(day for day in summary["days"] if day["utc_day"] == yesterday.date().isoformat())
    assert row["met"] is True and row["withdrawn_sources"] == 1
    assert summary["current_streak"] == 1


def test_target_practice_survives_day_deduplication_and_freezes(session: Session) -> None:
    opportunities = [
        _lesson(session),
        _lesson(session, ("e2e4", "e7e5")),
        _lesson(session, ("d2d4", "d7d5")),
    ]
    target = start_target(PLAYER, CODE, session)
    assert start_target(PLAYER, CODE, session).id == target.id
    _puzzle(session)
    for opportunity in opportunities:
        _attempt(session, opportunity)
    assert len(target.practice) == 3
    assert len(session.exec(select(HabitCredit)).all()) == 3
    assert target.practice_completed_at is not None
    _attempt(session, _lesson(session, ("c2c4", "e7e5")))
    assert len(target.practice) == 3
    assert habit_summary(PLAYER, session)["targets"][0]["status"] == "observing"


def test_unqualified_code_cannot_start_a_target(session: Session) -> None:
    with pytest.raises(ValueError, match="qualifying recurring pattern"):
        start_target(PLAYER, CODE, session)


def test_fifth_completed_game_awards_atomically_and_is_idempotent(session: Session) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    jobs = []
    for index in range(5):
        job = _scored_game(session, target.practice_completed_at + timedelta(seconds=index + 1))
        jobs.append(job)
        _complete(job, session)
        assert len(session.exec(select(PatternAward)).all()) == (1 if index == 4 else 0)
        assert len(session.exec(select(PatternAwardEvidence)).all()) == (5 if index == 4 else 0)
        if index == 3:
            session.commit()
    award = session.exec(select(PatternAward)).one()
    evidence = session.exec(
        select(PatternAwardEvidence).order_by(PatternAwardEvidence.ordinal)
    ).all()
    assert award.player_id == target.player_id == PLAYER
    assert award.game_ids == target.window_game_ids
    assert award.window_start == award.game_ids[0]
    assert award.awarded_at.replace(tzinfo=UTC) == job.completed_at.replace(tzinfo=UTC)
    assert award.barrier_game_id is None
    assert award.barrier_analysis_job_id is None
    assert [row.ordinal for row in evidence] == [1, 2, 3, 4, 5]
    assert [row.game_id for row in evidence] == [job.game_id for job in jobs]
    assert [row.analysis_job_id for row in evidence] == [job.id for job in jobs]
    for row, source_job in zip(evidence, jobs, strict=True):
        source_game = session.get(Game, row.game_id)
        assert source_game is not None
        assert row.player_id == source_game.owner_player_id == award.player_id
        assert row.award_id == award.id
        assert row.game_created_at.replace(tzinfo=UTC) == source_game.created_at.replace(tzinfo=UTC)
        assert row.job_completed_at.replace(tzinfo=UTC) == (
            source_job.completed_at.replace(tzinfo=UTC)
        )
        assert (
            row.game_created_at.replace(tzinfo=UTC)
            <= row.job_completed_at.replace(tzinfo=UTC)
            <= award.awarded_at.replace(tzinfo=UTC)
        )
    advance_targets(PLAYER, session)
    assert len(session.exec(select(PatternAward)).all()) == 1
    assert len(session.exec(select(PatternAwardEvidence)).all()) == 5
    session.rollback()
    assert not session.exec(select(PatternAward)).all()
    assert not session.exec(select(PatternAwardEvidence)).all()


def test_evidence_insert_failure_rolls_back_award_and_completion(session: Session) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    jobs = [
        _scored_game(session, target.practice_completed_at + timedelta(seconds=index + 1))
        for index in range(5)
    ]
    for job in jobs[:4]:
        _complete(job, session)
    session.commit()

    def reject_evidence(*_: object) -> None:
        raise RuntimeError("evidence insert failed")

    event.listen(PatternAwardEvidence, "before_insert", reject_evidence)
    try:
        with pytest.raises(RuntimeError, match="evidence insert failed"):
            _complete(jobs[-1], session)
    finally:
        event.remove(PatternAwardEvidence, "before_insert", reject_evidence)
        session.rollback()
    assert not session.exec(select(PatternAward)).all()
    assert not session.exec(select(PatternAwardEvidence)).all()
    assert jobs[-1].status == "pending"
    assert jobs[-1].completed_at is None


def test_late_earlier_analysis_atomically_completes_the_chronological_five(
    session: Session,
) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    jobs = [
        _scored_game(session, target.practice_completed_at + timedelta(seconds=index + 1))
        for index in range(5)
    ]
    for job in jobs[1:]:
        _complete(job, session)
    assert not session.exec(select(PatternAward)).all()
    _complete(jobs[0], session)
    award = session.exec(select(PatternAward)).one()
    assert award.game_ids == [str(job.game_id) for job in jobs]
    assert award.awarded_at.replace(tzinfo=UTC) == jobs[0].completed_at.replace(tzinfo=UTC)
    assert award.barrier_game_id is None
    assert award.barrier_analysis_job_id is None
    evidence = session.exec(
        select(PatternAwardEvidence).order_by(PatternAwardEvidence.ordinal)
    ).all()
    assert [row.analysis_job_id for row in evidence] == [job.id for job in jobs]


def test_imports_and_unscored_games_do_not_fill_the_window(session: Session) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    imported = _scored_game(
        session, target.practice_completed_at + timedelta(seconds=1), imported=True
    )
    _complete(imported, session)
    assert target.window_game_ids == []
    short = Game(
        pgn="",
        moves=["e2e4"],
        result="1-0",
        owner_player_id=PLAYER,
        owner_color="white",
        created_at=target.practice_completed_at + timedelta(seconds=2),
    )
    session.add_all([short, PlaySession(player_id=PLAYER, game_id=short.id, status="completed")])
    job = AnalysisJob(game_id=short.id)
    session.add(job)
    session.flush()
    _complete(job, session)
    assert target.window_game_ids == []
    assert not session.exec(select(PatternAward)).all()


def test_uncapped_observation_resets_window_without_a_retained_lesson(session: Session) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    for index in range(5):
        job = _scored_game(session, target.practice_completed_at + timedelta(seconds=index + 1))
        if index == 3:
            session.add(
                ProfileObservation(
                    player_id=PLAYER,
                    game_id=str(job.game_id),
                    position_id=str(uuid4()),
                    skill_id=CODE,
                    observed_at=datetime.now(UTC),
                )
            )
        _complete(job, session)
    assert len(target.window_game_ids) == 1
    assert not session.exec(select(PatternAward)).all()


def test_current_invalidity_is_read_only_and_history_is_preserved(session: Session) -> None:
    target = _prepared_target(session)
    supporting = target.pattern_snapshot["supporting_moves"][0]["lesson_id"]
    schedule = session.get(ReviewSchedule, (PLAYER, supporting))
    assert schedule is not None
    schedule.retired_at = datetime.now(UTC)
    session.add(schedule)
    session.flush()
    before = len(session.exec(select(HabitCredit)).all())
    assert target_invalidity(target, session) is not None
    assert habit_summary(PLAYER, session)["targets"][0]["status"] == "invalidated"
    assert target.invalidated_at is None
    assert len(session.exec(select(HabitCredit)).all()) == before
    # A subsequent target write persists the same reason, not the GET above.
    job = _scored_game(session, datetime.now(UTC) - timedelta(minutes=1))
    _complete(job, session)
    assert target.invalidated_at is not None
    assert not session.exec(select(PatternAward)).all()


@pytest.mark.parametrize("recurs", [False, True])
def test_pending_earlier_game_blocks_later_five_until_barrier_completes(
    session: Session, recurs: bool
) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    jobs = [
        _scored_game(session, target.practice_completed_at + timedelta(seconds=index + 1))
        for index in range(6)
    ]
    for job in jobs[1:]:
        _complete(job, session)
    assert session.exec(select(PatternAward)).all() == []
    if recurs:
        session.add(
            ProfileObservation(
                player_id=PLAYER,
                game_id=str(jobs[0].game_id),
                position_id=str(uuid4()),
                skill_id=CODE,
                observed_at=datetime.now(UTC),
            )
        )
    _complete(jobs[0], session)
    award = session.exec(select(PatternAward)).one()
    expected = jobs[1:] if recurs else jobs[:5]
    assert award.game_ids == [str(job.game_id) for job in expected]
    assert award.awarded_at.replace(tzinfo=UTC) == jobs[0].completed_at.replace(tzinfo=UTC)
    assert award.barrier_game_id == (jobs[0].game_id if recurs else None)
    assert award.barrier_analysis_job_id == (jobs[0].id if recurs else None)
    evidence = session.exec(
        select(PatternAwardEvidence).order_by(PatternAwardEvidence.ordinal)
    ).all()
    assert len(evidence) == 5
    assert [row.analysis_job_id for row in evidence] == [job.id for job in expected]


def test_active_earlier_session_blocks_and_is_projected_until_finished(session: Session) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    jobs = [
        _scored_game(session, target.practice_completed_at + timedelta(seconds=index + 1))
        for index in range(6)
    ]
    game = session.get(Game, jobs[0].game_id)
    assert game is not None
    moves = list(game.moves)
    game.moves = []
    game.result = "*"
    play = session.exec(select(PlaySession).where(PlaySession.game_id == game.id)).one()
    play.status = "active"
    session.add_all([game, play])
    session.flush()
    for job in jobs[1:]:
        _complete(job, session)
    assert session.exec(select(PatternAward)).all() == []
    projected = habit_summary(PLAYER, session)["targets"][0]["blocking_game"]
    assert projected == {
        "game_id": str(game.id),
        "session_id": str(play.id),
        "reason": "unfinished",
    }
    game.moves, game.result, play.status = moves, "1/2-1/2", "completed"
    session.add_all([game, play])
    _complete(jobs[0], session)
    award = session.exec(select(PatternAward)).one()
    assert award.game_ids == [str(job.game_id) for job in jobs[:5]]


def test_short_finished_game_still_blocks_until_analysis_completes(session: Session) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    earlier = _scored_game(session, target.practice_completed_at + timedelta(seconds=1))
    game = session.get(Game, earlier.game_id)
    assert game is not None
    game.moves = game.moves[:2]
    session.add(game)
    later = _scored_game(session, target.practice_completed_at + timedelta(seconds=2))
    _complete(later, session)
    assert target.window_game_ids == []
    blocker = habit_summary(PLAYER, session)["targets"][0]["blocking_game"]
    assert blocker["game_id"] == str(game.id) and blocker["reason"] == "analysis"
    _complete(earlier, session)
    assert target.window_game_ids == [str(later.game_id)]


def test_short_analysis_barrier_releases_five_with_separate_provenance(session: Session) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    barrier = _scored_game(session, target.practice_completed_at + timedelta(seconds=1))
    game = session.get(Game, barrier.game_id)
    assert game is not None
    game.moves = game.moves[:2]
    session.add(game)
    jobs = [
        _scored_game(session, target.practice_completed_at + timedelta(seconds=index + 2))
        for index in range(5)
    ]
    for job in jobs:
        _complete(job, session)
    assert not session.exec(select(PatternAward)).all()
    assert not session.exec(select(PatternAwardEvidence)).all()
    _complete(barrier, session)
    award = session.exec(select(PatternAward)).one()
    evidence = session.exec(
        select(PatternAwardEvidence).order_by(PatternAwardEvidence.ordinal)
    ).all()
    assert award.game_ids == [str(job.game_id) for job in jobs]
    assert award.barrier_game_id == barrier.game_id
    assert award.barrier_analysis_job_id == barrier.id
    assert award.awarded_at.replace(tzinfo=UTC) == barrier.completed_at.replace(tzinfo=UTC)
    assert len(evidence) == 5
    assert [row.ordinal for row in evidence] == [1, 2, 3, 4, 5]
    assert [row.game_id for row in evidence] == [job.game_id for job in jobs]
    assert [row.analysis_job_id for row in evidence] == [job.id for job in jobs]
    assert all(row.game_id != barrier.game_id for row in evidence)


def test_award_evidence_selects_latest_completed_owned_game_job(session: Session) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    jobs = [
        _scored_game(session, target.practice_completed_at + timedelta(seconds=index + 1))
        for index in range(5)
    ]
    for job in jobs[:4]:
        _complete(job, session)
    replacement = AnalysisJob(
        game_id=jobs[0].game_id, status="completed", completed_at=datetime.now(UTC)
    )
    session.add_all(
        [
            replacement,
            AnalysisJob(
                game_id=jobs[0].game_id,
                status="completed",
                completed_at=target.practice_completed_at - timedelta(seconds=1),
            ),
            AnalysisJob(game_id=jobs[0].game_id, status="completed"),
            AnalysisJob(
                game_id=jobs[0].game_id,
                status="pending",
                completed_at=datetime.now(UTC) + timedelta(days=1),
            ),
            AnalysisJob(
                game_id=jobs[0].game_id,
                status="failed",
                completed_at=datetime.now(UTC) + timedelta(days=1),
            ),
        ]
    )
    _complete(jobs[-1], session)
    award = session.exec(select(PatternAward)).one()
    evidence = session.exec(
        select(PatternAwardEvidence).order_by(PatternAwardEvidence.ordinal)
    ).all()
    assert [row.analysis_job_id for row in evidence] == [
        replacement.id, *(job.id for job in jobs[1:])
    ]
    assert (
        evidence[0].job_completed_at.replace(tzinfo=UTC)
        == replacement.completed_at.replace(tzinfo=UTC)
    )
    assert all(
        row.job_completed_at.replace(tzinfo=UTC) <= award.awarded_at.replace(tzinfo=UTC)
        for row in evidence
    )


def test_summary_reads_persisted_award_evidence_without_rebuilding_sources(
    session: Session,
) -> None:
    target = _prepared_target(session)
    assert target.practice_completed_at is not None
    jobs = [
        _scored_game(session, target.practice_completed_at + timedelta(seconds=index + 1))
        for index in range(5)
    ]
    for job in jobs:
        _complete(job, session)
    session.commit()
    original = habit_summary(PLAYER, session)["targets"][0]["award"]
    assert len(original["evidence"]) == 5
    game = session.get(Game, jobs[0].game_id)
    assert game is not None
    game.created_at = datetime.now(UTC) + timedelta(days=1)
    jobs[0].completed_at = None
    jobs[0].status = "failed"
    target.window_game_ids = []
    session.add_all([game, jobs[0], target])
    session.commit()
    before = session.connection().connection.total_changes
    summary = habit_summary(PLAYER, session)
    assert summary["targets"][0]["award"] == original
    assert session.connection().connection.total_changes == before

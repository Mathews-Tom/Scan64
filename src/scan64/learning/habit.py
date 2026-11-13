"""Prospective, immutable habit evidence, independent of mastery and rating.

Only outcome writers create credits; projections never initialize a policy, daily
puzzle set, rating, target, or award. Callers own the surrounding transaction.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import chess
from chess_lesson_spec import LessonSpec
from pydantic import ValidationError
from sqlalchemy import or_
from sqlmodel import Session, col, select

from scan64.api.models import Player
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedLessonOpportunity,
)
from scan64.chess.boards import board_from
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.content.models import ContentAttempt, ContentItem, LessonAttempt, StudySession
from scan64.content.puzzle_practice import lock_puzzle_player
from scan64.content.puzzle_practice_models import PuzzleDailySet, PuzzleDailySlot
from scan64.content.puzzle_runs import PuzzleRun, matches_completed_solution
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.diagnosis.visibility import visible_game_lesson
from scan64.learning.habit_models import (
    HABIT_POLICY_VERSION,
    HabitCredit,
    HabitDay,
    HabitGoal,
    HabitPolicy,
    PatternAward,
    PatternAwardEvidence,
    PatternTarget,
)
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.learning.tolerance import review_probability

DEFAULT_GOAL = 3
PRACTICE_SOLVES = 3
PRACTICE_GAMES = 2
WINDOW_GAMES = 5
MINIMUM_OWNER_MOVES = 20
EVIDENCE_LIMITS = (
    "Lessons are server-graded first recorded attempts with app-reported zero hints; "
    "lesson answers and hints are available in the app, so independence is not server-proven. "
    "Puzzles require a server-unhinted terminal solve. Each source position counts once per "
    "UTC day across modes. Pattern practice requires three distinct positions across at least "
    "two games, then no observation in five fully scored, terminal Scan64 play-session games "
    "with at least 20 owner moves each. Diagnosis uses a fast-pass swing threshold of at "
    "least 150cp; absence is bounded by that threshold and game coverage, not proof of cure, "
    "mastery, or increased chess strength."
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def ensure_policy(player_id: str, session: Session) -> HabitPolicy:
    """Initialize only on a write, before the player's first terminal outcome.

    Existing players are seeded at deployment by the migration. Missing policies
    start *now*, never at an old game, attempt, puzzle slot, or imported timestamp.
    """
    lock_puzzle_player(player_id, session)
    policy = session.get(HabitPolicy, player_id)
    if policy is None:
        if session.get(Player, player_id) is None:
            raise ValueError("Player not found")
        policy = HabitPolicy(player_id=player_id, effective_at=_now())
        session.add(policy)
        session.flush()
    return policy


def _goal_for(player_id: str, utc_day: str, session: Session) -> int:
    goal = session.exec(
        select(HabitGoal)
        .where(HabitGoal.player_id == player_id, HabitGoal.effective_day <= utc_day)
        .order_by(col(HabitGoal.effective_day).desc())
        .limit(1)
    ).first()
    return goal.goal if goal is not None else DEFAULT_GOAL


def edit_goal(player_id: str, goal: int, session: Session) -> None:
    if isinstance(goal, bool) or not isinstance(goal, int) or goal < 1:
        raise ValueError("Daily goal must be a positive integer")
    ensure_policy(player_id, session)
    effective_day = (_now().date() + timedelta(days=1)).isoformat()
    pending = session.get(HabitGoal, (player_id, effective_day))
    if pending is None:
        pending = HabitGoal(player_id=player_id, effective_day=effective_day, goal=goal)
    else:
        pending.goal = goal
        pending.created_at = _now()
    session.add(pending)


def _lesson_source(
    opportunity: PersistedLessonOpportunity, player_id: str, session: Session
) -> tuple[LessonSpec, Position, Game] | None:
    """Current trust/ownership proof shared by credits, supply and invalidity."""
    if (
        opportunity.player_id != player_id
        or opportunity.verification_status != "verified"
        or opportunity.verification_analysis_id is None
    ):
        return None
    schedule = session.get(ReviewSchedule, (player_id, str(opportunity.id)))
    if schedule is None or schedule.retired_at is not None:
        return None
    try:
        spec = visible_game_lesson(opportunity, session)
    except (KeyError, TypeError, ValidationError, ValueError):
        return None
    if (
        spec is None
        or spec.diagnosis is None
        or spec.diagnosis.primary not in TRUSTED_CODES
        or spec.verification.status != "verified"
    ):
        return None
    state = session.get(SkillState, (player_id, spec.diagnosis.primary))
    if state is not None and state.retired_at is not None:
        return None
    game = session.get(Game, opportunity.game_id)
    position = session.get(Position, opportunity.source_position_id)
    analysis = session.get(EngineAnalysis, opportunity.verification_analysis_id)
    if (
        game is None
        or game.owner_player_id != player_id
        or game.owner_color not in ("white", "black")
        or position is None
        or position.game_id != game.id
        or position.fen != spec.source.fen
        or analysis is None
        or analysis.position_id != position.id
        or not analysis.raw_result
    ):
        return None
    try:
        source = chess.Board(position.fen)
        if not source.is_valid():
            return None
        board = board_from(game.headers.get("FEN"))
        for move in game.moves:
            if (
                board.fen() == position.fen
                and ("white" if board.turn else "black") == game.owner_color
            ):
                return spec, position, game
            board.push_uci(move)
    except ValueError:
        return None
    return None


def _qualified_lesson(
    attempt: LessonAttempt, session: Session
) -> tuple[PersistedLessonOpportunity, str, dict[str, Any]] | None:
    if (
        attempt.source_kind != "persisted_opportunity"
        or attempt.opportunity_id is None
        or attempt.lesson_id != str(attempt.opportunity_id)
        or attempt.grading_status != "verified"
        or attempt.success is not True
        or attempt.hints_used != 0
        or attempt.submitted_move is None
    ):
        return None
    study = session.get(StudySession, attempt.session_id)
    if study is None or study.player_id != attempt.player_id:
        return None
    opportunity = session.get(PersistedLessonOpportunity, attempt.opportunity_id)
    if opportunity is None:
        return None
    source = _lesson_source(opportunity, attempt.player_id, session)
    if source is None:
        return None
    first = session.exec(
        select(LessonAttempt)
        .where(LessonAttempt.opportunity_id == opportunity.id)
        .order_by(col(LessonAttempt.completed_at), col(LessonAttempt.id))
        .limit(1)
    ).first()
    if first is None or first.id != attempt.id:
        return None
    spec, position, game = source
    if spec.interaction.maximum_attempts < 1 or spec.diagnosis is None:
        return None
    return (
        opportunity,
        chess.Board(position.fen).epd(),
        {
            "opportunity_id": str(opportunity.id),
            "game_id": str(game.id),
            "position_id": str(position.id),
            "code": spec.diagnosis.primary,
            "verification_analysis_id": str(opportunity.verification_analysis_id),
            "evidence_references": list(spec.diagnosis.evidence_refs),
            "hints_authority": "app_reported",
            "hints_used": 0,
        },
    )


def _record_credit(
    player_id: str,
    source_kind: str,
    source_id: str,
    source_key: str,
    source_epd: str,
    completed_at: datetime,
    trust_snapshot: dict[str, Any],
    policy: HabitPolicy,
    session: Session,
) -> None:
    if policy.policy_version != HABIT_POLICY_VERSION or _utc(completed_at) < _utc(
        policy.effective_at
    ):
        return
    utc_day = _utc(completed_at).date().isoformat()
    duplicate = session.exec(
        select(HabitCredit.id).where(
            HabitCredit.player_id == player_id,
            or_(
                (col(HabitCredit.source_kind) == source_kind)
                & (col(HabitCredit.source_id) == source_id),
                (col(HabitCredit.utc_day) == utc_day)
                & or_(
                    col(HabitCredit.source_key) == source_key,
                    col(HabitCredit.source_epd) == source_epd,
                ),
            ),
        )
    ).first()
    if duplicate is not None:
        return
    day = session.get(HabitDay, (player_id, utc_day))
    if day is None:
        day = HabitDay(
            player_id=player_id,
            utc_day=utc_day,
            goal=_goal_for(player_id, utc_day, session),
            policy_version=policy.policy_version,
        )
        session.add(day)
    session.add(
        HabitCredit(
            player_id=player_id,
            utc_day=utc_day,
            source_kind=source_kind,
            source_id=source_id,
            source_key=source_key,
            source_epd=source_epd,
            completed_at=completed_at,
            policy_version=policy.policy_version,
            trust_snapshot=trust_snapshot,
        )
    )
    session.flush()
    sources = session.exec(
        select(HabitCredit.completed_at).where(
            HabitCredit.player_id == player_id, HabitCredit.utc_day == utc_day
        )
    ).all()
    if day.met_at is None and len(sources) >= day.goal:
        day.met_at = sorted(_utc(value) for value in sources)[day.goal - 1]
        session.add(day)


def target_invalidity(target: PatternTarget, session: Session) -> str | None:
    """Project current source withdrawal without re-ranking or changing history."""
    with session.no_autoflush:
        if target.invalidation_reason is not None:
            return target.invalidation_reason
        if target.policy_version != HABIT_POLICY_VERSION:
            return "The target uses an unsupported habit policy."
        if target.code not in TRUSTED_CODES:
            return "The tracked diagnosis is no longer trusted."
        moves = target.pattern_snapshot.get("supporting_moves")
        if not isinstance(moves, list) or not moves:
            return "The target's supporting lesson evidence is missing."
        for move in moves:
            if not isinstance(move, dict) or not isinstance(move.get("lesson_id"), str):
                return "The target's supporting lesson evidence is invalid."
            try:
                opportunity = session.get(PersistedLessonOpportunity, UUID(move["lesson_id"]))
            except ValueError:
                return "The target's supporting lesson evidence is invalid."
            if opportunity is None:
                return "A supporting lesson was removed or its ownership changed."
            source = _lesson_source(opportunity, target.player_id, session)
            if source is None:
                return "A supporting lesson is withdrawn, retired, untrusted, or no longer owned."
            spec, _, game = source
            if (
                spec.diagnosis is None
                or spec.diagnosis.primary != target.code
                or str(game.id) != move.get("game_id")
            ):
                return "A supporting lesson no longer proves the tracked diagnosis."
        return None


def _targets(player_id: str, session: Session) -> list[PatternTarget]:
    return list(
        session.exec(
            select(PatternTarget)
            .where(PatternTarget.player_id == player_id)
            .order_by(col(PatternTarget.started_at), col(PatternTarget.id))
        ).all()
    )


def _persist_invalidity(target: PatternTarget, session: Session) -> bool:
    reason = target_invalidity(target, session)
    if reason is not None:
        if target.invalidated_at is None:
            target.invalidated_at = _now()
            target.invalidation_reason = reason
            session.add(target)
        return True
    return False


def record_lesson_credit(attempt: LessonAttempt, session: Session) -> None:
    policy = ensure_policy(attempt.player_id, session)
    session.flush()
    targets = _targets(attempt.player_id, session)
    active = [target for target in targets if not _persist_invalidity(target, session)]
    qualified = _qualified_lesson(attempt, session)
    if qualified is None or _utc(attempt.completed_at) < _utc(policy.effective_at):
        return
    opportunity, epd, snapshot = qualified
    _record_credit(
        attempt.player_id,
        "lesson",
        attempt.id,
        f"lesson:{opportunity.id}",
        epd,
        attempt.completed_at,
        snapshot,
        policy,
        session,
    )
    # Day deduplication deliberately does not suppress independently qualified
    # target practice: one position can appear in several historical lessons.
    for target in active:
        if (
            target.code != snapshot["code"]
            or target.policy_version != policy.policy_version
            or target.practice_completed_at is not None
            or _utc(attempt.completed_at) < _utc(target.started_at)
            or any(entry.get("source_epd") == epd for entry in target.practice)
        ):
            continue
        practice = [
            *target.practice,
            {
                "attempt_id": attempt.id,
                "opportunity_id": str(opportunity.id),
                "game_id": str(opportunity.game_id),
                "source_epd": epd,
                "completed_at": _utc(attempt.completed_at).isoformat(),
            },
        ]
        target.practice = practice
        if (
            len(practice) >= PRACTICE_SOLVES
            and len({entry["game_id"] for entry in practice}) >= PRACTICE_GAMES
        ):
            target.practice_completed_at = attempt.completed_at
        session.add(target)


def record_puzzle_credit(run: PuzzleRun, session: Session) -> None:
    policy = ensure_policy(run.player_id, session)
    session.flush()
    if run.state != "solved" or run.completed_at is None or run.hint_steps:
        return
    attempt = session.get(ContentAttempt, run.id)
    study = session.get(StudySession, run.session_id)
    item = session.get(ContentItem, run.item_id)
    if (
        attempt is None
        or not attempt.success
        or attempt.hint_assisted
        or attempt.item_id != run.item_id
        or attempt.player_id != run.player_id
        or attempt.session_id != run.session_id
        or attempt.completed_at is None
        or _utc(attempt.completed_at) != _utc(run.completed_at)
        or _utc(attempt.started_at) != _utc(run.started_at)
        or attempt.response_payload.get("run_id") != run.id
        or attempt.response_payload.get("wrong_attempts") != run.wrong_attempts
        or study is None
        or study.player_id != run.player_id
        or study.domain != "puzzles"
        or item is None
        or item.domain != "puzzles"
    ):
        return
    try:
        spec = LessonSpec.model_validate(item.payload["lesson"])
        source = chess.Board(spec.source.fen)
        if (
            not source.is_valid()
            or spec.schema_version != "0.2.0"
            or spec.source.kind != "external"
            or spec.verification.status != "source_verified"
            or not spec.interaction.solution
            or len(spec.interaction.solution) % 2 != 1
            or run.cursor != len(spec.interaction.solution)
        ):
            return
        for move in run.played_moves:
            source.push_uci(move)
        if source.fen() != run.fen or not matches_completed_solution(
            run.played_moves,
            spec.interaction.solution,
            source,
        ):
            return
    except (KeyError, TypeError, ValidationError, ValueError):
        return
    _record_credit(
        run.player_id,
        "puzzle",
        run.id,
        f"puzzle:{item.id}",
        chess.Board(spec.source.fen).epd(),
        run.completed_at,
        {
            "item_id": item.id,
            "run_id": run.id,
            "content_attempt_id": attempt.id,
            "hints_authority": "server",
            "hint_steps": {},
        },
        policy,
        session,
    )


def start_target(player_id: str, code: str, session: Session) -> PatternTarget:
    policy = ensure_policy(player_id, session)
    # The existing M67 projection is authoritative; themes and Beta priors are
    # neither pattern identities nor substitutes for retained owner evidence.
    from scan64.api.reports import read_player_patterns

    targets = _targets(player_id, session)
    for target in targets:
        invalid = _persist_invalidity(target, session)
        if target.code == code and target.policy_version == policy.policy_version:
            if invalid:
                raise ValueError("The existing target's supporting evidence has been withdrawn")
            return target
    patterns = read_player_patterns(player_id, session)
    pattern = next((item for item in patterns.recurring_diagnoses if item.diagnosis == code), None)
    if pattern is None or patterns.status != "recurring_diagnosis":
        raise ValueError("No currently qualifying recurring pattern exists for this code")
    target = PatternTarget(
        player_id=player_id,
        code=code,
        policy_version=policy.policy_version,
        started_at=_now(),
        pattern_snapshot=pattern.model_dump(mode="json"),
    )
    session.add(target)
    session.flush()
    return target


def _fully_scored_owner_moves(game: Game, session: Session) -> bool:
    rows = {
        row.ply: row
        for row in session.exec(
            select(GameMoveReview).where(GameMoveReview.game_id == game.id)
        ).all()
    }
    owner_moves = 0
    try:
        board = board_from(game.headers.get("FEN"))
        if not board.is_valid():
            return False
        for ply, move in enumerate(game.moves, 1):
            owner_move = ("white" if board.turn else "black") == game.owner_color
            if not owner_move:
                board.push_uci(move)
                continue
            owner_moves += 1
            row = rows.get(ply)
            if row is None or row.mover != game.owner_color or row.move_uci != move:
                return False
            source = session.get(Position, row.source_position_id)
            reached = session.get(Position, row.reached_position_id)
            before = session.get(EngineAnalysis, row.before_analysis_id)
            after = session.get(EngineAnalysis, row.after_analysis_id)
            if (
                source is None
                or reached is None
                or before is None
                or after is None
                or source.game_id != game.id
                or reached.game_id != game.id
                or source.fen != board.fen()
                or before.position_id != source.id
                or after.position_id != reached.id
            ):
                return False
            before_probability, before_terminal = review_probability(
                board, before.raw_result[0] if before.raw_result else {}
            )
            board.push_uci(move)
            after_probability, after_terminal = review_probability(
                board, after.raw_result[0] if after.raw_result else {}
            )
            if (
                reached.fen != board.fen()
                or before_probability is None
                or after_probability is None
                or not math.isfinite(before_probability)
                or not math.isfinite(after_probability)
                or row.before_probability != before_probability
                or row.after_probability != after_probability
                or row.before_terminal != before_terminal
                or row.after_terminal != after_terminal
            ):
                return False
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return False
    return owner_moves >= MINIMUM_OWNER_MOVES


def _observation_games(
    target: PatternTarget, session: Session
) -> tuple[list[Game], dict[str, str] | None]:
    if target.practice_completed_at is None:
        return [], None
    candidates = session.exec(
        select(Game)
        .where(
            Game.owner_player_id == target.player_id,
            col(Game.owner_color).in_(("white", "black")),
            Game.created_at > target.practice_completed_at,
        )
        .order_by(col(Game.created_at), col(Game.id))
    ).all()
    eligible: list[Game] = []
    for game in candidates:
        plays = session.exec(
            select(PlaySession)
            .where(
                PlaySession.game_id == game.id,
                PlaySession.player_id == target.player_id,
            )
            .order_by(col(PlaySession.id))
        ).all()
        if not plays:
            continue
        unfinished = next((play for play in plays if play.status != "completed"), None)
        if unfinished is not None or game.result not in {"1-0", "0-1", "1/2-1/2"}:
            play = unfinished or plays[0]
            return eligible, {
                "game_id": str(game.id),
                "session_id": str(play.id),
                "reason": "unfinished",
            }
        completed = session.exec(
            select(AnalysisJob.id).where(
                AnalysisJob.game_id == game.id,
                AnalysisJob.status == "completed",
                col(AnalysisJob.completed_at).is_not(None),
            )
        ).first()
        if completed is None:
            return eligible, {
                "game_id": str(game.id),
                "session_id": str(plays[0].id),
                "reason": "analysis",
            }
        try:
            board = board_from(game.headers.get("FEN"))
            owner_starts = ("white" if board.turn else "black") == game.owner_color
        except ValueError:
            continue
        if (len(game.moves) + int(owner_starts)) // 2 < MINIMUM_OWNER_MOVES:
            continue
        if _fully_scored_owner_moves(game, session):
            eligible.append(game)
    return eligible, None


def _recurrence(game: Game, target: PatternTarget, session: Session) -> bool:
    observation = session.exec(
        select(ProfileObservation).where(
            ProfileObservation.player_id == target.player_id,
            ProfileObservation.game_id == str(game.id),
            ProfileObservation.skill_id == target.code,
            col(ProfileObservation.retired_at).is_(None),
        )
    ).first()
    if observation is not None:
        return True
    rows = session.exec(
        select(PersistedLessonOpportunity).where(
            PersistedLessonOpportunity.game_id == game.id,
            PersistedLessonOpportunity.player_id == target.player_id,
        )
    ).all()
    for row in rows:
        source = _lesson_source(row, target.player_id, session)
        if source is not None and source[0].diagnosis is not None:
            if source[0].diagnosis.primary == target.code:
                return True
    return False


def _persist_award(
    target: PatternTarget, window: list[str], completion: AnalysisJob, session: Session
) -> None:
    """Snapshot the five owned sources in the outcome writer's transaction."""
    sources: dict[str, tuple[Game, AnalysisJob]] = {}
    for game, job in session.exec(
        select(Game, AnalysisJob)
        .join(AnalysisJob, col(AnalysisJob.game_id) == col(Game.id))
        .where(
            Game.owner_player_id == target.player_id,
            col(Game.id).in_([UUID(game_id) for game_id in window]),
            AnalysisJob.status == "completed",
            col(AnalysisJob.completed_at).is_not(None),
            col(AnalysisJob.completed_at) >= col(Game.created_at),
            col(AnalysisJob.completed_at) <= completion.completed_at,
        )
        .order_by(col(AnalysisJob.completed_at).desc(), col(AnalysisJob.id).desc())
    ).all():
        sources.setdefault(str(game.id), (game, job))
    if len(sources) != WINDOW_GAMES:
        return
    separate_barrier = str(completion.game_id) not in window
    award = PatternAward(
        player_id=target.player_id,
        target_id=target.id,
        code=target.code,
        policy_version=target.policy_version,
        window_start=window[0],
        game_ids=list(window),
        awarded_at=completion.completed_at,
        barrier_game_id=completion.game_id if separate_barrier else None,
        barrier_analysis_job_id=completion.id if separate_barrier else None,
    )
    session.add(award)
    session.flush()
    for ordinal, game_id in enumerate(window, 1):
        game, job = sources[game_id]
        session.add(
            PatternAwardEvidence(
                player_id=target.player_id,
                award_id=award.id,
                ordinal=ordinal,
                game_id=game.id,
                analysis_job_id=job.id,
                game_created_at=game.created_at,
                job_completed_at=job.completed_at,
            )
        )


def advance_targets(player_id: str, session: Session) -> None:
    """Called only after flushing the completed job marker, before its commit."""
    if (
        session.exec(
            select(PatternTarget.id).where(PatternTarget.player_id == player_id).limit(1)
        ).first()
        is None
    ):
        # Legacy analyses may name an owner without a registered player. They
        # still complete normally; there is no owned habit target to advance.
        return
    ensure_policy(player_id, session)
    completion = session.exec(
        select(AnalysisJob)
        .join(Game, col(AnalysisJob.game_id) == col(Game.id))
        .where(
            Game.owner_player_id == player_id,
            AnalysisJob.status == "completed",
            col(AnalysisJob.completed_at).is_not(None),
        )
        .order_by(col(AnalysisJob.completed_at).desc(), col(AnalysisJob.id).desc())
        .limit(1)
    ).first()
    if completion is None or completion.completed_at is None:
        return
    for target in _targets(player_id, session):
        already_invalidated = target.invalidated_at is not None
        invalid = _persist_invalidity(target, session)
        if already_invalidated or target.practice_completed_at is None:
            continue
        historical = session.exec(
            select(PatternAward.id).where(PatternAward.target_id == target.id)
        ).first()
        if historical is not None:
            continue
        window: list[str] = []
        for game in _observation_games(target, session)[0]:
            if _recurrence(game, target, session):
                window = []
            else:
                window = [*window, str(game.id)][-WINDOW_GAMES:]
            # The last required analysis can be a preceding recurrence barrier.
            # Its completion makes the chronological clean window knowable.
            if not invalid and len(window) == WINDOW_GAMES:
                award = session.exec(
                    select(PatternAward.id).where(
                        PatternAward.player_id == player_id,
                        PatternAward.code == target.code,
                        PatternAward.policy_version == target.policy_version,
                        PatternAward.window_start == window[0],
                    )
                ).first()
                if award is None:
                    _persist_award(target, window, completion, session)
                break
        target.window_game_ids = window
        session.add(target)
    session.flush()


def _withdrawn(credit: HabitCredit, session: Session) -> bool:
    if credit.source_kind == "lesson":
        opportunity_id = credit.trust_snapshot.get("opportunity_id")
        if not isinstance(opportunity_id, str):
            return True
        try:
            opportunity = session.get(PersistedLessonOpportunity, UUID(opportunity_id))
        except ValueError:
            return True
        return opportunity is None or _lesson_source(opportunity, credit.player_id, session) is None
    item_id = credit.trust_snapshot.get("item_id")
    return not isinstance(item_id, str) or session.get(ContentItem, item_id) is None


def _supply(player_id: str, today: str, session: Session) -> dict[str, Any]:
    day = session.exec(
        select(PuzzleDailySet).where(
            PuzzleDailySet.player_id == player_id,
            PuzzleDailySet.utc_day == today,
        )
    ).first()
    slots = (
        list(
            session.exec(
                select(PuzzleDailySlot).where(
                    PuzzleDailySlot.set_id == day.id,
                    col(PuzzleDailySlot.closed_at).is_(None),
                )
            ).all()
        )
        if day is not None
        else []
    )
    seen = select(PuzzleRun.item_id).where(PuzzleRun.player_id == player_id)
    attempted = select(ContentAttempt.item_id).where(ContentAttempt.player_id == player_id)
    extra = select(ContentItem.id).where(
        ContentItem.domain == "puzzles",
        ~col(ContentItem.id).in_(seen),
        ~col(ContentItem.id).in_(attempted),
    )
    if slots:
        extra = extra.where(~col(ContentItem.id).in_([slot.item_id for slot in slots]))
    attempted_lessons = select(LessonAttempt.opportunity_id).where(
        col(LessonAttempt.opportunity_id).is_not(None)
    )
    opportunities = session.exec(
        select(PersistedLessonOpportunity).where(
            PersistedLessonOpportunity.player_id == player_id,
            ~col(PersistedLessonOpportunity.id).in_(attempted_lessons),
        )
    ).all()
    credited_epds = set(
        session.exec(
            select(HabitCredit.source_epd).where(
                HabitCredit.player_id == player_id,
                HabitCredit.utc_day == today,
            )
        ).all()
    )
    retry_epds = set()
    for opportunity in opportunities:
        source = _lesson_source(opportunity, player_id, session)
        if source is not None and source[0].interaction.maximum_attempts > 0:
            epd = chess.Board(source[1].fen).epd()
            if epd not in credited_epds:
                retry_epds.add(epd)
    return {
        "remaining_daily_slots": sum(slot.run_id is None for slot in slots),
        "eligible_lesson_retries": len(retry_epds),
        "extra_modes_available": session.exec(extra.limit(1)).first() is not None,
    }


def habit_summary(player_id: str, session: Session) -> dict[str, Any]:
    """Read-only calendar/target projection; never replay outcome rewards."""
    with session.no_autoflush:
        now = _now()
        today = now.date().isoformat()
        rows = list(
            session.exec(
                select(HabitDay)
                .where(HabitDay.player_id == player_id)
                .order_by(col(HabitDay.utc_day))
            ).all()
        )
        credits = list(
            session.exec(
                select(HabitCredit).where(
                    HabitCredit.player_id == player_id,
                )
            ).all()
        )
        counts = Counter(credit.utc_day for credit in credits)
        withdrawn = Counter(credit.utc_day for credit in credits if _withdrawn(credit, session))
        by_day = {row.utc_day: row for row in rows}
        today_row = by_day.get(today)
        goal = today_row.goal if today_row is not None else _goal_for(player_id, today, session)
        pending = session.exec(
            select(HabitGoal)
            .where(
                HabitGoal.player_id == player_id,
                HabitGoal.effective_day > today,
            )
            .order_by(col(HabitGoal.effective_day))
            .limit(1)
        ).first()
        met_days = {
            date.fromisoformat(row.utc_day)
            for row in rows
            if row.utc_day < today and row.met_at is not None
        }
        current = 0
        cursor = now.date() - timedelta(days=1)
        while cursor in met_days:
            current += 1
            cursor -= timedelta(days=1)
        best = running = 0
        previous: date | None = None
        for day in sorted(met_days):
            running = (
                running + 1 if previous is not None and day - previous == timedelta(days=1) else 1
            )
            best = max(best, running)
            previous = day
        days = [
            {
                "utc_day": row.utc_day,
                "goal": row.goal,
                "qualifying_solves": counts[row.utc_day],
                "met": row.met_at is not None,
                "withdrawn_sources": withdrawn[row.utc_day],
            }
            for row in rows
        ]
        if today_row is None:
            days.append(
                {
                    "utc_day": today,
                    "goal": goal,
                    "qualifying_solves": 0,
                    "met": False,
                    "withdrawn_sources": 0,
                }
            )
        targets = []
        for target in _targets(player_id, session):
            invalidity = target_invalidity(target, session)
            award = session.exec(
                select(PatternAward)
                .where(
                    PatternAward.target_id == target.id,
                    PatternAward.player_id == player_id,
                )
                .order_by(col(PatternAward.awarded_at), col(PatternAward.id))
                .limit(1)
            ).first()
            award_data = None
            if award is not None:
                award_data = award.model_dump(mode="json")
                award_data["evidence"] = [
                    evidence.model_dump(mode="json")
                    for evidence in session.exec(
                        select(PatternAwardEvidence)
                        .where(
                            PatternAwardEvidence.award_id == award.id,
                            PatternAwardEvidence.player_id == player_id,
                        )
                        .order_by(col(PatternAwardEvidence.ordinal))
                    ).all()
                ]
            status = (
                "invalidated"
                if invalidity is not None
                else "met"
                if award is not None
                else "observing"
                if target.practice_completed_at is not None
                else "practicing"
            )
            blocking_game = (
                _observation_games(target, session)[1] if status == "observing" else None
            )
            targets.append(
                {
                    "id": target.id,
                    "code": target.code,
                    "status": status,
                    "practice_count": len(target.practice),
                    "practice_game_count": len({entry["game_id"] for entry in target.practice}),
                    "eligible_window_count": len(target.window_game_ids),
                    "blocking_game": blocking_game,
                    "invalidity_reason": invalidity,
                    "award": award_data,
                    "pattern_snapshot": target.pattern_snapshot,
                }
            )
        return {
            "utc_day": today,
            "goal": goal,
            "qualifying_solves": counts[today],
            "remaining": max(0, goal - counts[today]),
            "pending_goal": pending.goal if pending is not None else None,
            "pending_effective_day": pending.effective_day if pending is not None else None,
            "current_streak": current,
            "best_streak": best,
            "reset_at": datetime.combine(now.date() + timedelta(days=1), time(), UTC).isoformat(),
            "day_status": "in_progress",
            "days": days,
            "supply": _supply(player_id, today, session),
            "targets": targets,
            "evidence_limits": EVIDENCE_LIMITS,
        }

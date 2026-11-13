from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, date, datetime
from math import isclose, isfinite
from typing import Any
from uuid import uuid4

import chess
from chess_lesson_spec import LessonSpec
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import update
from sqlmodel import Session, SQLModel, col, delete, select

from scan64.api.auth import require_player_token
from scan64.api.middleware import IdempotencyRecord
from scan64.api.models import (
    DeletionAudit,
    Player,
    PlayerCredential,
    PlayerOnboarding,
    PlayerProfile,
)
from scan64.api.onboarding import INTRO_TARGET, LEVEL_MAPPING, MAPPING_VERSION, read_onboarding, utc
from scan64.chess.analysis.credit import MAX_POSITIVE_CREDITS_PER_GAME_SKILL, positive_move_proof
from scan64.chess.analysis.credit_models import PositiveMoveCredit
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedDiagnosis,
    PersistedLessonOpportunity,
)
from scan64.chess.games.assistance import SessionHintReveal, prefix_hash
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.participants import player_color
from scan64.chess.positions.models import Position
from scan64.coach.models import CoachStudentLink
from scan64.content.models import ContentAttempt, ContentItem, LessonAttempt, StudySession
from scan64.content.puzzle_mode_models import PuzzleModeBinding
from scan64.content.puzzle_practice_models import (
    INITIAL_RATING,
    POLICY_VERSION,
    THEME_MAP_VERSION,
    PuzzleDailySet,
    PuzzleDailySlot,
    PuzzleRating,
    PuzzleRatingEvent,
    elo_after,
)
from scan64.content.puzzle_runs import PuzzleRun, matches_completed_solution
from scan64.content.validate import validate_solution_line
from scan64.learning.evaluation.transfer_measurement import TransferMeasurement
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.transfer import TransferPosition
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
from scan64.learning.tolerance import review_loss, review_probability
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson
from scan64.persistence.database import get_session

router = APIRouter(tags=["data_lifecycle"])


class ExportRequest(BaseModel):
    player_id: str


class ExportArchive(BaseModel):
    player: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    onboarding: dict[str, Any] | None = None
    play_sessions: list[dict[str, Any]] = Field(default_factory=list)
    session_hint_reveals: list[dict[str, Any]] = Field(default_factory=list)
    positive_move_credits: list[dict[str, Any]] = Field(default_factory=list)
    games: list[dict[str, Any]] = Field(default_factory=list)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    engine_analyses: list[dict[str, Any]] = Field(default_factory=list)
    review_moves: list[dict[str, Any]] = Field(default_factory=list)
    analysis_jobs: list[dict[str, Any]] = Field(default_factory=list)
    lesson_opportunities: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    profile_observations: list[dict[str, Any]] = Field(default_factory=list)
    lesson_attempts: list[dict[str, Any]] = Field(default_factory=list)
    transfer_positions: list[dict[str, Any]] = Field(default_factory=list)
    transfer_measurements: list[dict[str, Any]] = Field(default_factory=list)
    coach_student_links: list[dict[str, Any]] = Field(default_factory=list)
    skill_states: list[dict[str, Any]] = Field(default_factory=list)
    review_schedules: list[dict[str, Any]] = Field(default_factory=list)
    study_sessions: list[dict[str, Any]] = Field(default_factory=list)
    content_attempts: list[dict[str, Any]] = Field(default_factory=list)
    puzzle_runs: list[dict[str, Any]] = Field(default_factory=list)
    puzzle_mode_bindings: list[dict[str, Any]] = Field(default_factory=list)
    puzzle_content_items: list[dict[str, Any]] = Field(default_factory=list)
    puzzle_rating: dict[str, Any] | None = None
    puzzle_daily_sets: list[dict[str, Any]] = Field(default_factory=list)
    puzzle_daily_slots: list[dict[str, Any]] = Field(default_factory=list)
    puzzle_rating_events: list[dict[str, Any]] = Field(default_factory=list)
    habit_policy: dict[str, Any] | None = None
    habit_goals: list[dict[str, Any]] = Field(default_factory=list)
    habit_days: list[dict[str, Any]] = Field(default_factory=list)
    habit_credits: list[dict[str, Any]] = Field(default_factory=list)
    pattern_targets: list[dict[str, Any]] = Field(default_factory=list)
    pattern_awards: list[dict[str, Any]] = Field(default_factory=list)
    pattern_award_evidence: list[dict[str, Any]] = Field(default_factory=list)
    credential_hash: str | None = None

_HABIT_V1_CODES = frozenset({
    "board_awareness.hanging_piece",
    "threat_processing.missed_check",
    "threat_processing.missed_capture",
    "threat_processing.missed_direct_threat",
})
_HABIT_INVALIDATION_REASONS = frozenset({
    "The target uses an unsupported habit policy.",
    "The tracked diagnosis is no longer trusted.",
    "The target's supporting lesson evidence is missing.",
    "The target's supporting lesson evidence is invalid.",
    "A supporting lesson was removed or its ownership changed.",
    "A supporting lesson is withdrawn, retired, untrusted, or no longer owned.",
    "A supporting lesson no longer proves the tracked diagnosis.",
})


def _habit_time(value: Any) -> datetime:
    if type(value) is not str or "T" not in value:
        raise ValueError("Invalid habit timestamp")
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _habit_day(value: Any) -> str:
    if type(value) is not str or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Invalid habit UTC day")
    return value


def _habit_records[HabitRow: SQLModel](
    model: type[HabitRow], records: list[dict[str, Any]], player_id: str,
) -> list[HabitRow]:
    result = []
    for data in records:
        if set(data) != set(model.model_fields) or data.get("player_id") != player_id:
            raise ValueError("Invalid habit fields or owner")
        for name, value in data.items():
            if name in {"goal", "ordinal"}:
                if type(value) is not int or value < 1 or name == "ordinal" and value > 5:
                    raise ValueError("Invalid habit goal or evidence ordinal")
            elif name in {"trust_snapshot", "pattern_snapshot"}:
                if type(value) is not dict:
                    raise ValueError("Invalid habit snapshot")
            elif name in {"practice", "window_game_ids", "game_ids"}:
                if type(value) is not list:
                    raise ValueError("Invalid habit evidence list")
            elif name.endswith("_at"):
                if value is not None:
                    if _habit_time(value) > datetime.now(UTC):
                        raise ValueError("Habit timestamp is in the future")
                elif name not in {"met_at", "practice_completed_at", "invalidated_at"}:
                    raise ValueError("Missing habit timestamp")
            elif name in {"utc_day", "effective_day"}:
                _habit_day(value)
            elif value is None and name == "invalidation_reason":
                pass
            elif value is None and name in {"barrier_game_id", "barrier_analysis_job_id"}:
                pass
            elif type(value) is not str or not value:
                raise ValueError("Invalid habit scalar")
        if "policy_version" in data and data["policy_version"] != HABIT_POLICY_VERSION:
            raise ValueError("Unknown habit policy")
        result.append(model.model_validate(data))
    return result


def _validate_habit_archive(
    session: Session, archive: ExportArchive, player_id: str,
) -> tuple[
    HabitPolicy, list[HabitGoal], list[HabitDay], list[HabitCredit],
    list[PatternTarget], list[PatternAward], list[PatternAwardEvidence],
]:
    """Restore immutable ledgers, never run qualification/reward mutation hooks.

    Historical trust is the stored snapshot, not today's allowlist/retirement.
    References, grading, game replay and historical recurrence remain checkable.
    """
    goals = _habit_records(HabitGoal, archive.habit_goals, player_id)
    days = _habit_records(HabitDay, archive.habit_days, player_id)
    credits = _habit_records(HabitCredit, archive.habit_credits, player_id)
    targets = _habit_records(PatternTarget, archive.pattern_targets, player_id)
    awards = _habit_records(PatternAward, archive.pattern_awards, player_id)
    award_evidence = _habit_records(
        PatternAwardEvidence, archive.pattern_award_evidence, player_id,
    )
    if archive.habit_policy is None:
        if goals or days or credits or targets or awards or award_evidence:
            raise ValueError("Habit evidence requires its archived cutover")
        # Legacy archives have no earned habit ledger. A fresh cutover prevents
        # pre-M69 attempts from becoming rewards on their next write.
        return (
            HabitPolicy(player_id=player_id), goals, days, credits, targets, awards, award_evidence,
        )
    policy = _habit_records(HabitPolicy, [archive.habit_policy], player_id)[0]
    effective = utc(policy.effective_at)
    games = {str(row["id"]): Game.model_validate(row) for row in archive.games}
    positions = {str(row["id"]): Position.model_validate(row) for row in archive.positions}
    analyses = {
        str(row["id"]): EngineAnalysis.model_validate(row) for row in archive.engine_analyses
    }
    opportunities = {
        str(row["id"]): PersistedLessonOpportunity.model_validate(row)
        for row in archive.lesson_opportunities
    }
    attempts = {
        str(row["id"]): LessonAttempt.model_validate(row) for row in archive.lesson_attempts
    }
    evidence = {str(row["evidence_id"]): Evidence.model_validate(row) for row in archive.evidence}
    schedules = {str(row["item_id"]): row for row in archive.review_schedules}
    jobs = [AnalysisJob.model_validate(row) for row in archive.analysis_jobs]
    reviews = [GameMoveReview.model_validate(row) for row in archive.review_moves]
    runs = {str(row["id"]): PuzzleRun.model_validate(row) for row in archive.puzzle_runs}
    content_attempts = {
        str(row["id"]): ContentAttempt.model_validate(row) for row in archive.content_attempts
    }
    items = {
        str(row["id"]): ContentItem.model_validate(row) for row in archive.puzzle_content_items
    }
    studies = {str(row["id"]): row for row in archive.study_sessions}
    # These inputs may otherwise be retained rather than overwritten on restore.
    # Never validate against one source and persist a conflicting shared source.
    if credits or targets or awards:
        sources: list[tuple[SQLModel, Any]] = [
            (game, game.id) for game in games.values()
        ]
        sources.extend((position, position.id) for position in positions.values())
        sources.extend((analysis, analysis.id) for analysis in analyses.values())
        sources.extend((opportunity, opportunity.id) for opportunity in opportunities.values())
        sources.extend((proof, proof.evidence_id) for proof in evidence.values())
        sources.extend((job, job.id) for job in jobs)
        sources.extend((review, review.id) for review in reviews)
        sources.extend((credit, credit.id) for credit in credits)
        sources.extend((target, target.id) for target in targets)
        sources.extend((award, award.id) for award in awards)
        sources.extend((proof, proof.id) for proof in award_evidence)
        sources.extend((attempt, attempt.id) for attempt in attempts.values())
        sources.extend(
            (play, play.id)
            for play in (PlaySession.model_validate(row) for row in archive.play_sessions)
        )
        for source_row, key in sources:
            existing = session.get(type(source_row), key)
            if existing is not None and existing.model_dump(mode="json") != source_row.model_dump(
                mode="json"
            ):
                raise ValueError("Conflicting habit source")

    def lesson_source(
        attempt_id: str, snapshot: dict[str, Any] | None = None,
    ) -> tuple[LessonAttempt, PersistedLessonOpportunity, str, str]:
        attempt = attempts.get(attempt_id)
        raw_attempt = next(
            (row for row in archive.lesson_attempts if row.get("id") == attempt_id), {},
        )
        if (
            attempt is None or attempt.player_id != player_id
            or attempt.source_kind != "persisted_opportunity"
            or attempt.success is not True or attempt.grading_status != "verified"
            or attempt.hints_used != 0 or attempt.opportunity_id is None
            or type(raw_attempt.get("hints_used")) is not int
            or type(raw_attempt.get("elapsed_ms")) is not int
            or set(raw_attempt) != set(LessonAttempt.model_fields)
            or type(raw_attempt.get("success")) is not bool
            or attempt.lesson_id != str(attempt.opportunity_id)
            or attempt.session_id not in studies
            or studies[attempt.session_id]["player_id"] != player_id
            or utc(attempt.completed_at) < effective
            or _habit_time(raw_attempt.get("completed_at")) != utc(attempt.completed_at)
            or any(
                other.opportunity_id == attempt.opportunity_id and other.id != attempt.id
                and utc(other.completed_at) <= utc(attempt.completed_at)
                for other in attempts.values()
            )
        ):
            raise ValueError("Habit lesson is not the first unhinted server-graded success")
        opportunity = opportunities.get(str(attempt.opportunity_id))
        if opportunity is None or opportunity.player_id != player_id:
            raise ValueError("Invalid habit lesson opportunity")
        position = positions.get(str(opportunity.source_position_id))
        game = games.get(str(opportunity.game_id))
        spec = LessonSpec.model_validate(opportunity.lesson_spec)
        schedule = schedules.get(str(opportunity.id))
        if (
            position is None or game is None or game.owner_player_id != player_id
            or position.game_id != game.id or spec.source.fen != position.fen
            or utc(opportunity.created_at) > utc(attempt.completed_at)
            or spec.diagnosis is None or spec.diagnosis.primary not in _HABIT_V1_CODES
            or opportunity.verification_analysis_id is None
            or schedule is None
            or schedule.get("retired_at") is not None
            and _habit_time(schedule["retired_at"]) <= utc(attempt.completed_at)
            or _habit_time(studies[attempt.session_id]["started_at"]) > utc(attempt.completed_at)
            or any(
                row.get("concept_code") == spec.diagnosis.primary
                and row.get("retired_at") is not None
                and _habit_time(row["retired_at"]) <= utc(attempt.completed_at)
                for row in archive.skill_states
            )
        ):
            raise ValueError("Invalid habit lesson source")
        analysis = analyses.get(str(opportunity.verification_analysis_id))
        board = chess.Board(position.fen)
        move = chess.Move.from_uci(attempt.submitted_move or "")
        if (
            analysis is None or analysis.position_id != position.id
            or move not in board.legal_moves
            or board.san(move) not in {entry.san for entry in spec.interaction.accepted_moves}
            or not spec.diagnosis.evidence_refs
        ):
            raise ValueError("Invalid habit server grade or source EPD")
        source_board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
        source_found = False
        for move_uci in game.moves:
            if source_board.fen() == position.fen and (
                "white" if source_board.turn else "black"
            ) == game.owner_color:
                source_found = True
            source_board.push_uci(move_uci)
        if not source_found:
            raise ValueError("Habit source is not an owned game occurrence")
        for reference in spec.diagnosis.evidence_refs:
            proof = evidence.get(reference)
            if (
                proof is None or proof.position_id != str(position.id)
                or proof.engine_analysis_id not in analyses
                or analyses[proof.engine_analysis_id].position_id != position.id
            ):
                raise ValueError("Forged habit evidence reference")
        expected = {
            "opportunity_id": str(opportunity.id), "game_id": str(game.id),
            "position_id": str(position.id), "code": spec.diagnosis.primary,
            "verification_analysis_id": str(analysis.id),
            "evidence_references": list(spec.diagnosis.evidence_refs),
            "hints_authority": "app_reported", "hints_used": 0,
        }
        if snapshot is not None and (
            snapshot != expected or type(snapshot.get("hints_used")) is not int
        ):
            raise ValueError("Forged habit trust snapshot")
        return attempt, opportunity, board.epd(), spec.diagnosis.primary

    goal_days: set[str] = set()
    for goal in goals:
        if (
            goal.effective_day in goal_days or utc(goal.created_at) < effective
            or date.fromisoformat(goal.effective_day)
            != date.fromordinal(utc(goal.created_at).date().toordinal() + 1)
        ):
            raise ValueError("Invalid habit next-day goal")
        goal_days.add(goal.effective_day)
    by_day = {day.utc_day: day for day in days}
    if len(by_day) != len(days):
        raise ValueError("Duplicate habit days")
    credit_keys: set[tuple[str, str]] = set()
    epd_keys: set[tuple[str, str]] = set()
    source_ids: set[tuple[str, str]] = set()
    credit_ids: set[str] = set()
    completed_by_day: dict[str, list[datetime]] = {}
    for credit in credits:
        completed = utc(credit.completed_at)
        if (
            credit.id in credit_ids or credit.utc_day not in by_day
            or completed < effective or credit.utc_day != completed.date().isoformat()
            or (credit.utc_day, credit.source_key) in credit_keys
            or (credit.utc_day, credit.source_epd) in epd_keys
            or (credit.source_kind, credit.source_id) in source_ids
        ):
            raise ValueError("Duplicate or pre-cutover habit credit")
        credit_ids.add(credit.id)
        credit_keys.add((credit.utc_day, credit.source_key))
        epd_keys.add((credit.utc_day, credit.source_epd))
        source_ids.add((credit.source_kind, credit.source_id))
        completed_by_day.setdefault(credit.utc_day, []).append(completed)
        if credit.source_kind == "lesson":
            attempt, opportunity, epd, _ = lesson_source(credit.source_id, credit.trust_snapshot)
            if (
                credit.source_key != f"lesson:{opportunity.id}" or credit.source_epd != epd
                or completed != utc(attempt.completed_at)
            ):
                raise ValueError("Invalid habit lesson credit provenance")
        elif credit.source_kind == "puzzle":
            run = runs.get(credit.source_id)
            attempt_row = content_attempts.get(credit.source_id)
            item = items.get(run.item_id) if run is not None else None
            raw_run = next(
                (row for row in archive.puzzle_runs if row.get("id") == credit.source_id), {},
            )
            raw_content_attempt = next(
                (row for row in archive.content_attempts if row.get("id") == credit.source_id), {},
            )
            if (
                run is None or attempt_row is None or item is None
                or run.player_id != player_id or run.state != "solved" or run.hint_steps
                or run.completed_at is None or completed != utc(run.completed_at)
                or _habit_time(raw_run.get("completed_at")) != completed
                or _habit_time(raw_content_attempt.get("completed_at")) != completed
                or type(raw_content_attempt.get("success")) is not bool
                or type(raw_content_attempt.get("hint_assisted")) is not bool
                or set(raw_run) != set(PuzzleRun.model_fields)
                or set(raw_content_attempt) != set(ContentAttempt.model_fields)
                or attempt_row.player_id != player_id or attempt_row.item_id != run.item_id
                or attempt_row.session_id != run.session_id or attempt_row.success is not True
                or attempt_row.hint_assisted or attempt_row.completed_at is None
                or completed != utc(attempt_row.completed_at)
                or attempt_row.response_payload.get("run_id") != run.id
                or credit.source_key != f"puzzle:{item.id}"
                or credit.source_epd != chess.Board(_puzzle_spec(item).source.fen).epd()
                or credit.trust_snapshot != {
                    "item_id": item.id, "run_id": run.id, "content_attempt_id": attempt_row.id,
                    "hints_authority": "server", "hint_steps": {},
                }
            ):
                raise ValueError("Invalid habit puzzle credit provenance")
            spec = _puzzle_spec(item)
            board = chess.Board(spec.source.fen)
            for move_uci in run.played_moves:
                board.push_uci(move_uci)
            if (
                run.cursor != len(run.played_moves) or run.cursor % 2 != 1
                or run.fen != board.fen()
                or not matches_completed_solution(
                    run.played_moves, spec.interaction.solution, board,
                )
            ):
                raise ValueError("Invalid habit puzzle completion")
        else:
            raise ValueError("Unknown habit credit source")
    for day in days:
        applicable = sorted(
            (goal for goal in goals if goal.effective_day <= day.utc_day),
            key=lambda goal: goal.effective_day,
        )
        times = sorted(completed_by_day.get(day.utc_day, []))
        expected_met = times[day.goal - 1] if len(times) >= day.goal else None
        if (
            day.utc_day < effective.date().isoformat()
            or day.utc_day > datetime.now(UTC).date().isoformat()
            or day.goal != (applicable[-1].goal if applicable else 3)
            or (utc(day.met_at) if day.met_at is not None else None) != expected_met
        ):
            raise ValueError("Invalid immutable habit goal/met-day snapshot")

    def game_eligible(game: Game, after: datetime, at: datetime) -> bool:
        if (
            game.owner_player_id != player_id or game.owner_color not in {"white", "black"}
            or game.result not in {"1-0", "0-1", "1/2-1/2"} or utc(game.created_at) <= after
            or not any(
                row.get("game_id") == str(game.id) and row.get("player_id") == player_id
                and row.get("status") == "completed" for row in archive.play_sessions
            )
            or any(
                row.get("game_id") == str(game.id) and row.get("player_id") == player_id
                and row.get("status") != "completed" for row in archive.play_sessions
            )
            or not any(
                job.game_id == game.id and job.status == "completed"
                and job.completed_at is not None
                and utc(game.created_at) <= utc(job.completed_at) <= at for job in jobs
            )
        ):
            return False
        board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
        if not board.is_valid():
            return False
        rows = {row.ply: row for row in reviews if row.game_id == game.id}
        owner_moves = 0
        for ply, move_uci in enumerate(game.moves, 1):
            if ("white" if board.turn else "black") != game.owner_color:
                board.push_uci(move_uci)
                continue
            owner_moves += 1
            row = rows.get(ply)
            if row is None or row.mover != game.owner_color or row.move_uci != move_uci:
                return False
            source = positions.get(str(row.source_position_id))
            reached = positions.get(str(row.reached_position_id))
            before_analysis = analyses.get(str(row.before_analysis_id))
            after_analysis = analyses.get(str(row.after_analysis_id))
            if (
                source is None or reached is None
                or before_analysis is None or after_analysis is None
                or source.game_id != game.id or reached.game_id != game.id
                or source.fen != board.fen() or before_analysis.position_id != source.id
                or after_analysis.position_id != reached.id
            ):
                return False
            before_probability, before_terminal = review_probability(
                board, before_analysis.raw_result[0] if before_analysis.raw_result else {},
            )
            board.push_uci(move_uci)
            after_probability, after_terminal = review_probability(
                board, after_analysis.raw_result[0] if after_analysis.raw_result else {},
            )
            if (
                reached.fen != board.fen()
                or before_probability is None or after_probability is None
                or not isfinite(before_probability) or not isfinite(after_probability)
                or row.before_probability != before_probability
                or row.after_probability != after_probability
                or row.before_terminal != before_terminal or row.after_terminal != after_terminal
            ):
                return False
        return owner_moves >= 20

    def recurrence(game_id: str, code: str, at: datetime) -> bool:
        for row in archive.profile_observations:
            if (
                row["player_id"] == player_id
                and row["game_id"] == game_id and row["skill_id"] == code
                and _habit_time(row["observed_at"]) <= at
                and (row.get("retired_at") is None or _habit_time(row["retired_at"]) > at)
            ):
                return True
        for opportunity in opportunities.values():
            diagnosis = opportunity.lesson_spec.get("diagnosis", {})
            schedule = schedules.get(str(opportunity.id))
            if (
                opportunity.player_id == player_id
                and str(opportunity.game_id) == game_id and diagnosis.get("primary") == code
                and utc(opportunity.created_at) <= at and schedule is not None
                and (schedule.get("retired_at") is None or _habit_time(schedule["retired_at"]) > at)
                and opportunity.verification_analysis_id is not None
                and diagnosis.get("evidence_refs")
            ):
                return True
        return False

    target_ids: set[str] = set()
    target_codes: set[str] = set()
    for target in targets:
        started = utc(target.started_at)
        if (
            target.id in target_ids or target.code in target_codes or started < effective
            or target.code not in _HABIT_V1_CODES
            or (target.invalidated_at is None) != (target.invalidation_reason is None)
            or target.invalidated_at is not None and (
                utc(target.invalidated_at) < started
                or target.invalidation_reason not in _HABIT_INVALIDATION_REASONS
            )
        ):
            raise ValueError("Invalid pattern target policy or invalidation")
        target_ids.add(target.id)
        target_codes.add(target.code)
        snapshot = target.pattern_snapshot
        fields = {
            "diagnosis", "name", "template", "occurrence_count", "game_ids",
            "evidence_references", "total_occurrences", "scored_occurrences",
            "observed_loss", "supporting_moves", "practice_themes",
        }
        if set(snapshot) != fields or snapshot["diagnosis"] != target.code:
            raise ValueError("Invalid pattern source snapshot")
        for name in ("game_ids", "evidence_references", "supporting_moves", "practice_themes"):
            if type(snapshot[name]) is not list:
                raise ValueError("Invalid pattern snapshot list")
        for name in ("name", "template"):
            if type(snapshot[name]) is not str or not snapshot[name]:
                raise ValueError("Invalid pattern snapshot description")
        for name in ("occurrence_count", "total_occurrences", "scored_occurrences"):
            if type(snapshot[name]) is not int or snapshot[name] < 0:
                raise ValueError("Invalid pattern snapshot count")
        supporting_keys: set[tuple[str, int]] = set()
        source_games: set[str] = set()
        source_refs: set[str] = set()
        losses = []
        for move in snapshot["supporting_moves"]:
            if (
                type(move) is not dict or set(move) != {"game_id", "ply", "lesson_id", "loss"}
                or type(move["ply"]) is not int or type(move["game_id"]) is not str
                or type(move["lesson_id"]) is not str
            ):
                raise ValueError("Invalid pattern occurrence")
            candidate_opportunity = opportunities.get(move["lesson_id"])
            game = games.get(move["game_id"])
            if candidate_opportunity is None or game is None:
                raise ValueError("Missing pattern occurrence source")
            opportunity = candidate_opportunity
            position = positions.get(str(opportunity.source_position_id))
            diagnosis = opportunity.lesson_spec.get("diagnosis", {})
            verification = analyses.get(str(opportunity.verification_analysis_id))
            schedule = schedules.get(str(opportunity.id))
            if (
                opportunity.player_id != player_id or opportunity.game_id != game.id
                or game.owner_player_id != player_id or position is None
                or position.game_id != game.id or diagnosis.get("primary") != target.code
                or utc(opportunity.created_at) > started or utc(game.created_at) > started
                or opportunity.lesson_spec.get("source", {}).get("fen") != position.fen
                or verification is None or verification.position_id != position.id
                or schedule is None
                or schedule.get("retired_at") is not None
                and _habit_time(schedule["retired_at"]) <= started
                or any(
                    row.get("concept_code") == target.code and row.get("retired_at") is not None
                    and _habit_time(row["retired_at"]) <= started for row in archive.skill_states
                )
                or not any(
                    job.game_id == game.id and job.status == "completed"
                    and job.completed_at is not None and utc(job.completed_at) <= started
                    for job in jobs
                )
                or not 1 <= move["ply"] <= len(game.moves)
                or (move["game_id"], move["ply"]) in supporting_keys
            ):
                raise ValueError("Forged pattern occurrence provenance")
            board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
            for move_uci in game.moves[:move["ply"] - 1]:
                board.push_uci(move_uci)
            if board.fen() != position.fen or (
                "white" if board.turn else "black"
            ) != game.owner_color:
                raise ValueError("Pattern occurrence is not an owner move")
            supporting_keys.add((move["game_id"], move["ply"]))
            source_games.add(move["game_id"])
            refs = diagnosis.get("evidence_refs")
            if type(refs) is not list or not refs:
                raise ValueError("Pattern occurrence lacks evidence")
            for reference in refs:
                proof = evidence.get(reference)
                if (
                    proof is None or proof.position_id != str(position.id)
                    or proof.engine_analysis_id not in analyses
                    or analyses[proof.engine_analysis_id].position_id != position.id
                ):
                    raise ValueError("Forged pattern evidence")
                source_refs.add(reference)
            loss = move["loss"]
            matching_review = next((
                row for row in reviews if row.game_id == game.id and row.ply == move["ply"]
                and row.source_position_id == position.id and row.mover == game.owner_color
                and row.move_uci == game.moves[move["ply"] - 1]
            ), None)
            expected_loss = (
                review_loss(matching_review.before_probability, matching_review.after_probability)
                if matching_review is not None else None
            )
            # This immutable snapshot predates any later review backfill.
            # An unscored occurrence remains honest even if it is scored now.
            if loss is not None:
                if (
                    type(loss) not in (int, float) or not isfinite(loss) or not 0 <= loss <= 1
                    or expected_loss is None or not isclose(loss, expected_loss, abs_tol=1e-9)
                ):
                    raise ValueError("Invalid pattern occurrence loss")
                losses.append(loss)
        if (
            len(source_games) < 3 or snapshot["game_ids"] != sorted(source_games)
            or snapshot["occurrence_count"] != len(source_games)
            or snapshot["total_occurrences"] != len(supporting_keys)
            or snapshot["scored_occurrences"] != len(losses)
            or snapshot["observed_loss"] is not None
            and type(snapshot["observed_loss"]) not in (int, float)
            or snapshot["evidence_references"] != sorted(source_refs)
            or snapshot["observed_loss"] != (sum(losses) if losses else None)
            or any(type(theme) is not str for theme in snapshot["practice_themes"])
        ):
            raise ValueError("Inconsistent pattern snapshot evidence")
        practice_ids: set[str] = set()
        practice_epds: set[str] = set()
        practice_games: set[str] = set()
        practice_times: list[datetime] = []
        for entry in target.practice:
            if len(practice_epds) >= 3 and len(practice_games) >= 2:
                raise ValueError("Pattern practice continued after its immutable completion")
            if type(entry) is not dict or set(entry) != {
                "attempt_id", "opportunity_id", "game_id", "source_epd", "completed_at",
            } or any(type(value) is not str for value in entry.values()):
                raise ValueError("Invalid pattern practice shape")
            attempt, opportunity, epd, code = lesson_source(entry["attempt_id"])
            completed = utc(attempt.completed_at)
            if (
                entry["attempt_id"] in practice_ids or epd in practice_epds or completed < started
                or entry["opportunity_id"] != str(opportunity.id)
                or entry["game_id"] != str(opportunity.game_id) or entry["source_epd"] != epd
                or _habit_time(entry["completed_at"]) != completed or code != target.code
                or practice_times and completed < practice_times[-1]
                or target.invalidated_at is not None and completed > utc(target.invalidated_at)
            ):
                raise ValueError("Invalid pattern practice provenance")
            practice_ids.add(entry["attempt_id"])
            practice_epds.add(epd)
            practice_games.add(entry["game_id"])
            practice_times.append(completed)
        completed_practice = (
            practice_times[-1] if len(practice_epds) >= 3 and len(practice_games) >= 2 else None
        )
        if (
            (utc(target.practice_completed_at) if target.practice_completed_at else None)
            != completed_practice
            or len(set(target.window_game_ids)) != len(target.window_game_ids)
            or len(target.window_game_ids) > 5
            or any(type(game_id) is not str for game_id in target.window_game_ids)
            or completed_practice is None and target.window_game_ids
        ):
            raise ValueError("Inconsistent pattern practice/window")
        if completed_practice is not None:
            window_games = [games.get(game_id) for game_id in target.window_game_ids]
            if any(
                game is None or not game_eligible(game, completed_practice, datetime.now(UTC))
                for game in window_games
            ):
                raise ValueError("Invalid pattern window game")
            order = [
                (utc(games[game_id].created_at), game_id) for game_id in target.window_game_ids
            ]
            if order != sorted(order):
                raise ValueError("Unordered pattern observation window")
    evidence_by_award: dict[str, list[PatternAwardEvidence]] = {}
    evidence_ids: set[str] = set()
    for award_record in award_evidence:
        if award_record.id in evidence_ids:
            raise ValueError("Duplicate pattern award evidence")
        evidence_ids.add(award_record.id)
        evidence_by_award.setdefault(award_record.award_id, []).append(award_record)
    jobs_by_id = {str(job.id): job for job in jobs}
    awards_by_key: set[tuple[str, str, str]] = set()
    award_ids: set[str] = set()
    awarded_targets: set[str] = set()
    targets_by_id = {target.id: target for target in targets}
    for award in awards:
        candidate_target = targets_by_id.get(award.target_id)
        if candidate_target is None:
            raise ValueError("Missing pattern award target")
        target = candidate_target
        at = utc(award.awarded_at)
        key = (award.code, award.policy_version, award.window_start)
        if (
            award.id in award_ids or key in awards_by_key
            or award.code != target.code or award.policy_version != target.policy_version
            or award.target_id in awarded_targets
            or target.practice_completed_at is None or at < utc(target.practice_completed_at)
            or target.invalidated_at is not None and at > utc(target.invalidated_at)
            or len(award.game_ids) != 5 or len(set(award.game_ids)) != 5
            or any(type(game_id) is not str for game_id in award.game_ids)
            or award.window_start != award.game_ids[0]
        ):
            raise ValueError("Invalid pattern award identity/window")
        award_ids.add(award.id)
        awards_by_key.add(key)
        awarded_targets.add(award.target_id)
        after = utc(target.practice_completed_at)
        proofs = sorted(evidence_by_award.get(award.id, []), key=lambda proof: proof.ordinal)
        if (
            len(proofs) != 5 or [proof.ordinal for proof in proofs] != list(range(1, 6))
            or [str(proof.game_id) for proof in proofs] != award.game_ids
            or target.window_game_ids != award.game_ids
        ):
            raise ValueError("Award disagrees with persisted five-game evidence")
        completed_times: list[datetime] = []
        for award_record in proofs:
            game = games.get(str(award_record.game_id))
            job = jobs_by_id.get(str(award_record.analysis_job_id))
            if (
                game is None or job is None or job.game_id != game.id
                or job.status != "completed" or job.completed_at is None
                or utc(award_record.game_created_at) != utc(game.created_at)
                or utc(award_record.job_completed_at) != utc(job.completed_at)
                or not game_eligible(game, after, at)
                or not utc(game.created_at) <= utc(job.completed_at) <= at
                or recurrence(str(game.id), award.code, at)
            ):
                raise ValueError("Invalid persisted pattern award evidence")
            completed_times.append(utc(job.completed_at))
        order = [(utc(proof.game_created_at), str(proof.game_id)) for proof in proofs]
        if order != sorted(order):
            raise ValueError("Unordered persisted pattern award evidence")
        # Validate only this recorded span, never rebuild a first-ever window.
        # An unresolved owned play-session game blocks even when too short to
        # become an eligible observation; skip short games only after analysis.
        for game in games.values():
            if not order[0] <= (utc(game.created_at), str(game.id)) <= order[-1]:
                continue
            plays = [
                row for row in archive.play_sessions
                if row.get("game_id") == str(game.id) and row.get("player_id") == player_id
            ]
            if game.owner_player_id == player_id and plays:
                if (
                    game.result not in {"1-0", "0-1", "1/2-1/2"}
                    or any(row.get("status") != "completed" for row in plays)
                    or not any(
                        job.game_id == game.id and job.status == "completed"
                        and job.completed_at is not None
                        and utc(game.created_at) <= utc(job.completed_at) <= at
                        for job in jobs
                    )
                ):
                    raise ValueError("Unresolved game inside persisted pattern award span")
            if game_eligible(game, after, at) and recurrence(str(game.id), award.code, at):
                raise ValueError("Recurrence inside persisted pattern award span")
        if (award.barrier_game_id is None) != (award.barrier_analysis_job_id is None):
            raise ValueError("Incomplete pattern award barrier")
        if award.barrier_game_id is not None:
            barrier = games.get(str(award.barrier_game_id))
            job = jobs_by_id.get(str(award.barrier_analysis_job_id))
            if (
                barrier is None or barrier.owner_player_id != player_id
                or barrier.owner_color not in {"white", "black"}
                or barrier.result not in {"1-0", "0-1", "1/2-1/2"}
                or not after < utc(barrier.created_at)
                or (utc(barrier.created_at), str(barrier.id)) > order[-1]
                or job is None or job.game_id != barrier.id or job.status != "completed"
                or job.completed_at is None
                or not utc(barrier.created_at) <= utc(job.completed_at) <= at
                or not any(
                    row.get("game_id") == str(barrier.id)
                    and row.get("player_id") == player_id and row.get("status") == "completed"
                    for row in archive.play_sessions
                )
                or any(
                    row.get("game_id") == str(barrier.id)
                    and row.get("player_id") == player_id and row.get("status") != "completed"
                    for row in archive.play_sessions
                )
            ):
                raise ValueError("Invalid persisted pattern award barrier")
            completed_times.append(utc(job.completed_at))
        if at < max(completed_times):
            raise ValueError("Pattern award precedes its required analyses")
    if set(evidence_by_award) != award_ids:
        raise ValueError("Orphaned or missing pattern award evidence")
    return policy, goals, days, credits, targets, awards, award_evidence


def _validate_onboarding_archive(
    data: dict[str, Any] | None,
    player_id: str,
    runs: list[PuzzleRun],
    sets: list[PuzzleDailySet],
    slots: list[PuzzleDailySlot],
) -> PlayerOnboarding:
    """Restore declarations, never client progress claims or preference provenance."""
    if data is None:
        return PlayerOnboarding(player_id=player_id, status="not_offered")
    fields = {
        "player_id", "status", "declared_level", "suggested_bot_level", "mapping_version",
        "intro_started_at", "skipped_at", "completed_at",
    }
    if set(data) != fields or type(data["player_id"]) is not str or data["player_id"] != player_id:
        raise ValueError("Invalid onboarding fields or owner")
    status = data["status"]
    if type(status) is not str or status not in {
        "pending", "not_offered", "in_progress", "skipped", "completed",
    }:
        raise ValueError("Invalid onboarding status")
    levels = LEVEL_MAPPING
    declared = data["declared_level"]
    suggestion = data["suggested_bot_level"]
    mapping = data["mapping_version"]
    if declared is not None:
        if (
            type(declared) is not str
            or declared not in levels
            or type(suggestion) is not int
            or suggestion != levels[declared]
            or type(mapping) is not str
            or mapping != MAPPING_VERSION
        ):
            raise ValueError("Invalid onboarding declaration mapping")
    elif suggestion is not None or mapping is not None:
        raise ValueError("Unplaced onboarding has a suggestion")

    timestamps: dict[str, datetime | None] = {}
    now = datetime.now(UTC)
    for key in ("intro_started_at", "skipped_at", "completed_at"):
        raw = data[key]
        if raw is None:
            timestamps[key] = None
            continue
        if type(raw) is not str or "T" not in raw:
            raise ValueError("Invalid onboarding timestamp type")
        parsed = utc(datetime.fromisoformat(raw))
        if parsed > now:
            raise ValueError("Future onboarding timestamp")
        timestamps[key] = parsed
    started = timestamps["intro_started_at"]
    skipped = timestamps["skipped_at"]
    completed = timestamps["completed_at"]
    if (declared is None) != (started is None):
        raise ValueError("Onboarding clock does not match declaration")
    if status in {"pending", "not_offered"} and (
        declared is not None or skipped is not None or completed is not None
    ):
        raise ValueError("Unplaced onboarding has placement history")
    if (status in {"in_progress", "completed"} or completed is not None) and declared is None:
        raise ValueError("Onboarding declaration is missing")
    if (status == "skipped") != (skipped is not None):
        raise ValueError("Onboarding skip timestamp does not match status")
    if (status == "completed" and completed is None) or (
        status not in {"completed", "skipped"} and completed is not None
    ):
        raise ValueError("Onboarding completion timestamp does not match status")
    if skipped is not None and (
        started is not None and skipped < started
        or completed is not None and skipped < completed
    ):
        raise ValueError("Onboarding skip precedes declaration or completion")

    if started is not None:
        own_sets = {daily.id for daily in sets if daily.player_id == player_id}
        bound = {
            slot.run_id: slot
            for slot in slots
            if slot.player_id == player_id and slot.set_id in own_sets and slot.run_id is not None
        }
        introductory = sorted(
            (
                run for run in runs
                if run.player_id == player_id and run.id in bound and utc(run.started_at) >= started
            ),
            key=lambda run: (utc(run.started_at), run.id),
        )[:INTRO_TARGET]
        closed = [
            utc(run.completed_at)
            for run in introductory
            if run.state in {"solved", "failed", "abandoned"}
            and run.completed_at is not None
            and bound[run.id].closed_at is not None
        ]
        all_closed = len(closed) == INTRO_TARGET
        if status != "skipped" and (status == "completed") != all_closed:
            raise ValueError("Onboarding completion does not match introductory runs")
        if completed is not None and (not all_closed or completed != max(closed)):
            raise ValueError("Onboarding completion does not match introductory closure")
    return PlayerOnboarding.model_validate({**data, **timestamps})


def _puzzle_spec(item: ContentItem) -> LessonSpec:
    """Validate portable puzzle dependencies without granting global write access."""
    payload = item.payload
    moves = payload.get("moves")
    puzzle_id = payload.get("puzzle_id")
    fen = payload.get("fen")
    themes = payload.get("themes", [])
    for value in (
        payload.get("rating", item.difficulty_estimate),
        item.difficulty_estimate,
    ):
        try:
            valid_number = type(value) in (int, float) and isfinite(value)
        except OverflowError:
            valid_number = False
        if not valid_number:
            raise ValueError("Invalid puzzle difficulty metadata")
    if not isinstance(themes, list) or any(not isinstance(theme, str) for theme in themes):
        raise ValueError("Invalid puzzle theme metadata")
    if (
        item.domain != "puzzles"
        or item.version != "0.2.0"
        or item.skill_mapping
        or not isinstance(puzzle_id, str)
        or not puzzle_id
        or item.id != f"lichess:{puzzle_id}"
        or not isinstance(fen, str)
        or not isinstance(moves, list)
        or any(not isinstance(move, str) for move in moves)
        or len(moves) < 2
        or len(moves) % 2
        or payload.get("setup") != moves[0]
        or type(payload.get("import_sequence")) is not int
        or payload["import_sequence"] < 0
    ):
        raise ValueError("Invalid puzzle identity")
    if validate_solution_line(fen, moves) is not None:
        raise ValueError("Invalid puzzle source line")
    board = chess.Board(fen)
    board.push_uci(moves[0])
    spec = LessonSpec.model_validate(payload.get("lesson"))
    if (
        spec.schema_version != "0.2.0"
        or spec.source.kind != "external"
        or spec.verification.status != "source_verified"
        or spec.diagnosis is not None
        or spec.source.fen != board.fen()
        or spec.interaction.solution != moves[1:]
    ):
        raise ValueError("Puzzle lesson does not match its source")
    verify_lesson(spec)
    return spec


def _validate_puzzle_archive(
    session: Session,
    player_id: str,
    items: list[ContentItem],
    runs: list[PuzzleRun],
    study_sessions: list[StudySession],
    attempts: list[ContentAttempt],
    slots: list[PuzzleDailySlot],
) -> list[ContentItem]:
    """Preflight the entire puzzle graph before any archive records are written."""
    catalog = {item.id: item for item in items}
    if len(catalog) != len(items) or len({run.id for run in runs}) != len(runs):
        raise ValueError("Duplicate puzzle records")
    studies = {study.id: study for study in study_sessions}
    if len(studies) != len(study_sessions) or len({run.session_id for run in runs}) != len(runs):
        raise ValueError("Duplicate puzzle study session")
    attempts_by_id = {attempt.id: attempt for attempt in attempts}
    if len(attempts_by_id) != len(attempts):
        raise ValueError("Duplicate content attempts")
    for archived_study in study_sessions:
        if (
            archived_study.domain == "puzzles"
            and session.get(StudySession, archived_study.id) is not None
        ):
            raise ValueError("Conflicting puzzle study session")
    for archived_attempt in attempts:
        if session.get(ContentAttempt, archived_attempt.id) is not None:
            raise ValueError("Conflicting content attempt")
    referenced = (
        {run.item_id for run in runs}
        | {slot.item_id for slot in slots}
        | {attempt.item_id for attempt in attempts if attempt.item_id in catalog}
    )
    if set(catalog) - referenced:
        raise ValueError("Unreferenced puzzle catalog records")
    missing = []
    specs = {}
    for item in items:
        specs[item.id] = _puzzle_spec(item)
        existing = session.get(ContentItem, item.id)
        if existing is None:
            missing.append(item)
        elif (
            existing.domain != "puzzles"
            or existing.payload.get("fen") != item.payload["fen"]
            or existing.payload.get("moves") != item.payload["moves"]
        ):
            raise ValueError("Conflicting puzzle identity")
    if sum(run.state == "active" for run in runs) > 1:
        raise ValueError("Multiple active puzzle runs")
    for run in runs:
        study = studies.get(run.session_id)
        if (
            run.player_id != player_id
            or study is None
            or study.player_id != player_id
            or study.domain != "puzzles"
            or run.item_id not in specs
            or session.get(PuzzleRun, run.id) is not None
            or run.state not in {"active", "solved", "failed", "abandoned"}
            or run.cursor < 0
            or run.revision < 0
            or not 0 <= run.wrong_attempts <= 3
        ):
            raise ValueError("Invalid puzzle run references or counters")
        solution = specs[run.item_id].interaction.solution
        board = chess.Board(specs[run.item_id].source.fen)
        for index, move in enumerate(run.played_moves):
            if index >= len(solution):
                raise ValueError("Puzzle run exceeds solution")
            board.push_uci(move)
            if move != solution[index] and not (
                run.state == "solved"
                and index == len(run.played_moves) - 1
                and index % 2 == 0
                and board.is_checkmate()
            ):
                raise ValueError("Puzzle run diverges from solution")
        solved = run.state == "solved"
        if (
            run.fen != board.fen()
            or run.cursor != len(run.played_moves)
            or run.cursor % 2 != (1 if solved else 0)
            or (solved and not (run.cursor == len(solution) or board.is_checkmate()))
            or (not solved and run.cursor >= len(solution))
            or (run.state == "failed") != (run.wrong_attempts == 3)
            or (run.state == "active") != (run.completed_at is None)
            or (run.completed_at is not None and run.completed_at < run.started_at)
            or any(
                not key.isdecimal()
                or str(int(key)) != key
                or int(key) % 2
                or int(key) > run.cursor
                or int(key) >= len(solution)
                or type(step) is not int
                or not 1 <= step <= 3
                for key, step in run.hint_steps.items()
            )
            or run.revision
            < (len(run.played_moves) + 1) // 2 + run.wrong_attempts + len(run.hint_steps)
        ):
            raise ValueError("Invalid puzzle run state")
        attempt = attempts_by_id.get(run.id)
        if run.state in {"solved", "failed"}:
            if (
                attempt is None
                or attempt.player_id != player_id
                or attempt.item_id != run.item_id
                or attempt.session_id != run.session_id
                or attempt.success != solved
                or attempt.hint_assisted != bool(run.hint_steps)
                or attempt.response_payload.get("run_id") != run.id
                or attempt.completed_at != run.completed_at
            ):
                raise ValueError("Invalid terminal puzzle attempt")
        elif attempt is not None:
            raise ValueError("Unfinished puzzle run has an attempt")
    run_ids = {run.id for run in runs}
    for attempt in attempts:
        attempt_item = catalog.get(attempt.item_id) or session.get(ContentItem, attempt.item_id)
        if attempt_item is None:
            raise ValueError("Missing content reference")
        if attempt_item.domain == "puzzles" and (
            attempt.item_id not in catalog or attempt.id not in run_ids
        ):
            raise ValueError("Puzzle attempt has no archived run")
    return missing


def _validate_puzzle_practice_archive(
    session: Session,
    player_id: str,
    rating: PuzzleRating | None,
    sets: list[PuzzleDailySet],
    slots: list[PuzzleDailySlot],
    events: list[PuzzleRatingEvent],
    runs: list[PuzzleRun],
    items: list[ContentItem],
    attempts: list[ContentAttempt],
) -> None:
    """Validate portable M64 history without replaying awards or trusting live metadata."""
    sets_by_id = {daily.id: daily for daily in sets}
    slots_by_id = {slot.id: slot for slot in slots}
    runs_by_id = {run.id: run for run in runs}
    item_ids = {item.id for item in items}
    if (
        len(sets_by_id) != len(sets)
        or len(slots_by_id) != len(slots)
        or len({daily.utc_day for daily in sets}) != len(sets)
        or len({event.run_id for event in events}) != len(events)
        or len({event.slot_id for event in events}) != len(events)
    ):
        raise ValueError("Duplicate puzzle practice records")
    if rating is None:
        if sets or slots or events:
            raise ValueError("Puzzle practice history requires a rating")
    elif (
        rating.player_id != player_id
        or not isfinite(rating.rating)
        or rating.rated_count < 0
        or session.get(PuzzleRating, player_id) is not None
    ):
        raise ValueError("Invalid puzzle rating")
    for daily in sets:
        created_utc = (
            daily.created_at.replace(tzinfo=UTC)
            if daily.created_at.tzinfo is None
            else daily.created_at.astimezone(UTC)
        )
        if (
            daily.player_id != player_id
            or daily.policy_version != POLICY_VERSION
            or daily.theme_map_version != THEME_MAP_VERSION
            or not isfinite(daily.player_rating)
            or date.fromisoformat(daily.utc_day).isoformat() != daily.utc_day
            or created_utc.date().isoformat() != daily.utc_day
            or session.get(PuzzleDailySet, daily.id) is not None
        ):
            raise ValueError("Invalid puzzle daily policy snapshot")
    bound_runs: set[str] = set()
    first_items: set[str] = set()
    for slot in slots:
        daily_set = sets_by_id.get(slot.set_id)
        if (
            slot.player_id != player_id
            or daily_set is None
            or slot.item_id not in item_ids
            or not isfinite(slot.item_rating)
            or slot.reason not in {"due", "weak", "exploration"}
            or slot.widening not in {"200", "400", "catalog", "due"}
            or (slot.reason == "due") != (slot.widening == "due")
            or (slot.reason == "due" and slot.first_exposure)
            or session.get(PuzzleDailySlot, slot.id) is not None
        ):
            raise ValueError("Invalid puzzle daily slot")
        if slot.first_exposure and slot.run_id is not None:
            if slot.item_id in first_items:
                raise ValueError("Duplicate first puzzle exposure")
            first_items.add(slot.item_id)
        if slot.run_id is None:
            if slot.closed_at is not None:
                raise ValueError("Unplayed puzzle slot is closed")
            continue
        run = runs_by_id.get(slot.run_id)
        if (
            slot.run_id in bound_runs
            or run is None
            or run.player_id != player_id
            or run.item_id != slot.item_id
            or (run.state == "active") != (slot.closed_at is None)
            or slot.closed_at != run.completed_at
            or run.started_at < daily_set.created_at
        ):
            raise ValueError("Invalid puzzle slot run binding")
        bound_runs.add(slot.run_id)
        other_runs = [
            other for other in runs if other.item_id == slot.item_id and other.id != run.id
        ]
        seen_before = any(other.started_at <= run.started_at for other in other_runs)
        seen_before = seen_before or any(
            attempt.player_id == player_id
            and attempt.item_id == slot.item_id
            and attempt.id != run.id
            and (
                attempt.started_at <= run.started_at
                or (attempt.completed_at is not None and attempt.completed_at <= run.started_at)
            )
            for attempt in attempts
        )
        if slot.first_exposure == seen_before:
            raise ValueError("Puzzle first exposure does not match archived history")
    for daily in sets:
        members = [slot for slot in slots if slot.set_id == daily.id]
        if (
            len(members) > 10
            or len({slot.item_id for slot in members}) != len(members)
            or sorted(slot.position for slot in members) != list(range(len(members)))
        ):
            raise ValueError("Invalid puzzle daily membership")
    expected_events = {
        slot.run_id
        for slot in slots
        if slot.first_exposure and slot.run_id is not None and slot.closed_at is not None
    }
    if {event.run_id for event in events} != expected_events:
        raise ValueError("Missing or ineligible puzzle rating event")
    before = INITIAL_RATING
    previous_completed: datetime | None = None
    for sequence, event in enumerate(sorted(events, key=lambda item: item.sequence), start=1):
        bound_slot = slots_by_id.get(event.slot_id)
        run = runs_by_id.get(event.run_id)
        if (
            event.player_id != player_id
            or event.sequence != sequence
            or event.policy_version != POLICY_VERSION
            or bound_slot is None
            or run is None
            or bound_slot.run_id != run.id
            or not bound_slot.first_exposure
            or bound_slot.reason == "due"
            or run.state not in {"solved", "failed", "abandoned"}
            or event.item_id != bound_slot.item_id
            or event.item_id != run.item_id
            or run.completed_at is None
            or event.item_rating != bound_slot.item_rating
            or event.policy_version != sets_by_id[bound_slot.set_id].policy_version
            or event.score != int(run.state == "solved" and not run.hint_steps)
            or not all(
                isfinite(value)
                for value in (
                    event.before,
                    event.after,
                    event.item_rating,
                )
            )
            or not isclose(event.before, before, rel_tol=0, abs_tol=1e-9)
            or not isclose(
                event.after,
                elo_after(event.before, event.item_rating, event.score),
                rel_tol=0,
                abs_tol=1e-9,
            )
            or event.created_at < run.completed_at
            or previous_completed is not None
            and run.completed_at < previous_completed
            or session.get(PuzzleRatingEvent, event.run_id) is not None
        ):
            raise ValueError("Invalid puzzle rating event chain")
        before = event.after
        previous_completed = run.completed_at
    if rating is not None and (
        rating.rated_count != len(events)
        or not isclose(rating.rating, before, rel_tol=0, abs_tol=1e-9)
    ):
        raise ValueError("Puzzle rating does not match its ledger")


def _validate_puzzle_modes_archive(
    session: Session,
    player_id: str,
    bindings: list[PuzzleModeBinding],
    runs: list[PuzzleRun],
    slots: list[PuzzleDailySlot],
    events: list[PuzzleRatingEvent],
) -> None:
    """Restore mode snapshots without consulting mutable catalog theme metadata."""
    runs_by_id = {run.id: run for run in runs}
    slotted_runs = {slot.run_id for slot in slots if slot.run_id is not None}
    rated_runs = {event.run_id for event in events}
    if len({binding.run_id for binding in bindings}) != len(bindings):
        raise ValueError("Duplicate puzzle mode bindings")
    for binding in bindings:
        run = runs_by_id.get(binding.run_id)
        if (
            binding.player_id != player_id
            or run is None
            or run.player_id != player_id
            or binding.run_id in slotted_runs
            or binding.run_id in rated_runs
            or binding.mode not in {"theme", "streak"}
            or binding.widening not in {"200", "400", "catalog"}
            or (binding.mode == "theme" and (binding.theme is None or not binding.theme.strip()))
            or (binding.mode == "streak" and binding.theme is not None)
            or session.get(PuzzleModeBinding, binding.run_id) is not None
        ):
            raise ValueError("Invalid puzzle mode binding")


def _validate_reveal_snapshot(reveal: SessionHintReveal, game: Game | None) -> None:
    if game is not None and reveal.initial_fen != game.headers.get("FEN", chess.STARTING_FEN):
        raise ValueError("Hint snapshot initial position does not match its session")
    board = chess.Board(reveal.initial_fen)
    if not board.is_valid():
        raise ValueError("Invalid hint snapshot position")
    for move_uci in reveal.moves:
        board.push_uci(move_uci)
    if reveal.canonical_id != board.epd() or reveal.prefix_hash != prefix_hash(
        reveal.initial_fen, reveal.moves
    ):
        raise ValueError("Invalid hint snapshot identity")


def _validate_credit_archive(
    session: Session,
    player_id: str,
    play_sessions: list[PlaySession],
    games: list[Game],
    positions: list[Position],
    analyses: list[EngineAnalysis],
    reviews: list[GameMoveReview],
    reveals: list[SessionHintReveal],
    credits: list[PositiveMoveCredit],
) -> None:
    """Restore evidence, never apply it to mastery or trust external references."""
    sessions_by_id = {item.id: item for item in play_sessions}
    games_by_id = {item.id: item for item in games}
    positions_by_id = {item.id: item for item in positions}
    analyses_by_id = {item.id: item for item in analyses}
    reviews_by_key = {(item.game_id, item.ply): item for item in reviews}
    if len(sessions_by_id) != len(play_sessions):
        raise ValueError("Duplicate play sessions")
    for item in play_sessions:
        if item.player_id != player_id or session.get(PlaySession, item.id) is not None:
            raise ValueError("Conflicting play session owner")
        if item.assistance_tracking_version not in (None, 1):
            raise ValueError("Unsupported assistance tracking version")

    reveal_keys = {(item.session_id, item.prefix_hash) for item in reveals}
    if len(reveal_keys) != len(reveals):
        raise ValueError("Duplicate hint reveals")
    for reveal in reveals:
        play_session = sessions_by_id.get(reveal.session_id)
        if (
            reveal.player_id != player_id
            or play_session is None
            or not 1 <= reveal.highest_step <= 3
            or reveal.ply != len(reveal.moves) + 1
        ):
            raise ValueError("Invalid hint reveal owner or snapshot")
        _validate_reveal_snapshot(
            reveal,
            games_by_id.get(play_session.game_id) if play_session.game_id else None,
        )
        if session.get(SessionHintReveal, (reveal.session_id, reveal.prefix_hash)) is not None:
            raise ValueError("Conflicting hint reveal")

    credit_keys = {
        (item.player_id, item.game_id, item.canonical_id, item.skill_id) for item in credits
    }
    if len(credit_keys) != len(credits):
        raise ValueError("Duplicate positive credits")
    credit_counts = Counter((item.player_id, item.game_id, item.skill_id) for item in credits)
    if any(count > MAX_POSITIVE_CREDITS_PER_GAME_SKILL for count in credit_counts.values()):
        raise ValueError("Positive credits exceed the per-game credit cap")
    for credit in credits:
        game = games_by_id.get(credit.game_id)
        row = reviews_by_key.get((credit.game_id, credit.ply))
        linked_sessions = [item for item in play_sessions if item.game_id == credit.game_id]
        if (
            credit.player_id != player_id
            or game is None
            or game.owner_player_id != player_id
            or game.result == "*"
            or row is None
            or credit.proof_version != 1
            or len(linked_sessions) != 1
        ):
            raise ValueError("Invalid positive credit provenance")
        play_session = linked_sessions[0]
        if (
            play_session.assistance_tracking_version != 1
            or play_session.status != "completed"
            or play_session.credit_analysis_status == "analysed before the game ended"
            or play_session.coach_mode
            or play_session.opponent_config.get("initial_fen")
            or play_session.opponent_config.get("custom_start") == "true"
            or game.headers.get("FEN")
            or credit.ply <= int(play_session.opponent_config.get("initial_ply", "0"))
            or any(
                reveal.session_id == play_session.id and reveal.canonical_id == credit.canonical_id
                for reveal in reveals
            )
        ):
            raise ValueError("Ineligible positive credit")
        source = positions_by_id.get(row.source_position_id)
        reached = positions_by_id.get(row.reached_position_id)
        analysis = analyses_by_id.get(credit.source_analysis_id)
        reached_analysis = analyses_by_id.get(credit.reached_analysis_id)
        if (
            source is None
            or reached is None
            or analysis is None
            or reached_analysis is None
            or source.game_id != game.id
            or reached.game_id != game.id
            or source.canonical_id != credit.canonical_id
            or source.canonical_id != chess.Board(source.fen).epd()
            or analysis.position_id != source.id
            or reached_analysis.position_id != reached.id
            or row.before_analysis_id != analysis.id
            or row.after_analysis_id != reached_analysis.id
            or row.mover
            != (
                "white"
                if player_color(
                    game.headers.get("FEN"),
                    game.moves,
                    play_session.opponent_config.get("player_color") or game.owner_color,
                )
                else "black"
            )
        ):
            raise ValueError("Invalid positive credit source")
        if not 1 <= credit.ply <= len(game.moves):
            raise ValueError("Positive credit occurrence is outside the game")
        board = chess.Board(game.headers.get("FEN", chess.STARTING_FEN))
        for move_uci in game.moves[: credit.ply - 1]:
            board.push_uci(move_uci)
        if source.fen != board.fen() or row.move_uci != game.moves[credit.ply - 1]:
            raise ValueError("Positive credit source does not match game history")
        board.push_uci(row.move_uci)
        if reached.fen != board.fen():
            raise ValueError("Positive credit reached position does not match game history")
        previous = reviews_by_key.get((game.id, credit.ply - 1))
        predecessor = positions_by_id.get(previous.source_position_id) if previous else None
        if previous is not None and previous.reached_position_id != source.id:
            raise ValueError("Disconnected positive credit predecessor")
        proof, _ = positive_move_proof(
            row,
            source,
            analysis,
            predecessor=predecessor,
            preceding_move_uci=previous.move_uci if previous else None,
        )
        if proof is None or proof != credit.proof or proof.get("skill_id") != credit.skill_id:
            raise ValueError("Invalid positive credit proof")
        # Never validate against one archive row, then persist a different shared row.
        existing_game = session.get(Game, game.id)
        if existing_game is not None and existing_game.model_dump(mode="json") != game.model_dump(
            mode="json"
        ):
            raise ValueError("Conflicting positive credit game")
        for position in [source, reached] + ([predecessor] if predecessor is not None else []):
            existing_position = session.get(Position, position.id)
            if existing_position is not None and existing_position.model_dump(
                mode="json"
            ) != position.model_dump(mode="json"):
                raise ValueError("Conflicting positive credit position")
        for engine_analysis in (analysis, reached_analysis):
            existing_analysis = session.get(EngineAnalysis, engine_analysis.id)
            if existing_analysis is not None and existing_analysis.model_dump(
                mode="json"
            ) != engine_analysis.model_dump(mode="json"):
                raise ValueError("Conflicting positive credit analysis")
        for review in [row] + ([previous] if previous is not None else []):
            existing_review = session.get(GameMoveReview, review.id)
            if existing_review is not None and existing_review.model_dump(
                mode="json"
            ) != review.model_dump(mode="json"):
                raise ValueError("Conflicting positive credit review")
        if (
            session.get(
                PositiveMoveCredit,
                (credit.player_id, credit.game_id, credit.canonical_id, credit.skill_id),
            )
            is not None
        ):
            raise ValueError("Conflicting positive credit")


@router.post("/v1/exports", response_model=ExportArchive)
def export_player_data(
    request: Request, req: ExportRequest, session: Session = Depends(get_session)
) -> ExportArchive:
    player_id = req.player_id
    credential_hash = require_player_token(request, player_id, session)
    player = session.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    profile = session.get(PlayerProfile, player_id)
    play_sessions = session.exec(
        select(PlaySession).where(col(PlaySession.player_id) == player_id)
    ).all()
    read_onboarding(player, session)
    onboarding = session.get(PlayerOnboarding, player_id)
    session_hint_reveals = session.exec(
        select(SessionHintReveal).where(col(SessionHintReveal.player_id) == player_id)
    ).all()
    positive_move_credits = session.exec(
        select(PositiveMoveCredit).where(col(PositiveMoveCredit.player_id) == player_id)
    ).all()
    games = session.exec(select(Game).where(col(Game.owner_player_id) == player_id)).all()
    game_ids = [game.id for game in games]
    archived_game_ids = set(game_ids)
    positions = (
        session.exec(select(Position).where(col(Position.game_id).in_(game_ids))).all()
        if game_ids
        else []
    )
    position_ids = [position.id for position in positions]
    engine_analyses = (
        session.exec(
            select(EngineAnalysis).where(col(EngineAnalysis.position_id).in_(position_ids))
        ).all()
        if position_ids
        else []
    )
    review_moves = (
        session.exec(select(GameMoveReview).where(col(GameMoveReview.game_id).in_(game_ids))).all()
        if game_ids
        else []
    )
    evidence = (
        session.exec(
            select(Evidence).where(
                col(Evidence.position_id).in_([str(position_id) for position_id in position_ids])
            )
        ).all()
        if position_ids
        else []
    )
    analysis_jobs = (
        session.exec(select(AnalysisJob).where(col(AnalysisJob.game_id).in_(game_ids))).all()
        if game_ids
        else []
    )
    lesson_opportunities = (
        session.exec(
            select(PersistedLessonOpportunity).where(
                col(PersistedLessonOpportunity.game_id).in_(game_ids)
            )
        ).all()
        if game_ids
        else []
    )
    profile_observations = (
        session.exec(
            select(ProfileObservation).where(
                col(ProfileObservation.player_id) == player_id,
                col(ProfileObservation.game_id).in_([str(game_id) for game_id in game_ids]),
                col(ProfileObservation.position_id).in_(
                    [str(position_id) for position_id in position_ids]
                ),
            )
        ).all()
        if game_ids and position_ids
        else []
    )
    skill_states = session.exec(
        select(SkillState).where(col(SkillState.player_id) == player_id)
    ).all()
    review_schedules = session.exec(
        select(ReviewSchedule).where(col(ReviewSchedule.player_id) == player_id)
    ).all()
    study_sessions = session.exec(
        select(StudySession).where(col(StudySession.player_id) == player_id)
    ).all()
    study_session_ids = [study_session.id for study_session in study_sessions]
    content_attempts = session.exec(
        select(ContentAttempt).where(col(ContentAttempt.player_id) == player_id)
    ).all()
    puzzle_runs = session.exec(select(PuzzleRun).where(col(PuzzleRun.player_id) == player_id)).all()
    puzzle_mode_bindings = session.exec(
        select(PuzzleModeBinding).where(col(PuzzleModeBinding.player_id) == player_id)
    ).all()
    puzzle_rating = session.get(PuzzleRating, player_id)
    puzzle_daily_sets = session.exec(
        select(PuzzleDailySet).where(col(PuzzleDailySet.player_id) == player_id)
    ).all()
    puzzle_daily_slots = session.exec(
        select(PuzzleDailySlot).where(col(PuzzleDailySlot.player_id) == player_id)
    ).all()
    puzzle_rating_events = session.exec(
        select(PuzzleRatingEvent)
        .where(col(PuzzleRatingEvent.player_id) == player_id)
        .order_by(col(PuzzleRatingEvent.sequence))
    ).all()
    puzzle_item_ids = (
        {run.item_id for run in puzzle_runs}
        | {slot.item_id for slot in puzzle_daily_slots}
        | {attempt.item_id for attempt in content_attempts}
    )
    puzzle_content_items = (
        session.exec(
            select(ContentItem).where(
                col(ContentItem.id).in_(puzzle_item_ids), ContentItem.domain == "puzzles"
            )
        ).all()
        if puzzle_item_ids
        else []
    )
    lesson_attempts = (
        session.exec(
            select(LessonAttempt).where(
                col(LessonAttempt.player_id) == player_id,
                col(LessonAttempt.session_id).in_(study_session_ids),
            )
        ).all()
        if study_session_ids
        else []
    )
    transfer_measurements = session.exec(
        select(TransferMeasurement).where(col(TransferMeasurement.player_id) == player_id)
    ).all()
    transfer_position_ids = {
        position_id
        for measurement in transfer_measurements
        for position_id in (measurement.source_position_id, measurement.target_position_id)
        if position_id is not None
    }
    transfer_positions = (
        session.exec(
            select(TransferPosition).where(col(TransferPosition.id).in_(transfer_position_ids))
        ).all()
        if transfer_position_ids
        else []
    )
    coach_student_links = session.exec(
        select(CoachStudentLink).where(
            (col(CoachStudentLink.coach_id) == player_id)
            | (col(CoachStudentLink.student_id) == player_id)
        )
    ).all()

    return ExportArchive(
        player=player.model_dump(mode="json"),
        profile=profile.model_dump(mode="json") if profile else None,
        onboarding=onboarding.model_dump(mode="json") if onboarding else None,
        play_sessions=[
            play_session.model_copy(update={"game_id": None}).model_dump(mode="json")
            if play_session.game_id not in archived_game_ids
            else play_session.model_dump(mode="json")
            for play_session in play_sessions
        ],
        session_hint_reveals=[reveal.model_dump(mode="json") for reveal in session_hint_reveals],
        positive_move_credits=[credit.model_dump(mode="json") for credit in positive_move_credits],
        games=[game.model_dump(mode="json") for game in games],
        positions=[position.model_dump(mode="json") for position in positions],
        engine_analyses=[analysis.model_dump(mode="json") for analysis in engine_analyses],
        analysis_jobs=[analysis_job.model_dump(mode="json") for analysis_job in analysis_jobs],
        review_moves=[move.model_dump(mode="json") for move in review_moves],
        lesson_opportunities=[
            opportunity.model_dump(mode="json") for opportunity in lesson_opportunities
        ],
        evidence=[item.model_dump(mode="json") for item in evidence],
        profile_observations=[
            observation.model_dump(mode="json") for observation in profile_observations
        ],
        lesson_attempts=[attempt.model_dump(mode="json") for attempt in lesson_attempts],
        transfer_positions=[position.model_dump(mode="json") for position in transfer_positions],
        transfer_measurements=[
            measurement.model_dump(mode="json") for measurement in transfer_measurements
        ],
        coach_student_links=[link.model_dump(mode="json") for link in coach_student_links],
        skill_states=[skill_state.model_dump(mode="json") for skill_state in skill_states],
        review_schedules=[
            review_schedule.model_dump(mode="json") for review_schedule in review_schedules
        ],
        study_sessions=[study_session.model_dump(mode="json") for study_session in study_sessions],
        content_attempts=[
            content_attempt.model_dump(mode="json") for content_attempt in content_attempts
        ],
        puzzle_runs=[run.model_dump(mode="json") for run in puzzle_runs],
        puzzle_mode_bindings=[binding.model_dump(mode="json") for binding in puzzle_mode_bindings],
        puzzle_content_items=[item.model_dump(mode="json") for item in puzzle_content_items],
        puzzle_rating=puzzle_rating.model_dump(mode="json") if puzzle_rating else None,
        puzzle_daily_sets=[daily.model_dump(mode="json") for daily in puzzle_daily_sets],
        puzzle_daily_slots=[slot.model_dump(mode="json") for slot in puzzle_daily_slots],
        habit_policy=(
            policy.model_dump(mode="json")
            if (policy := session.get(HabitPolicy, player_id)) is not None else None
        ),
        habit_goals=[
            row.model_dump(mode="json") for row in
            session.exec(select(HabitGoal).where(HabitGoal.player_id == player_id)).all()
        ],
        habit_days=[
            row.model_dump(mode="json") for row in
            session.exec(select(HabitDay).where(HabitDay.player_id == player_id)).all()
        ],
        habit_credits=[
            row.model_dump(mode="json") for row in
            session.exec(select(HabitCredit).where(HabitCredit.player_id == player_id)).all()
        ],
        pattern_targets=[
            row.model_dump(mode="json") for row in
            session.exec(select(PatternTarget).where(PatternTarget.player_id == player_id)).all()
        ],
        pattern_awards=[
            row.model_dump(mode="json") for row in
            session.exec(select(PatternAward).where(PatternAward.player_id == player_id)).all()
        ],
        pattern_award_evidence=[
            row.model_dump(mode="json") for row in session.exec(
                select(PatternAwardEvidence).where(PatternAwardEvidence.player_id == player_id)
                .order_by(col(PatternAwardEvidence.award_id), col(PatternAwardEvidence.ordinal))
            ).all()
        ],
        puzzle_rating_events=[event.model_dump(mode="json") for event in puzzle_rating_events],
        credential_hash=credential_hash,
    )


@router.post("/v1/imports")
def import_player_data(
    request: Request, archive: ExportArchive, session: Session = Depends(get_session)
) -> dict[str, str]:
    if not archive.player:
        raise HTTPException(status_code=400, detail="Invalid archive: missing player data")

    player_id = archive.player.get("id")
    if not isinstance(player_id, str) or not player_id:
        raise HTTPException(status_code=400, detail="Invalid archive: missing player id")
    if archive.credential_hash is None:
        raise HTTPException(status_code=400, detail="Invalid archive: missing credential hash")

    token_hash = require_player_token(
        request,
        player_id,
        session,
        expected_token_hash=archive.credential_hash,
    )
    existing = session.get(Player, player_id)
    if existing:
        raise HTTPException(status_code=409, detail="Player already exists")

    try:
        player = Player.model_validate(archive.player)
        profile = PlayerProfile.model_validate(archive.profile) if archive.profile else None
        play_sessions = [PlaySession.model_validate(data) for data in archive.play_sessions]
        games = [Game.model_validate(data) for data in archive.games]
        positions = [Position.model_validate(data) for data in archive.positions]
        engine_analyses = [EngineAnalysis.model_validate(data) for data in archive.engine_analyses]
        review_moves = [GameMoveReview.model_validate(data) for data in archive.review_moves]
        analysis_jobs = [AnalysisJob.model_validate(data) for data in archive.analysis_jobs]
        lesson_opportunities = [
            PersistedLessonOpportunity.model_validate(data) for data in archive.lesson_opportunities
        ]
        evidence = [Evidence.model_validate(data) for data in archive.evidence]
        profile_observations = [
            ProfileObservation.model_validate(data) for data in archive.profile_observations
        ]
        lesson_attempts = [LessonAttempt.model_validate(data) for data in archive.lesson_attempts]
        transfer_positions = [
            TransferPosition.model_validate(data) for data in archive.transfer_positions
        ]
        transfer_measurements = [
            TransferMeasurement.model_validate(data) for data in archive.transfer_measurements
        ]
        coach_student_links = [
            CoachStudentLink.model_validate(data) for data in archive.coach_student_links
        ]
        skill_states = [SkillState.model_validate(data) for data in archive.skill_states]
        review_schedules = [
            ReviewSchedule.model_validate(data) for data in archive.review_schedules
        ]
        study_sessions = [StudySession.model_validate(data) for data in archive.study_sessions]
        content_attempts = [
            ContentAttempt.model_validate(data) for data in archive.content_attempts
        ]
        session_hint_reveals = [
            SessionHintReveal.model_validate(data) for data in archive.session_hint_reveals
        ]
        positive_move_credits = [
            PositiveMoveCredit.model_validate(data) for data in archive.positive_move_credits
        ]
        for opportunity in lesson_opportunities:
            PersistedDiagnosis.model_validate(opportunity.lesson_spec.get("diagnosis"))
    except ValidationError as error:
        raise HTTPException(status_code=400, detail="Invalid archive: malformed records") from error

    # Validation and first-write-wins persistence must resolve the same source.
    # Use parsed database identities so alternate UUID spellings cannot bypass this.
    for identity_records, identity_field in (
        (games, "id"),
        (positions, "id"),
        (engine_analyses, "id"),
        (analysis_jobs, "id"),
        (lesson_opportunities, "id"),
        (lesson_attempts, "id"),
        (evidence, "evidence_id"),
    ):
        if len({getattr(record, identity_field) for record in identity_records}) != len(
            identity_records
        ):
            raise HTTPException(status_code=400, detail="Invalid archive: duplicate source IDs")

    try:
        for data in archive.puzzle_content_items:
            difficulty = data.get("difficulty_estimate")
            try:
                valid_difficulty = (
                    isinstance(difficulty, (int, float))
                    and not isinstance(difficulty, bool)
                    and isfinite(difficulty)
                )
            except OverflowError:
                valid_difficulty = False
            if not valid_difficulty:
                raise ValueError("Invalid puzzle difficulty estimate")
        puzzle_items = [ContentItem.model_validate(data) for data in archive.puzzle_content_items]
        for data in archive.puzzle_runs:
            for counter in ("cursor", "revision", "wrong_attempts"):
                if counter in data and type(data[counter]) is not int:
                    raise ValueError("Invalid puzzle counter")
            if any(type(step) is not int for step in data.get("hint_steps", {}).values()):
                raise ValueError("Invalid hint steps")
        puzzle_runs = [PuzzleRun.model_validate(data) for data in archive.puzzle_runs]
        puzzle_mode_bindings = [
            PuzzleModeBinding.model_validate(data) for data in archive.puzzle_mode_bindings
        ]
        puzzle_rating = (
            PuzzleRating.model_validate(archive.puzzle_rating)
            if archive.puzzle_rating is not None
            else None
        )
        puzzle_daily_sets = [
            PuzzleDailySet.model_validate(data) for data in archive.puzzle_daily_sets
        ]
        puzzle_daily_slots = [
            PuzzleDailySlot.model_validate(data) for data in archive.puzzle_daily_slots
        ]
        puzzle_rating_events = [
            PuzzleRatingEvent.model_validate(data) for data in archive.puzzle_rating_events
        ]
        integer_fields = (
            (
                [archive.puzzle_rating] if archive.puzzle_rating is not None else [],
                ("rated_count",),
            ),
            (archive.puzzle_daily_slots, ("position",)),
            (archive.puzzle_rating_events, ("sequence", "score")),
        )
        for records, fields in integer_fields:
            for data in records:
                if any(type(data.get(field)) is not int for field in fields):
                    raise ValueError("Invalid puzzle practice counter")
        for data in archive.puzzle_daily_slots:
            if type(data.get("first_exposure")) is not bool:
                raise ValueError("Invalid first exposure flag")
        numeric_fields = (
            ([archive.puzzle_rating] if archive.puzzle_rating is not None else [], ("rating",)),
            (archive.puzzle_daily_sets, ("player_rating",)),
            (archive.puzzle_daily_slots, ("item_rating",)),
            (archive.puzzle_rating_events, ("before", "after", "item_rating")),
        )
        for records, snapshot_fields in numeric_fields:
            for data in records:
                if any(type(data.get(field)) not in (int, float) for field in snapshot_fields):
                    raise ValueError("Invalid puzzle practice snapshot")
        for data in archive.puzzle_daily_sets:
            if (
                data.get("policy_version") != POLICY_VERSION
                or data.get("theme_map_version") != THEME_MAP_VERSION
            ):
                raise ValueError("Missing or unsupported puzzle selection policy")
        for data in archive.puzzle_rating_events:
            if data.get("policy_version") != POLICY_VERSION:
                raise ValueError("Missing or unsupported puzzle rating policy")
        missing_puzzles = _validate_puzzle_archive(
            session,
            player_id,
            puzzle_items,
            puzzle_runs,
            study_sessions,
            content_attempts,
            puzzle_daily_slots,
        )
        _validate_puzzle_practice_archive(
            session,
            player_id,
            puzzle_rating,
            puzzle_daily_sets,
            puzzle_daily_slots,
            puzzle_rating_events,
            puzzle_runs,
            puzzle_items,
            content_attempts,
        )
        _validate_puzzle_modes_archive(
            session,
            player_id,
            puzzle_mode_bindings,
            puzzle_runs,
            puzzle_daily_slots,
            puzzle_rating_events,
        )
    except (ValueError, TypeError, AttributeError, LessonVerificationError) as error:
        raise HTTPException(
            status_code=400, detail="Invalid archive: malformed puzzle records"
        ) from error

    if "manual_bot_level" in player.preferences:
        manual = player.preferences["manual_bot_level"]
        if type(manual) is not int or not 1 <= manual <= 8:
            raise HTTPException(
                status_code=400, detail="Invalid archive: malformed manual bot choice",
            )

    try:
        onboarding = _validate_onboarding_archive(
            archive.onboarding, player_id, puzzle_runs, puzzle_daily_sets, puzzle_daily_slots,
        )
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(
            status_code=400, detail="Invalid archive: malformed onboarding",
        ) from error

    game_ids = {game.id for game in games}
    existing_games = {game.id: session.get(Game, game.id) for game in games}
    position_ids = {position.id for position in positions}
    position_ids_as_strings = {str(position_id) for position_id in position_ids}
    analysis_ids = {str(analysis.id) for analysis in engine_analyses}
    positions_by_id = {position.id: position for position in positions}
    study_session_ids = {study_session.id for study_session in study_sessions}
    analyses_by_id = {analysis.id: analysis for analysis in engine_analyses}
    lesson_opportunity_ids = {opportunity.id for opportunity in lesson_opportunities}
    transfer_position_ids = {position.id for position in transfer_positions}
    has_foreign_owner = (
        player.id != player_id
        or profile is not None
        and profile.player_id != player_id
        or any(
            play_session.player_id != player_id
            or play_session.game_id is not None
            and play_session.game_id not in game_ids
            for play_session in play_sessions
        )
        or any(game.owner_player_id != player_id for game in games)
        or any(
            existing_game is not None and existing_game.owner_player_id != player_id
            for existing_game in existing_games.values()
        )
        or any(skill_state.player_id != player_id for skill_state in skill_states)
        or any(schedule.player_id != player_id for schedule in review_schedules)
        or any(observation.player_id != player_id for observation in profile_observations)
        or any(study_session.player_id != player_id for study_session in study_sessions)
        or any(content_attempt.player_id != player_id for content_attempt in content_attempts)
        or any(attempt.player_id != player_id for attempt in lesson_attempts)
        or any(measurement.player_id != player_id for measurement in transfer_measurements)
        or any(position.game_id not in game_ids for position in positions)
        or any(analysis.position_id not in position_ids for analysis in engine_analyses)
        or any(analysis_job.game_id not in game_ids for analysis_job in analysis_jobs)
        or any(
            move.game_id not in game_ids
            or move.source_position_id not in positions_by_id
            or move.reached_position_id not in positions_by_id
            or positions_by_id[move.source_position_id].game_id != move.game_id
            or positions_by_id[move.reached_position_id].game_id != move.game_id
            or move.before_analysis_id not in analyses_by_id
            or move.after_analysis_id not in analyses_by_id
            or analyses_by_id[move.before_analysis_id].position_id != move.source_position_id
            or analyses_by_id[move.after_analysis_id].position_id != move.reached_position_id
            for move in review_moves
        )
        or any(
            item.position_id not in position_ids_as_strings
            or item.engine_analysis_id not in analysis_ids
            for item in evidence
        )
        or any(
            opportunity.source_position_id not in positions_by_id
            or positions_by_id[opportunity.source_position_id].game_id != opportunity.game_id
            for opportunity in lesson_opportunities
        )
        or any(
            observation.game_id not in {str(game_id) for game_id in game_ids}
            or observation.position_id not in position_ids_as_strings
            for observation in profile_observations
        )
        or any(
            attempt.session_id not in study_session_ids
            or attempt.opportunity_id is not None
            and attempt.opportunity_id not in lesson_opportunity_ids
            for attempt in lesson_attempts
        )
        or any(
            session.get(TransferPosition, position_id) is None
            for position_id in transfer_position_ids
        )
        or any(
            measurement.source_position_id not in transfer_position_ids
            or measurement.target_position_id is not None
            and measurement.target_position_id not in transfer_position_ids
            for measurement in transfer_measurements
        )
        or any(
            player_id not in (link.coach_id, link.student_id)
            or session.get(
                Player,
                link.student_id if link.coach_id == player_id else link.coach_id,
            )
            is None
            for link in coach_student_links
        )
        or any(
            content_attempt.session_id is not None
            and content_attempt.session_id not in study_session_ids
            for content_attempt in content_attempts
        )
    )
    if has_foreign_owner:
        raise HTTPException(
            status_code=400,
            detail="Invalid archive: records do not belong to the authorized player",
        )

    review_keys = {(move.game_id, move.ply) for move in review_moves}
    if len(review_keys) != len(review_moves) or len({move.id for move in review_moves}) != len(
        review_moves
    ):
        raise HTTPException(status_code=400, detail="Invalid archive: duplicate review moves")
    for move in review_moves:
        for position_id in (move.source_position_id, move.reached_position_id):
            existing_position = session.get(Position, position_id)
            if existing_position is not None and existing_position.game_id != move.game_id:
                raise HTTPException(status_code=400, detail="Invalid archive: conflicting position")
        for analysis_id, position_id in (
            (move.before_analysis_id, move.source_position_id),
            (move.after_analysis_id, move.reached_position_id),
        ):
            existing_analysis = session.get(EngineAnalysis, analysis_id)
            if existing_analysis is not None and existing_analysis.position_id != position_id:
                raise HTTPException(status_code=400, detail="Invalid archive: conflicting analysis")

    try:
        for data in archive.play_sessions:
            version = data.get("assistance_tracking_version")
            if version is not None and type(version) is not int:
                raise ValueError("Invalid assistance tracking version")
        for data in archive.session_hint_reveals:
            if any(type(data.get(key)) is not int for key in ("ply", "highest_step")):
                raise ValueError("Invalid hint snapshot counters")
        for data in archive.positive_move_credits:
            if any(type(data.get(key)) is not int for key in ("ply", "proof_version")):
                raise ValueError("Invalid positive credit counters")
        _validate_credit_archive(
            session,
            player_id,
            play_sessions,
            games,
            positions,
            engine_analyses,
            review_moves,
            session_hint_reveals,
            positive_move_credits,
        )
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(
            status_code=400, detail="Invalid archive: malformed assistance or credit provenance"
        ) from error
    try:
        (
            habit_policy, habit_goals, habit_days, habit_credits,
            pattern_targets, pattern_awards, pattern_award_evidence,
        ) = _validate_habit_archive(session, archive, player_id)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError) as error:
        raise HTTPException(
            status_code=400, detail="Invalid archive: malformed habit provenance",
        ) from error


    # Puzzle dependencies are the explicit global-catalog restore exception.
    # Existing rows (including metadata) are never overwritten.
    for puzzle_item in missing_puzzles:
        session.add(puzzle_item)
    session.flush()
    session.add(player)
    session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    if profile:
        session.add(profile)
    session.add(onboarding)

    for game in games:
        if session.get(Game, game.id) is None:
            session.add(game)

    for position in positions:
        if session.get(Position, position.id) is None:
            session.add(position)

    for analysis in engine_analyses:
        if session.get(EngineAnalysis, analysis.id) is None:
            session.add(analysis)
    for move in review_moves:
        existing_move = session.get(GameMoveReview, move.id)
        if existing_move is not None and existing_move.model_dump() != move.model_dump():
            raise HTTPException(status_code=400, detail="Invalid archive: conflicting review move")
        if existing_move is None:
            session.add(move)

    for analysis_job in analysis_jobs:
        if session.get(AnalysisJob, analysis_job.id) is None:
            session.add(analysis_job)

    for opportunity in lesson_opportunities:
        if session.get(PersistedLessonOpportunity, opportunity.id) is None:
            session.add(opportunity)
    for item in evidence:
        if session.get(Evidence, item.evidence_id) is None:
            session.add(item)

    for observation in profile_observations:
        session.add(observation)

    # TransferPosition rows are global curated references. Archives may describe
    # them for referential closure but never mutate the shared catalog.

    for measurement in transfer_measurements:
        if session.get(TransferMeasurement, measurement.id) is None:
            session.add(measurement)

    for link in coach_student_links:
        if session.get(CoachStudentLink, (link.coach_id, link.student_id)) is None:
            session.add(link)

    for play_session in play_sessions:
        session.add(play_session)
    session.flush()
    for reveal in session_hint_reveals:
        session.add(reveal)
    for credit in positive_move_credits:
        session.add(credit)

    for skill_state in skill_states:
        session.add(skill_state)

    for review_schedule in review_schedules:
        session.add(review_schedule)

    for study_session in study_sessions:
        session.add(study_session)

    session.flush()
    for run in puzzle_runs:
        session.add(run)
    session.flush()
    for binding in puzzle_mode_bindings:
        session.add(binding)
    if puzzle_rating is not None:
        session.add(puzzle_rating)
    for daily in puzzle_daily_sets:
        session.add(daily)
    session.flush()
    for slot in puzzle_daily_slots:
        session.add(slot)
    session.flush()
    # Restore the validated ledger verbatim; never call terminal award/schedule logic.
    for event in puzzle_rating_events:
        session.add(event)
    for content_attempt in content_attempts:
        session.add(content_attempt)

    for lesson_attempt in lesson_attempts:
        session.add(lesson_attempt)
    # Restore snapshots/cutover verbatim, without result or reward hooks.
    session.add(habit_policy)
    for habit_goal in habit_goals:
        session.add(habit_goal)
    for habit_day in habit_days:
        session.add(habit_day)
    for habit_credit in habit_credits:
        session.add(habit_credit)
    for pattern_target in pattern_targets:
        session.add(pattern_target)
    session.flush()
    for pattern_award in pattern_awards:
        session.add(pattern_award)
    session.flush()
    session.add_all(pattern_award_evidence)

    session.commit()
    return {"status": "imported"}


class DeletionRequest(BaseModel):
    dry_run: bool = True
    confirmation: str | None = None


class DeletionResponse(BaseModel):
    dry_run: bool
    affected_rows: dict[str, int]
    audit_id: str | None = None


@router.delete("/v1/players/{player_id}/data", response_model=DeletionResponse)
def delete_player_data(
    player_id: str,
    request: Request,
    req: DeletionRequest,
    session: Session = Depends(get_session),
) -> DeletionResponse:
    require_player_token(request, player_id, session)
    player = session.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    if not req.dry_run and req.confirmation != f"delete-{player_id}":
        raise HTTPException(
            status_code=400,
            detail="Invalid confirmation string. Must be 'delete-{player_id}'",
        )

    play_sessions = session.exec(
        select(PlaySession).where(col(PlaySession.player_id) == player_id)
    ).all()
    game_ids = {
        play_session.game_id for play_session in play_sessions if play_session.game_id
    } | set(session.exec(select(Game.id).where(col(Game.owner_player_id) == player_id)).all())
    shared_game_ids = (
        set(
            session.exec(
                select(PlaySession.game_id).where(
                    col(PlaySession.game_id).in_(game_ids),
                    col(PlaySession.player_id) != player_id,
                )
            ).all()
        )
        if game_ids
        else set()
    )
    disowned_game_ids = set(
        session.exec(
            select(Game.id).where(
                col(Game.id).in_(shared_game_ids),
                col(Game.owner_player_id) == player_id,
            )
        ).all()
    )
    owned_game_ids = list(game_ids - shared_game_ids)
    positions = (
        session.exec(select(Position).where(col(Position.game_id).in_(owned_game_ids))).all()
        if owned_game_ids
        else []
    )
    position_ids = [position.id for position in positions]
    engine_analyses = (
        session.exec(
            select(EngineAnalysis).where(col(EngineAnalysis.position_id).in_(position_ids))
        ).all()
        if position_ids
        else []
    )
    analysis_jobs = (
        session.exec(select(AnalysisJob).where(col(AnalysisJob.game_id).in_(owned_game_ids))).all()
        if owned_game_ids
        else []
    )
    review_moves = (
        session.exec(
            select(GameMoveReview).where(col(GameMoveReview.game_id).in_(owned_game_ids))
        ).all()
        if owned_game_ids
        else []
    )
    lesson_opportunities = session.exec(
        select(PersistedLessonOpportunity).where(
            (col(PersistedLessonOpportunity.game_id).in_(owned_game_ids))
            | (col(PersistedLessonOpportunity.player_id) == player_id)
        )
    ).all()
    evidence = (
        session.exec(
            select(Evidence).where(
                col(Evidence.position_id).in_([str(position_id) for position_id in position_ids])
            )
        ).all()
        if position_ids
        else []
    )
    profile_observations = session.exec(
        select(ProfileObservation).where(col(ProfileObservation.player_id) == player_id)
    ).all()
    lesson_attempts = session.exec(
        select(LessonAttempt).where(col(LessonAttempt.player_id) == player_id)
    ).all()
    transfer_measurements = session.exec(
        select(TransferMeasurement).where(col(TransferMeasurement.player_id) == player_id)
    ).all()

    coach_student_links = session.exec(
        select(CoachStudentLink).where(
            (col(CoachStudentLink.coach_id) == player_id)
            | (col(CoachStudentLink.student_id) == player_id)
        )
    ).all()

    affected_rows = {
        "player": 1,
        "player_credentials": 1 if session.get(PlayerCredential, player_id) is not None else 0,
        "profile": 1 if session.get(PlayerProfile, player_id) else 0,
        "onboarding": int(session.get(PlayerOnboarding, player_id) is not None),
        "play_sessions": len(play_sessions),
        "games": len(owned_game_ids),
        "games_disowned": len(disowned_game_ids),
        "positions": len(positions),
        "engine_analyses": len(engine_analyses),
        "review_moves": len(review_moves),
        "analysis_jobs": len(analysis_jobs),
        "lesson_opportunities": len(lesson_opportunities),
        "evidence": len(evidence),
        "profile_observations": len(profile_observations),
        "lesson_attempts": len(lesson_attempts),
        "habit_policy": int(session.get(HabitPolicy, player_id) is not None),
        "habit_goals": len(
            session.exec(select(HabitGoal).where(HabitGoal.player_id == player_id)).all()
        ),
        "habit_days": len(
            session.exec(select(HabitDay).where(HabitDay.player_id == player_id)).all()
        ),
        "habit_credits": len(
            session.exec(select(HabitCredit).where(HabitCredit.player_id == player_id)).all()
        ),
        "pattern_targets": len(
            session.exec(select(PatternTarget).where(PatternTarget.player_id == player_id)).all()
        ),
        "pattern_awards": len(
            session.exec(select(PatternAward).where(PatternAward.player_id == player_id)).all()
        ),
        "pattern_award_evidence": len(session.exec(
            select(PatternAwardEvidence).where(PatternAwardEvidence.player_id == player_id)
        ).all()),
        "transfer_measurements": len(transfer_measurements),
        "skill_states": len(
            session.exec(select(SkillState).where(col(SkillState.player_id) == player_id)).all()
        ),
        "review_schedules": len(
            session.exec(
                select(ReviewSchedule).where(col(ReviewSchedule.player_id) == player_id)
            ).all()
        ),
        "study_sessions": len(
            session.exec(select(StudySession).where(col(StudySession.player_id) == player_id)).all()
        ),
        "content_attempts": len(
            session.exec(
                select(ContentAttempt).where(col(ContentAttempt.player_id) == player_id)
            ).all()
        ),
        "puzzle_runs": len(
            session.exec(select(PuzzleRun).where(col(PuzzleRun.player_id) == player_id)).all()
        ),
        "coach_student_links": len(coach_student_links),
    }

    affected_rows["session_hint_reveals"] = len(
        session.exec(
            select(SessionHintReveal).where(col(SessionHintReveal.player_id) == player_id)
        ).all()
    )
    affected_rows["positive_move_credits"] = len(
        session.exec(
            select(PositiveMoveCredit).where(col(PositiveMoveCredit.player_id) == player_id)
        ).all()
    )
    affected_rows["puzzle_rating"] = int(session.get(PuzzleRating, player_id) is not None)
    affected_rows["puzzle_daily_sets"] = len(
        session.exec(select(PuzzleDailySet).where(col(PuzzleDailySet.player_id) == player_id)).all()
    )
    affected_rows["puzzle_daily_slots"] = len(
        session.exec(
            select(PuzzleDailySlot).where(col(PuzzleDailySlot.player_id) == player_id)
        ).all()
    )
    affected_rows["puzzle_rating_events"] = len(
        session.exec(
            select(PuzzleRatingEvent).where(col(PuzzleRatingEvent.player_id) == player_id)
        ).all()
    )
    affected_rows["puzzle_mode_bindings"] = len(
        session.exec(
            select(PuzzleModeBinding).where(col(PuzzleModeBinding.player_id) == player_id)
        ).all()
    )

    if req.dry_run:
        return DeletionResponse(dry_run=True, affected_rows=affected_rows)

    session.exec(
        delete(PatternAwardEvidence).where(col(PatternAwardEvidence.player_id) == player_id)
    )
    session.exec(delete(PatternAward).where(col(PatternAward.player_id) == player_id))
    session.exec(delete(PatternTarget).where(col(PatternTarget.player_id) == player_id))
    session.exec(delete(HabitCredit).where(col(HabitCredit.player_id) == player_id))
    session.exec(delete(HabitDay).where(col(HabitDay.player_id) == player_id))
    session.exec(delete(HabitGoal).where(col(HabitGoal.player_id) == player_id))
    session.exec(delete(HabitPolicy).where(col(HabitPolicy.player_id) == player_id))
    session.exec(delete(SessionHintReveal).where(col(SessionHintReveal.player_id) == player_id))
    session.exec(delete(PositiveMoveCredit).where(col(PositiveMoveCredit.player_id) == player_id))
    session.exec(delete(PuzzleModeBinding).where(col(PuzzleModeBinding.player_id) == player_id))
    session.exec(delete(PuzzleRatingEvent).where(col(PuzzleRatingEvent.player_id) == player_id))
    session.exec(delete(PuzzleDailySlot).where(col(PuzzleDailySlot.player_id) == player_id))
    session.exec(delete(PuzzleDailySet).where(col(PuzzleDailySet.player_id) == player_id))
    session.exec(delete(PuzzleRating).where(col(PuzzleRating.player_id) == player_id))

    if owned_game_ids:
        session.exec(delete(GameMoveReview).where(col(GameMoveReview.game_id).in_(owned_game_ids)))
    if position_ids:
        session.exec(
            delete(Evidence).where(
                col(Evidence.position_id).in_([str(position_id) for position_id in position_ids])
            )
        )
        session.exec(
            delete(EngineAnalysis).where(col(EngineAnalysis.position_id).in_(position_ids))
        )
    session.exec(delete(ProfileObservation).where(col(ProfileObservation.player_id) == player_id))
    session.exec(delete(LessonAttempt).where(col(LessonAttempt.player_id) == player_id))
    session.exec(delete(TransferMeasurement).where(col(TransferMeasurement.player_id) == player_id))
    session.exec(
        delete(PersistedLessonOpportunity).where(
            (col(PersistedLessonOpportunity.game_id).in_(owned_game_ids))
            | (col(PersistedLessonOpportunity.player_id) == player_id)
        )
    )
    if owned_game_ids:
        session.exec(delete(AnalysisJob).where(col(AnalysisJob.game_id).in_(owned_game_ids)))
        session.exec(delete(Position).where(col(Position.game_id).in_(owned_game_ids)))
    session.exec(delete(ContentAttempt).where(col(ContentAttempt.player_id) == player_id))
    session.exec(delete(PuzzleRun).where(col(PuzzleRun.player_id) == player_id))
    session.exec(delete(StudySession).where(col(StudySession.player_id) == player_id))
    session.exec(delete(ReviewSchedule).where(col(ReviewSchedule.player_id) == player_id))
    session.exec(delete(SkillState).where(col(SkillState.player_id) == player_id))
    session.exec(
        delete(CoachStudentLink).where(
            (col(CoachStudentLink.coach_id) == player_id)
            | (col(CoachStudentLink.student_id) == player_id)
        )
    )
    session.exec(delete(PlaySession).where(col(PlaySession.player_id) == player_id))
    if owned_game_ids:
        session.exec(delete(Game).where(col(Game.id).in_(owned_game_ids)))
    if disowned_game_ids:
        session.exec(
            update(Game)
            .where(col(Game.id).in_(disowned_game_ids))
            .values(
                owner_player_id=None,
                pgn="",
                white="Anonymous",
                black="Anonymous",
                headers={},
            )
        )

    profile = session.get(PlayerProfile, player_id)
    if profile:
        session.delete(profile)
    session.exec(delete(PlayerOnboarding).where(col(PlayerOnboarding.player_id) == player_id))
    session.exec(delete(PlayerCredential).where(col(PlayerCredential.player_id) == player_id))
    session.delete(player)
    principal = hashlib.sha256(request.headers["Authorization"].encode("utf-8")).hexdigest()
    session.exec(
        delete(IdempotencyRecord).where(
            col(IdempotencyRecord.idempotency_key).contains(f":{principal}:")
        )
    )

    audit_id = str(uuid4())
    session.add(DeletionAudit(id=audit_id, player_id=player_id, affected_rows=affected_rows))
    session.commit()
    request.state.skip_idempotency_cache = True

    return DeletionResponse(dry_run=False, affected_rows=affected_rows, audit_id=audit_id)

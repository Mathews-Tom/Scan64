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
from sqlmodel import Session, col, delete, select

from scan64.api.auth import require_player_token
from scan64.api.middleware import IdempotencyRecord
from scan64.api.models import DeletionAudit, Player, PlayerCredential, PlayerProfile
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
from scan64.content.puzzle_runs import PuzzleRun
from scan64.content.validate import validate_solution_line
from scan64.learning.evaluation.transfer_measurement import TransferMeasurement
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.transfer import TransferPosition
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson
from scan64.persistence.database import get_session

router = APIRouter(tags=["data_lifecycle"])


class ExportRequest(BaseModel):
    player_id: str


class ExportArchive(BaseModel):
    player: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
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
    credential_hash: str | None = None


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
    if credits and (
        len(games_by_id) != len(games)
        or len(positions_by_id) != len(positions)
        or len(analyses_by_id) != len(analyses)
    ):
        raise ValueError("Ambiguous positive credit references")
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

    # Puzzle dependencies are the explicit global-catalog restore exception.
    # Existing rows (including metadata) are never overwritten.
    for puzzle_item in missing_puzzles:
        session.add(puzzle_item)
    session.flush()
    session.add(player)
    session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    if profile:
        session.add(profile)

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

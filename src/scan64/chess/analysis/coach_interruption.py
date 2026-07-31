from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import chess
from chess_lesson_spec import Diagnosis, Explanation, LessonSpec
from sqlmodel import Session, select

from scan64.api.models import PlayerProfile
from scan64.chess.analysis.jobs import _resolve_pattern_detectors
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.ingestion import resolve_position
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.models import StudySession
from scan64.explanations.assembly import resolve_explanation
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.evidence.composer import compose_candidate_evidence
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.profiling.profile_update import apply_analysis_observation
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson
from scan64.providers.stockfish.pool import EnginePoolManager

COACH_DIAGNOSTIC_NODES = 10_000
COACH_DIAGNOSTIC_SWING_CP = 200
COACH_DIAGNOSTIC_TIMEOUT_SECONDS = 2.0
COACH_EXPLANATION_TIMEOUT_SECONDS = 0.25
COACH_PREPARATION_TIMEOUT_SECONDS = (
    COACH_DIAGNOSTIC_TIMEOUT_SECONDS - COACH_EXPLANATION_TIMEOUT_SECONDS
)


@dataclass(frozen=True)
class CoachInterruption:
    lesson: LessonSpec
    opportunity_id: UUID
    study_session_id: str


@dataclass(frozen=True)
class PreparedCoachInterruption:
    interruption: CoachInterruption
    player_id: str
    game_id: UUID
    skill_id: str
    observed_at: datetime
    source_position: Position
    result_position: Position
    before_analysis: EngineAnalysis
    after_analysis: EngineAnalysis
    evidence: tuple[Evidence, ...]
    opportunity: PersistedLessonOpportunity
    schedule: ReviewSchedule
    study_session: StudySession

    def add_to(self, session: Session) -> None:
        for row in (
            self.source_position,
            self.result_position,
            self.before_analysis,
            self.after_analysis,
            *self.evidence,
            self.opportunity,
            self.schedule,
            self.study_session,
        ):
            session.add(row)
        profile = session.get(PlayerProfile, self.player_id)
        apply_analysis_observation(
            session=session,
            player_id=self.player_id,
            game_id=str(self.game_id),
            position_id=str(self.result_position.id),
            skill_id=self.skill_id,
            rating=profile.rating if profile is not None else 1500,
            observed_at=self.observed_at,
        )


def _white_score_cp(analysis: EngineAnalysis, side_to_move: chess.Color) -> int | None:
    if not analysis.raw_result:
        return None
    result = analysis.raw_result[0]
    score_mate = result.get("score_mate")
    if isinstance(score_mate, int):
        score = 10_000 - abs(score_mate) * 10 if score_mate > 0 else -10_000 + abs(score_mate) * 10
        return score if side_to_move == chess.WHITE else -score
    score_cp = result.get("score_cp")
    if isinstance(score_cp, int):
        return score_cp if side_to_move == chess.WHITE else -score_cp
    return None


def _principal_variation_move(analysis: EngineAnalysis, board: chess.Board) -> chess.Move | None:
    if not analysis.raw_result:
        return None
    line = analysis.raw_result[0].get("pv")
    if not isinstance(line, list) or not line or not isinstance(line[0], str):
        return None
    try:
        move = chess.Move.from_uci(line[0])
    except ValueError:
        return None
    return move if move in board.legal_moves else None


async def _resolve_coach_explanation(
    diagnosis: Diagnosis, evidence: list[Evidence], fen: str
) -> Explanation:
    try:
        return await asyncio.wait_for(
            resolve_explanation(diagnosis, evidence, fen),
            timeout=COACH_EXPLANATION_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return await TemplateExplanationProvider().explain(diagnosis, evidence)


async def prepare_coach_interruption(
    *,
    session: Session,
    game: Game,
    player_id: str,
    before_board: chess.Board,
    after_board: chess.Board,
    played_move: str,
    history_san: list[str],
    pool_manager: EnginePoolManager | None,
) -> PreparedCoachInterruption | None:
    """Prepare an interruption within the diagnostic and explanation budgets."""
    if pool_manager is None:
        return None
    try:
        async with asyncio.timeout(COACH_PREPARATION_TIMEOUT_SECONDS):
            prepared = await _prepare_coach_interruption(
                session=session,
                game=game,
                player_id=player_id,
                before_board=before_board,
                after_board=after_board,
                played_move=played_move,
                history_san=history_san,
                pool_manager=pool_manager,
            )
    except TimeoutError:
        return None
    if prepared is None:
        return None

    lesson = prepared.interruption.lesson
    lesson.explanation = await _resolve_coach_explanation(
        lesson.diagnosis, list(prepared.evidence), prepared.source_position.fen
    )
    prepared.opportunity.lesson_spec = lesson.model_dump(mode="json")
    return prepared


async def _prepare_coach_interruption(
    *,
    session: Session,
    game: Game,
    player_id: str,
    before_board: chess.Board,
    after_board: chess.Board,
    played_move: str,
    history_san: list[str],
    pool_manager: EnginePoolManager,
) -> PreparedCoachInterruption | None:
    """Prepare database rows for a coach interruption without mutating state."""
    before_analysis = await pool_manager.analyze_interactive(
        before_board.fen(), nodes=COACH_DIAGNOSTIC_NODES
    )
    after_analysis = await pool_manager.analyze_interactive(
        after_board.fen(), nodes=COACH_DIAGNOSTIC_NODES
    )
    before_score = _white_score_cp(before_analysis, before_board.turn)
    after_score = _white_score_cp(after_analysis, after_board.turn)
    if before_score is None or after_score is None:
        return None

    player_is_white = before_board.turn == chess.WHITE
    player_before = before_score if player_is_white else -before_score
    player_after = after_score if player_is_white else -after_score
    swing_cp = player_before - player_after
    if swing_cp < COACH_DIAGNOSTIC_SWING_CP:
        return None

    source_position = resolve_position(session, before_board.fen(), game.id)
    existing_opportunity = session.exec(
        select(PersistedLessonOpportunity.id).where(
            PersistedLessonOpportunity.game_id == game.id,
            PersistedLessonOpportunity.player_id == player_id,
            PersistedLessonOpportunity.source_position_id == source_position.id,
        )
    ).first()
    if existing_opportunity is not None:
        return None
    result_position = resolve_position(session, after_board.fen(), game.id)
    before_analysis.position_id = source_position.id
    after_analysis.position_id = result_position.id
    evidence = compose_candidate_evidence(
        before_board=before_board,
        after_board=after_board,
        history_san=history_san,
        initial_fen=game.headers.get("FEN"),
        position_id=str(result_position.id),
        fast_analysis=before_analysis,
        focused_analysis=after_analysis,
        analysis_depth="interactive",
        played_move=played_move,
        swing_cp=swing_cp,
    )
    learning_opportunity = LearningOpportunity(
        opportunity_id=f"opp_{uuid4()}",
        position_id=str(result_position.id),
        player_id=player_id,
        game_id=str(game.id),
        played_move=played_move,
        engine_eval_before=player_before / 100.0,
        engine_eval_after=player_after / 100.0,
    )
    diagnosis_candidates = []
    player_context = PlayerContext(player_id=player_id)
    for detector in _resolve_pattern_detectors():
        diagnosis_candidates.extend(
            await detector.detect(learning_opportunity, evidence, player_context)
        )
    selection = arbitrate_diagnoses(diagnosis_candidates, evidence)
    if selection is None:
        return None
    primary, secondary = selection
    objective_move = _principal_variation_move(before_analysis, before_board)
    if objective_move is None:
        return None

    opportunity_id = uuid4()
    diagnosis = Diagnosis(
        primary=primary.skill_id,
        secondary=[candidate.skill_id for candidate in secondary],
        confidence=primary.confidence,
        evidence_refs=primary.evidence_ids,
    )
    lesson = await generate_exact_replay_exercise(
        diagnosis=diagnosis,
        fen=before_board.fen(),
        lesson_id=str(opportunity_id),
        best_move_san=before_board.san(objective_move),
    )
    lesson.explanation = await TemplateExplanationProvider().explain(diagnosis, evidence)
    try:
        verify_lesson(lesson, before_analysis)
    except LessonVerificationError:
        return None

    now = datetime.now(UTC)
    opportunity = PersistedLessonOpportunity(
        id=opportunity_id,
        game_id=game.id,
        source_position_id=source_position.id,
        player_id=player_id,
        lesson_spec=lesson.model_dump(mode="json"),
        verification_status=lesson.verification.status,
        verification_analysis_id=before_analysis.id,
    )
    schedule = ReviewSchedule(
        player_id=player_id,
        item_id=str(opportunity.id),
        skill_id=primary.skill_id,
        next_review_at=now,
    )
    study_session = StudySession(
        player_id=player_id,
        domain=f"coach_interruption:{game.id}",
    )
    interruption = CoachInterruption(
        lesson=lesson,
        opportunity_id=opportunity.id,
        study_session_id=study_session.id,
    )
    return PreparedCoachInterruption(
        interruption=interruption,
        player_id=player_id,
        game_id=game.id,
        skill_id=primary.skill_id,
        observed_at=now,
        source_position=source_position,
        result_position=result_position,
        before_analysis=before_analysis,
        after_analysis=after_analysis,
        evidence=tuple(evidence),
        opportunity=opportunity,
        schedule=schedule,
        study_session=study_session,
    )

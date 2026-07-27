import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import chess
from chess_lesson_spec import Diagnosis
from sqlmodel import Session, select

from scan64.api.models import PlayerProfile
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.analysis.orchestration import (
    FastPassConfig,
    FastPassOrchestrator,
    FocusedPassConfig,
    FocusedPassOrchestrator,
)
from scan64.chess.boards import board_from, uci_moves_to_san
from scan64.chess.games.ingestion import ingest_fen
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.evidence.composer import compose_candidate_evidence
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.plugins.host_registry import get_host_registry
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry
from scan64.learning.profiling.profile_update import apply_analysis_observation
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig


def _resolve_pattern_detectors(
    registry: PluginRegistry | None = None,
) -> tuple[PatternDetector, ...]:
    active_registry = registry if registry is not None else get_host_registry()
    detectors: list[PatternDetector] = []
    for name in active_registry.names(kind=PluginKind.PATTERN_DETECTOR):
        detector = active_registry.get(kind=PluginKind.PATTERN_DETECTOR, name=name)
        if not isinstance(detector, PatternDetector):
            raise RuntimeError(f"Registered detector {name!r} violates the plugin contract")
        detectors.append(detector)
    if not detectors:
        raise RuntimeError("The host plugin registry has no pattern detectors")
    return tuple(detectors)


def _persist_position_analysis(
    game: Game,
    fen: str,
    analysis: EngineAnalysis,
    session: Session,
    positions_by_fen: dict[str, Position],
) -> Position:
    position = positions_by_fen.get(fen)
    if position is None:
        position = session.exec(
            select(Position).where(Position.game_id == game.id, Position.fen == fen)
        ).first()
        if position is None:
            position = ingest_fen(fen, game.id)
            session.add(position)
        positions_by_fen[fen] = position
    if analysis.position_id != position.id:
        analysis.position_id = position.id
        session.add(analysis)
    return position


async def run_analysis_for_game(
    game: Game,
    session: Session,
    registry: PluginRegistry | None = None,
) -> None:
    if game.owner_player_id is None:
        raise ValueError("Cannot analyse a game without an owner")

    adapter = StockfishAdapter(StockfishConfig())
    orchestrator = FastPassOrchestrator(
        adapter, FastPassConfig(nodes=10000, swing_threshold_cp=150)
    )
    detectors = _resolve_pattern_detectors(registry)
    explanation_provider = TemplateExplanationProvider()
    ctx = PlayerContext(player_id=game.owner_player_id)

    initial_fen = game.headers.get("FEN")
    san_moves = uci_moves_to_san(game.moves, initial_fen)
    if not san_moves:
        return

    candidates = await orchestrator.run_fast_pass(san_moves, initial_fen)
    focused_orchestrator = FocusedPassOrchestrator(
        adapter, FocusedPassConfig(nodes=1_000_000, multipv=4)
    )
    focused_analyses = await focused_orchestrator.run_focused_pass(candidates)
    if len(focused_analyses) != len(candidates):
        raise RuntimeError("Focused pass did not return one analysis for every candidate")

    board = board_from(initial_fen)
    fens_before = [board.fen()]
    for san in san_moves:
        board.push_san(san)
        fens_before.append(board.fen())

    positions_by_fen: dict[str, Position] = {}
    for candidate, focused_analysis in zip(candidates, focused_analyses, strict=True):
        fen_before = fens_before[candidate.move_index]
        _persist_position_analysis(
            game, fen_before, candidate.before_analysis, session, positions_by_fen
        )
        after_position = _persist_position_analysis(
            game, candidate.fen, candidate.after_analysis, session, positions_by_fen
        )

        _persist_position_analysis(game, candidate.fen, focused_analysis, session, positions_by_fen)
        evidence = compose_candidate_evidence(
            before_board=board_from(fen_before),
            after_board=board_from(candidate.fen),
            history_san=san_moves[: candidate.move_index + 1],
            initial_fen=initial_fen,
            position_id=str(after_position.id),
            fast_analysis=candidate.before_analysis,
            focused_analysis=focused_analysis,
            played_move=san_moves[candidate.move_index],
            swing_cp=candidate.swing_cp,
        )
        for item in evidence:
            session.add(item)

        opportunity = LearningOpportunity(
            opportunity_id=f"opp_{uuid4()}",
            position_id=str(after_position.id),
            player_id=game.owner_player_id,
            game_id=str(game.id),
            played_move=san_moves[candidate.move_index],
            engine_eval_before=0.0,
            engine_eval_after=-(candidate.swing_cp / 100.0),
        )

        diagnosis_candidates = []
        for detector in detectors:
            diagnosis_candidates.extend(await detector.detect(opportunity, evidence, ctx))
        selection = arbitrate_diagnoses(diagnosis_candidates, evidence)
        if selection is None:
            continue
        best, secondary = selection

        fen_before = fens_before[candidate.move_index]
        best_move_uci = None
        if candidate.before_analysis.raw_result:
            pv = candidate.before_analysis.raw_result[0].get("pv") or []
            best_move_uci = pv[0] if pv else None

        board_before = chess.Board(fen_before)
        best_move_san = (
            board_before.san(chess.Move.from_uci(best_move_uci))
            if best_move_uci
            else san_moves[candidate.move_index]
        )

        diagnosis = Diagnosis(
            primary=best.skill_id,
            secondary=[candidate.skill_id for candidate in secondary],
            confidence=best.confidence,
            evidence_refs=best.evidence_ids,
        )
        profile = session.get(PlayerProfile, game.owner_player_id)
        apply_analysis_observation(
            session=session,
            player_id=game.owner_player_id,
            game_id=str(game.id),
            position_id=str(after_position.id),
            skill_id=best.skill_id,
            rating=profile.rating if profile is not None else 1500,
            observed_at=datetime.now(UTC),
        )

        lesson = await generate_exact_replay_exercise(
            diagnosis=diagnosis,
            fen=fen_before,
            lesson_id=f"les_{uuid4()}",
            best_move_san=best_move_san,
            hints=[],
        )
        lesson.explanation = await explanation_provider.explain(diagnosis, evidence)

        try:
            verify_lesson(lesson)
        except LessonVerificationError:
            continue

        persisted = PersistedLessonOpportunity(
            game_id=game.id,
            player_id=game.owner_player_id,
            lesson_spec=lesson.model_dump(mode="json"),
        )
        session.add(persisted)
        session.add(
            ReviewSchedule(
                player_id=game.owner_player_id,
                item_id=str(persisted.id),
                skill_id=best.skill_id,
                next_review_at=datetime.now(UTC),
            )
        )

    session.commit()


def execute_analysis_job(job_id: UUID) -> None:
    from datetime import UTC, datetime

    from scan64.persistence.database import engine

    with Session(engine) as session:
        job = session.get(AnalysisJob, job_id)
        if not job:
            return

        game = session.get(Game, job.game_id)
        if not game:
            job.status = "failed"
            job.error = "Game not found"
            session.add(job)
            session.commit()
            return

        job.status = "running"
        session.add(job)
        session.commit()

        try:
            asyncio.run(run_analysis_for_game(game, session))
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
        except Exception as error:
            session.rollback()
            failed_job = session.get(AnalysisJob, job_id)
            if failed_job is None:
                return
            failed_job.status = "failed"
            failed_job.error = str(error)
            session.add(failed_job)
            session.commit()
            return

        session.add(job)
        session.commit()

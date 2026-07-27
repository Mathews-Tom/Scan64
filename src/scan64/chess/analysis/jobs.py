import asyncio
from uuid import UUID, uuid4

import chess
from chess_lesson_spec import Diagnosis
from sqlmodel import Session, select

from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.chess.games.ingestion import ingest_fen
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.learning.diagnosis.detectors.board_awareness import HangingPieceDetector
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig


def _uci_moves_to_san(uci_moves: list[str]) -> list[str]:
    board = chess.Board()
    san_moves = []
    for uci in uci_moves:
        move = chess.Move.from_uci(uci)
        san_moves.append(board.san(move))
        board.push(move)
    return san_moves


def _classify_hanging_piece(fen_after_move: str) -> dict[str, object] | None:
    board = chess.Board(fen_after_move)
    mover_color = not board.turn
    opponent_color = board.turn
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece is None or piece.color != mover_color or piece.piece_type == chess.KING:
            continue
        if board.is_attacked_by(opponent_color, square) and not board.is_attacked_by(
            mover_color, square
        ):
            return {
                "is_hanging_piece_blunder": True,
                "hanging_square": chess.square_name(square),
                "hanging_piece": piece.symbol(),
            }
    return None


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


async def run_analysis_for_game(game: Game, session: Session) -> None:
    if game.owner_player_id is None:
        raise ValueError("Cannot analyse a game without an owner")

    adapter = StockfishAdapter(StockfishConfig())
    orchestrator = FastPassOrchestrator(
        adapter, FastPassConfig(nodes=10000, swing_threshold_cp=150)
    )
    detector = HangingPieceDetector()
    explanation_provider = TemplateExplanationProvider()
    ctx = PlayerContext(player_id=game.owner_player_id)

    san_moves = _uci_moves_to_san(game.moves)
    if not san_moves:
        return

    candidates = await orchestrator.run_fast_pass(san_moves)

    board = chess.Board()
    fens_before = [board.fen()]
    for san in san_moves:
        board.push_san(san)
        fens_before.append(board.fen())

    positions_by_fen: dict[str, Position] = {}
    for candidate in candidates:
        fen_before = fens_before[candidate.move_index]
        _persist_position_analysis(
            game, fen_before, candidate.before_analysis, session, positions_by_fen
        )
        after_position = _persist_position_analysis(
            game, candidate.fen, candidate.after_analysis, session, positions_by_fen
        )

        evidence_payload = _classify_hanging_piece(candidate.fen)
        if evidence_payload is None:
            continue

        evidence = Evidence(
            evidence_id=f"ev_{uuid4()}",
            kind="blunder_analysis",
            position_id=str(after_position.id),
            engine_analysis_id=str(candidate.after_analysis.id),
            claim="a piece was left undefended and attacked after the move",
            payload=evidence_payload,
        )
        session.add(evidence)

        opportunity = LearningOpportunity(
            opportunity_id=f"opp_{uuid4()}",
            position_id=str(after_position.id),
            player_id=game.owner_player_id,
            game_id=str(game.id),
            played_move=san_moves[candidate.move_index],
            engine_eval_before=0.0,
            engine_eval_after=-(candidate.swing_cp / 100.0),
        )

        diagnosis_candidates = await detector.detect(opportunity, [evidence], ctx)
        if not diagnosis_candidates:
            continue

        best = max(diagnosis_candidates, key=lambda c: c.confidence)

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
            confidence=best.confidence,
            evidence_refs=best.evidence_ids,
        )

        lesson = await generate_exact_replay_exercise(
            diagnosis=diagnosis,
            fen=fen_before,
            lesson_id=f"les_{uuid4()}",
            best_move_san=best_move_san,
            hints=[],
        )
        lesson.explanation = await explanation_provider.explain(diagnosis)

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

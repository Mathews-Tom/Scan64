import glob
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

import chess

from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.chess.games.ingestion import InvalidGameError, ingest_pgn
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.learning.diagnosis.detectors.board_awareness import HangingPieceDetector
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson
from scan64.lessonspec.models import Diagnosis, LessonSpec
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig


def _uci_moves_to_san(uci_moves: list[str]) -> list[str]:
    """Replay a UCI move sequence from the standard start position, returning SAN."""
    board = chess.Board()
    san_moves = []
    for uci in uci_moves:
        move = chess.Move.from_uci(uci)
        san_moves.append(board.san(move))
        board.push(move)
    return san_moves


def _classify_hanging_piece(fen_after_move: str) -> dict[str, object] | None:
    """
    Inspect the resulting position for a piece belonging to the side that
    just moved which is attacked and has no defender. This is derived
    purely from board state (python-chess attack maps), not a fabricated
    or hardcoded label.
    """
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


async def analyse_command(file_patterns: list[str], report: bool = False) -> None:
    """
    Analyse PGN files end to end: ingest and validate each game, run a
    Stockfish fast pass to flag candidate critical positions, classify
    blunder evidence from the resulting board state, run the event-tier
    detectors against that evidence, generate and verify an exact-replay
    LessonSpec for each diagnosed opportunity, and report recurring
    weaknesses (the same skill_id diagnosed across multiple games).
    """
    files: list[Path] = []
    for pattern in file_patterns:
        for matched_path in sorted(glob.glob(pattern)):
            files.append(Path(matched_path))

    if not files:
        print(f"No files found for patterns: {file_patterns}")
        return

    print(f"Analysing {len(files)} files...")

    adapter = StockfishAdapter(StockfishConfig())
    orchestrator = FastPassOrchestrator(
        adapter, FastPassConfig(nodes=10000, swing_threshold_cp=150)
    )
    detector = HangingPieceDetector()
    explanation_provider = TemplateExplanationProvider()
    ctx = PlayerContext(player_id="cli_player")

    lessons: list[LessonSpec] = []
    skill_games: dict[str, set[str]] = defaultdict(set)

    for file_path in files:
        pgn_text = file_path.read_text()
        try:
            game, _positions = ingest_pgn(pgn_text)
        except InvalidGameError as exc:
            print(f"Skipping {file_path.name}: {exc}")
            continue

        san_moves = _uci_moves_to_san(game.moves)
        if not san_moves:
            continue

        candidates = await orchestrator.run_fast_pass(san_moves)

        # FEN before each move index, needed for the exact-replay decision point.
        board = chess.Board()
        fens_before = [board.fen()]
        for san in san_moves:
            board.push_san(san)
            fens_before.append(board.fen())

        for candidate in candidates:
            evidence_payload = _classify_hanging_piece(candidate.fen)
            if evidence_payload is None:
                continue

            evidence = Evidence(
                evidence_id=f"ev_{uuid4()}",
                kind="blunder_analysis",
                position_id=candidate.fen,
                engine_analysis_id=str(uuid4()),
                claim="a piece was left undefended and attacked after the move",
                payload=evidence_payload,
            )

            opportunity = LearningOpportunity(
                opportunity_id=f"opp_{uuid4()}",
                position_id=candidate.fen,
                player_id="cli_player",
                game_id=file_path.name,
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
            except LessonVerificationError as exc:
                print(f"Rejected lesson from {file_path.name}: {exc}")
                continue

            lessons.append(lesson)
            skill_games[best.skill_id].add(file_path.name)

    recurring = {skill: games for skill, games in skill_games.items() if len(games) >= 2}

    if report:
        print("\n--- Analysis Report ---")
        print(f"Generated {len(lessons)} LessonSpec(s) from {len(files)} game(s).")
        if recurring:
            for skill, games in sorted(recurring.items()):
                names = ", ".join(sorted(games))
                print(f"Recurring weakness: {skill} across {len(games)} games ({names})")
        else:
            print("No recurring weakness (same skill_id in >=2 games) detected.")
        print(f"Status: {'SUCCESS' if lessons else 'NO_LESSONS_GENERATED'}")

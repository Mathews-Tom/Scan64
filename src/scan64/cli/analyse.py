import glob
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

import chess
from chess_lesson_spec import Diagnosis, LessonSpec

from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.chess.boards import board_from, uci_moves_to_san
from scan64.chess.games.ingestion import InvalidGameError, ingest_pgn
from scan64.explanations.assembly import resolve_explanation
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.detectors.board_awareness import HangingPieceDetector
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.evidence.composer import compose_candidate_evidence
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.exercises.hints import fill_lesson_hints
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig


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

        initial_fen = game.headers.get("FEN")
        san_moves = uci_moves_to_san(game.moves, initial_fen)
        if not san_moves:
            continue

        candidates = await orchestrator.run_fast_pass(san_moves, initial_fen)

        # FEN before each move index, needed for the exact-replay decision point.
        board = board_from(initial_fen)
        fens_before = [board.fen()]
        for san in san_moves:
            board.push_san(san)
            fens_before.append(board.fen())

        for candidate in candidates:
            focused = await adapter.analyze_position(candidate.fen, nodes=1_000_000, multipv=4)
            evidence = compose_candidate_evidence(
                before_board=board_from(fens_before[candidate.move_index]),
                after_board=board_from(candidate.fen),
                history_san=san_moves[: candidate.move_index + 1],
                initial_fen=initial_fen,
                position_id=candidate.fen,
                fast_analysis=candidate.before_analysis,
                reply_analysis=candidate.after_analysis,
                focused_analysis=focused,
                played_move=san_moves[candidate.move_index],
                swing_cp=candidate.swing_cp,
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

            diagnosis_candidates = await detector.detect(opportunity, evidence, ctx)
            selection = arbitrate_diagnoses(diagnosis_candidates, evidence)
            if selection is None:
                continue
            best, _secondary = selection

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
                accepted_moves_san=[best_move_san],
            )
            lesson.explanation = await resolve_explanation(diagnosis, evidence, fen_before)

            try:
                fill_lesson_hints(lesson, candidate.before_analysis)
                verify_lesson(lesson, candidate.before_analysis)
            except (LessonVerificationError, ValueError) as exc:
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

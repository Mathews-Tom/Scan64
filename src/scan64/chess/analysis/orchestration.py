from dataclasses import dataclass

import chess

from scan64.chess.analysis.models import EngineAnalysis
from scan64.providers.stockfish.adapter import StockfishAdapter


@dataclass
class FastPassConfig:
    nodes: int = 100000
    swing_threshold_cp: int = 150  # 1.5 pawns


@dataclass
class CandidatePosition:
    fen: str
    move_index: int
    before_analysis: EngineAnalysis
    after_analysis: EngineAnalysis
    swing_cp: int


class FastPassOrchestrator:
    def __init__(self, adapter: StockfishAdapter, config: FastPassConfig = FastPassConfig()):
        self.adapter = adapter
        self.config = config

    async def run_fast_pass(self, pgn_moves: list[str]) -> list[CandidatePosition]:
        """
        Runs a fast pass over a sequence of moves to flag candidate critical positions.
        """
        board = chess.Board()

        # We need the FEN before the move, and the FEN after the move.
        # But to be efficient, we can just evaluate every position reached.
        evaluations: list[EngineAnalysis] = []

        # Evaluate initial position
        eval_pos = await self.adapter.analyze_position(board.fen(), nodes=self.config.nodes)
        evaluations.append(eval_pos)

        for move_str in pgn_moves:
            move = board.parse_san(move_str)
            board.push(move)
            eval_pos = await self.adapter.analyze_position(board.fen(), nodes=self.config.nodes)
            evaluations.append(eval_pos)

        candidates = []
        board = chess.Board()

        for i, move_str in enumerate(pgn_moves):
            before_eval = evaluations[i]
            after_eval = evaluations[i + 1]

            # Extract scores. pov to white to compare absolute swings
            # Actually, score_cp in EngineAnalysis is from the perspective of the side to move.
            # We need to normalize to absolute advantage (e.g. from White's perspective)

            def get_white_cp(analysis: EngineAnalysis, is_white_to_move: bool) -> float:
                raw = analysis.raw_result[0]
                if "score_mate" in raw:
                    mate_val = int(raw["score_mate"])
                    # If mate in N, treat as a very large CP
                    cp = 10000 - abs(mate_val) * 10 if mate_val > 0 else -10000 + abs(mate_val) * 10
                    return float(cp if is_white_to_move else -cp)
                elif "score_cp" in raw:
                    cp = int(raw["score_cp"])
                    return float(cp if is_white_to_move else -cp)
                return 0.0

            is_white_before = board.turn == chess.WHITE
            cp_before = get_white_cp(before_eval, is_white_before)

            board.push_san(move_str)

            is_white_after = board.turn == chess.WHITE
            cp_after = get_white_cp(after_eval, is_white_after)

            # If the evaluation dropped significantly for the player who just moved
            swing = cp_after - cp_before

            # If White moved, a negative swing is bad for White.
            # If Black moved, a positive swing is bad for Black.
            player_swing = swing if is_white_before else -swing

            if player_swing < -self.config.swing_threshold_cp:
                candidates.append(
                    CandidatePosition(
                        fen=board.fen(),  # Position after the mistake
                        move_index=i,
                        before_analysis=before_eval,
                        after_analysis=after_eval,
                        swing_cp=int(-player_swing),
                    )
                )

        return candidates


@dataclass
class FocusedPassConfig:
    nodes: int = 1000000
    multipv: int = 4


class FocusedPassOrchestrator:
    def __init__(self, adapter: StockfishAdapter, config: FocusedPassConfig = FocusedPassConfig()):
        self.adapter = adapter
        self.config = config

    async def run_focused_pass(self, candidates: list[CandidatePosition]) -> list[EngineAnalysis]:
        """
        Runs a focused pass over a list of candidate positions to get deep MultiPV analysis.
        """
        results = []
        for candidate in candidates:
            analysis = await self.adapter.analyze_position(
                candidate.fen, nodes=self.config.nodes, multipv=self.config.multipv
            )
            results.append(analysis)
        return results

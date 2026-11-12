"""Repeatable, full-game adjacent-level calibration using the live bot policy.

Each pass plays ten fixed openings, each with the higher level once as White
and once as Black: exactly twenty games per adjacent pair. Draws are normal
chess outcomes (including claimable repetition/fifty-move draws), or adjudicated
after 200 full moves beyond the opening. No evaluation-based wins, resignations,
cached moves, or synthetic results are used. Both passes compare complete game
records, not just aggregate scores. Determinism is scoped to the reported engine
binary/version, one thread, node budget, openings and seeds; Stockfish's native
reduced-strength bestmove RNG is deliberately not used by the shared policy.
"""

import hashlib
from dataclasses import dataclass

import chess
import chess.engine

from scan64.chess.opponents.bot_policy import DEFAULT_BOT_NODES
from scan64.chess.opponents.bots import BOT_LEVELS
from scan64.chess.opponents.protocols import OpponentContext, OpponentPolicy
from scan64.chess.opponents.stockfish_opponent import StockfishOpponentProvider
from scan64.chess.positions.models import Position
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager

# Ten short, legal opening lines, covering open, semi-open and closed positions.
CALIBRATION_OPENINGS: tuple[tuple[str, ...], ...] = (
    (),
    ("e2e4", "e7e5"),
    ("d2d4", "d7d5"),
    ("e2e4", "c7c5"),
    ("e2e4", "e7e6", "d2d4", "d7d5"),
    ("e2e4", "c7c6", "d2d4", "d7d5"),
    ("d2d4", "g8f6", "c2c4", "e7e6"),
    ("d2d4", "d7d5", "c2c4", "e7e6"),
    ("c2c4", "e7e5"),
    ("g1f3", "d7d5", "g2g3", "g8f6"),
)
CALIBRATION_SEEDS = (11, 29, 47, 71, 101, 131, 173, 211, 257, 307)
MAX_GAME_PLIES = 400


@dataclass(frozen=True)
class CalibrationGame:
    white_level: int
    black_level: int
    seed: int
    opening: tuple[str, ...]
    moves: tuple[str, ...]
    result: str

    def higher_score(self) -> float:
        if self.result == "1/2-1/2":
            return 0.5
        white_won = self.result == "1-0"
        higher_is_white = self.white_level > self.black_level
        return float(white_won == higher_is_white)


async def play_calibration_game(
    provider: OpponentPolicy,
    white_level: int,
    black_level: int,
    seed: int,
    opening: tuple[str, ...],
    nodes: int,
) -> CalibrationGame:
    board = chess.Board()
    for opening_move in opening:
        board.push_uci(opening_move)
    played: list[str] = []
    for _ in range(MAX_GAME_PLIES):
        if board.outcome(claim_draw=True) is not None:
            break
        level = white_level if board.turn == chess.WHITE else black_level
        position = Position(
            fen=board.fen(),
            side_to_move="w" if board.turn else "b",
            canonical_id=board.board_fen(),
        )
        decision = await provider.choose_move(
            position,
            OpponentContext(
                strength_setting=0,
                bot_level=level,
                seed=seed,
                ply=board.ply(),
                nodes=nodes,
            ),
        )
        move = chess.Move.from_uci(decision.uci_move)
        if move not in board.legal_moves:
            raise chess.engine.EngineError(f"Bot level {level} returned illegal move {move}")
        board.push(move)
        played.append(move.uci())
    outcome = board.outcome(claim_draw=True)
    return CalibrationGame(
        white_level=white_level,
        black_level=black_level,
        seed=seed,
        opening=opening,
        moves=tuple(played),
        result=outcome.result() if outcome else "1/2-1/2",
    )


async def calibration_pass(config: StockfishConfig, nodes: int) -> tuple[CalibrationGame, ...]:
    manager = EnginePoolManager(config, interactive_concurrency=1, batch_concurrency=1)
    provider = StockfishOpponentProvider(config, pool_manager=manager)
    games: list[CalibrationGame] = []
    try:
        for lower, higher in zip(BOT_LEVELS, BOT_LEVELS[1:], strict=False):
            for opening, seed in zip(CALIBRATION_OPENINGS, CALIBRATION_SEEDS, strict=True):
                for higher_is_white in (True, False):
                    white = higher.level if higher_is_white else lower.level
                    black = lower.level if higher_is_white else higher.level
                    games.append(
                        await play_calibration_game(provider, white, black, seed, opening, nodes)
                    )
    finally:
        await manager.close()
    return tuple(games)


async def calibrate_command(binary_path: str, nodes: int = DEFAULT_BOT_NODES) -> int:
    if nodes < 1:
        raise ValueError("Calibration nodes must be positive")
    config = StockfishConfig(binary_path=binary_path, threads=1, hash_size=16)
    _, engine = await chess.engine.popen_uci(binary_path)
    try:
        version = engine.id.get("name", "unknown Stockfish version")
    finally:
        await engine.quit()
    print(f"Stockfish version: {version}", flush=True)
    print(f"Policy: session-seeded MultiPV; nodes={nodes}; threads=1; hash=16 MiB", flush=True)
    print("Levels are calibrated relative to each other, not Elo-anchored.", flush=True)
    print("Native unseedable reduced-strength bestmove RNG is not used.", flush=True)
    print("Draws: chess rules/claimable draws or 200 full moves after the opening.", flush=True)
    print("Two independent passes; 20 games per pair per pass, both colours.", flush=True)
    first = await calibration_pass(config, nodes)
    second = await calibration_pass(config, nodes)
    deterministic = first == second
    passed = deterministic
    for index, (lower, higher) in enumerate(zip(BOT_LEVELS, BOT_LEVELS[1:], strict=False)):
        games = first[index * 20 : (index + 1) * 20]
        repeat = second[index * 20 : (index + 1) * 20]
        score = sum(game.higher_score() for game in games)
        repeat_score = sum(game.higher_score() for game in repeat)
        enough_games = len(games) == len(repeat) == 20
        pair_passed = enough_games and score >= 12 and games == repeat
        passed = passed and pair_passed
        print(
            f"Level {higher.level} vs {lower.level}: {score:g}/20 ({score / 20:.0%}); "
            f"repeat {repeat_score:g}/20; "
            f"identical={'yes' if games == repeat else 'NO'}; "
            f"{'PASS' if pair_passed else 'FAIL'}",
            flush=True,
        )
    for label, games in (("first", first), ("repeat", second)):
        digest = hashlib.sha256(repr(games).encode()).hexdigest()
        print(f"{label} complete-game digest: {digest}", flush=True)
    print(f"Calibration {'PASS' if passed else 'FAIL'}", flush=True)
    return 0 if passed else 1

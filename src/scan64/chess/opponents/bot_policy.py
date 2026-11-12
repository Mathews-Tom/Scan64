"""Session-seeded weakening, shared by live play and calibration.

Stockfish does not expose a seed for its reduced-strength bestmove RNG. All
eight levels therefore use full-strength MultiPV scores and the same seeded
selection policy, with a per-level error budget. These relative practice
levels have no human Elo estimate.
"""

import hashlib
import random

import chess
import chess.engine

from scan64.chess.opponents.bots import get_bot_level
from scan64.chess.opponents.protocols import OpponentContext

DEFAULT_BOT_NODES = 1_000


def error_budget(level: int) -> int:
    return get_bot_level(level).error_budget_cp


def sample_candidate(
    board: chess.Board, candidates: list[chess.engine.InfoDict], context: OpponentContext
) -> chess.engine.PlayResult:
    assert context.bot_level is not None
    scored: dict[chess.Move, tuple[int, chess.engine.InfoDict]] = {}
    for candidate in candidates:
        pv = candidate.get("pv", [])
        score = candidate.get("score")
        if not pv or score is None or pv[0] not in board.legal_moves:
            continue
        cp = score.pov(board.turn).score(mate_score=100_000)
        if cp is not None:
            scored[pv[0]] = (cp, candidate)
    if not scored:
        raise chess.engine.EngineError("Stockfish returned no scored legal bot candidates")
    ranked = sorted(scored.items(), key=lambda item: (-item[1][0], item[0].uci()))
    best_score = ranked[0][1][0]
    budget = error_budget(context.bot_level)
    eligible = [item for item in ranked if best_score - item[1][0] <= budget]
    seed_material = f"{context.seed}:{context.ply}:{board.fen()}".encode()
    rng = random.Random(int.from_bytes(hashlib.sha256(seed_material).digest(), "big"))
    target_loss = rng.uniform(0, budget)
    move, (_, info) = min(
        eligible,
        key=lambda item: (abs(best_score - item[1][0] - target_loss), item[0].uci()),
    )
    return chess.engine.PlayResult(move, None, info=info)


async def choose_ladder_move(
    engine: chess.engine.UciProtocol,
    board: chess.Board,
    context: OpponentContext,
    game: object,
) -> chess.engine.PlayResult:
    assert context.bot_level is not None
    get_bot_level(context.bot_level)
    nodes = context.nodes if context.nodes is not None else DEFAULT_BOT_NODES
    if nodes < 1:
        raise ValueError("Bot node budget must be positive")
    await engine.configure(
        {
            "Threads": 1,
            "Skill Level": 20,
            "UCI_LimitStrength": False,
            "UCI_Elo": 1320,
        }
    )
    # Search every legal move so each level has the same candidate population;
    # only its explicit error budget changes the seeded selection.
    multipv = board.legal_moves.count()
    candidates = await engine.analyse(
        board,
        chess.engine.Limit(nodes=nodes),
        multipv=multipv,
        game=game,
        info=chess.engine.INFO_SCORE | chess.engine.INFO_PV,
    )
    if not isinstance(candidates, list):
        candidates = [candidates]
    return sample_candidate(board, candidates, context)

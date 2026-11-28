"""Print the root-cause themes and turning points of the slow-loss games (#400).

Usage:
    uv run python -m tests.helpers.themes.hand_check [--engine PATH] [--depth N]

Games: ``benchmarks/factor_calibration/games.json`` (20 public Lichess games
one side lost slowly, Lichess server evals, CC0). The loser is the learner.
For each game the script prints the turning point with the loser's scores
around it, and for each candidate mistake of the game (the loser's moves in
the decline that lost the most) the themes that held, with the board at the
position before the mistake. docs/narrator-pipeline.md section 18 reports the
hand check of this output. With ``--engine`` (a UCI engine, development
machine only) it also prints, at each turning point, the engine's best move and
the scores of the best and the played move: a turning point is a real error
when a better move kept the position level.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import chess
import chess.engine

from scan64.explanations.themes import (
    game_theme_facts,
    theme_phrase,
    trace_from_scores,
    turning_point_phrase,
)

GAMES = Path(__file__).parents[3] / "benchmarks" / "factor_calibration" / "games.json"


def _engine_check(
    engine: chess.engine.SimpleEngine, board: chess.Board, played: chess.Move, depth: int
) -> str:
    limit = chess.engine.Limit(depth=depth)
    best = engine.analyse(board, limit)
    after = board.copy()
    after.push(played)
    reply = engine.analyse(after, limit)
    best_cp = best["score"].pov(board.turn).score(mate_score=10_000)
    played_cp = reply["score"].pov(board.turn).score(mate_score=10_000)
    move = best["pv"][0]
    return f"engine best {board.san(move)} {best_cp:+d} cp, played {played_cp:+d} cp"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine")
    parser.add_argument("--depth", type=int, default=20)
    args = parser.parse_args()
    engine = chess.engine.SimpleEngine.popen_uci(args.engine) if args.engine else None
    games = json.loads(GAMES.read_text(encoding="utf-8"))["games"]
    for game in games:
        loser = chess.WHITE if game["winner"] == "black" else chess.BLACK
        sign = 1 if loser == chess.WHITE else -1
        # Lichess gives one eval per ply, after the move: position i + 1.
        trace = trace_from_scores(None, game["moves"], [None, *game["lichess_white_cp"]])
        print(f"== {game['url']} loser {'white' if loser else 'black'} {game['decline']}")
        printed = False
        for index in game["candidates"]:
            before, played = trace.boards[index], trace.moves[index]
            facts = game_theme_facts(trace, before, played)
            assert facts is not None
            point = facts.turning_point
            if point is not None and not printed:
                printed = True
                around = range(max(0, point.ply - 5), min(len(trace.scores), point.ply + 8))
                scores = [
                    (i, sign * s if (s := trace.scores[i]) is not None else None) for i in around
                ]
                print(f"  turning point: {turning_point_phrase(point)}")
                print(f"  loser's scores by position: {scores}")
                board = trace.boards[point.ply - 1]
                print(f"  {board.fen()}")
                if engine is not None:
                    move = trace.moves[point.ply - 1]
                    print(f"  {_engine_check(engine, board, move, args.depth)}")
            print(
                f"  mistake {before.fullmove_number} {before.san(played)}: trend "
                f"{facts.evaluation_trend_cp}"
            )
            for theme in facts.themes:
                print(f"    {theme.key}: {theme_phrase(theme)}; measured {theme.measured}")
                print(f"    {before.fen()}")
    if engine is not None:
        engine.quit()


if __name__ == "__main__":
    main()

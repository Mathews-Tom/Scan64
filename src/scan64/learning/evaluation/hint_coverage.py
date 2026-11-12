"""Offline candidate coverage: uv run python -m scan64.learning.evaluation.hint_coverage."""

import argparse
import json
from collections import Counter
from pathlib import Path

import chess

from scan64.chess.analysis.models import EngineAnalysis
from scan64.learning.insights import HintCandidate, position_insight

CORPUS = Path(__file__).resolve().parents[4] / "tests/fixtures/m48/recorded.json"
TIERS = ("tactical", "principle", "fallback")


def candidate_tier(candidate: HintCandidate) -> str:
    """Mutually exclusive shares; tactical evidence is never demoted."""
    if any(reason.tier == "tactical" for reason in candidate.reasons):
        return "tactical"
    return "principle" if candidate.reasons else "fallback"


def measure(path: Path = CORPUS) -> tuple[int, int, Counter[str]]:
    corpus = json.loads(path.read_text())
    counts: Counter[str] = Counter({tier: 0 for tier in TIERS})
    positions = 0
    games = corpus["games"][: corpus["provenance"]["game_count"]]
    for game in games:
        board = chess.Board()
        for index, fast in enumerate(game["fast"]):
            if not board.is_game_over():
                source = game.get("sources", {}).get(str(index))
                raw = (
                    source["raw_result"]
                    if source
                    else game.get("deep_before", {}).get(str(index))
                    or game["focused"].get(str(index - 1))
                    or fast
                )
                insight = position_insight(board, EngineAnalysis(raw_result=raw))
                positions += 1
                counts.update(candidate_tier(candidate) for candidate in insight.candidates)
            if index < len(game["moves"]):
                board.push_san(game["moves"][index])
    return len(games), positions, counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    args = parser.parse_args()
    games, positions, counts = measure(args.corpus)
    total = counts.total()
    print(f"Recorded games: {games}; positions: {positions}; sound candidates: {total}")
    print("Candidate tier | Count | Share")
    print("--- | ---: | ---:")
    for tier in TIERS:
        share = counts[tier] / total if total else 0.0
        print(f"{tier} | {counts[tier]} | {share:.2%}")
    print("Tactical takes precedence; principle means principle-only. No coverage threshold.")


if __name__ == "__main__":
    main()

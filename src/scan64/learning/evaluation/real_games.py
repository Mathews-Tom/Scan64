"""Offline, engine-derived diagnosis measurement (not independent coach review).

Run: uv run python -m scan64.learning.evaluation.real_games [--check]
Support is the number of emitted diagnoses, TP + FP. Reference labels use only
recorded million-node source/reply PVs, never fast signals or composer evidence.
The independent oracle accounts for exchanges locally on the capture square.
Every registered detector is arbitrated in isolation so a stronger competing
cause cannot hide false positives in this report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chess

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.evaluation.deep_reference import reference_labels
from scan64.learning.evidence.composer import compose_candidate_evidence
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry

CORPUS = Path(__file__).resolve().parents[4] / "tests/fixtures/m48/recorded.json"


class RecordedAdapter:
    """Exact FEN/config lookup for the real pipeline; missing calls fail closed."""

    def __init__(self, game: dict[str, Any]) -> None:
        self.game = game
        board = chess.Board()
        self.fens = [board.fen()]
        for san in game["moves"]:
            board.push_san(san)
            self.fens.append(board.fen())

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        if nodes == 10_000 and multipv == 1:
            return EngineAnalysis(config={}, raw_result=self.game["fast"][self.fens.index(fen)])
        for index, raw in self.game["focused"].items():
            if fen == self.fens[int(index) + 1] and multipv == 4:
                return EngineAnalysis(config={}, raw_result=raw)
        for index, source in self.game["sources"].items():
            if fen == self.fens[int(index)] and multipv <= source["config"]["multipv"]:
                return EngineAnalysis(
                    config=source["config"], raw_result=source["raw_result"][:multipv]
                )
        raise ValueError(f"Unrecorded analysis request: {fen}, nodes={nodes}, multipv={multipv}")


@dataclass
class Score:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def support(self) -> int:
        return self.tp + self.fp

    @property
    def precision(self) -> float:
        return self.tp / self.support if self.support else 0.0

    @property
    def trusted(self) -> bool:
        return self.precision >= 0.80 and self.support >= 10


def check_scores(scores: dict[str, Score], trusted: frozenset[str]) -> bool:
    return all(code in scores and scores[code].trusted for code in trusted)


async def measure(path: Path = CORPUS) -> dict[str, Score]:
    corpus = json.loads(path.read_text())
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    scores = {code: Score() for code in SEED_CODES}
    # The synthetic reference game is exercised separately, not part of real-game precision.
    for game in corpus["games"][: corpus["provenance"]["game_count"]]:
        adapter = RecordedAdapter(game)
        candidates = await FastPassOrchestrator(
            adapter, FastPassConfig(nodes=10_000)
        ).run_fast_pass(game["moves"])
        for candidate in candidates:
            before = chess.Board(adapter.fens[candidate.move_index])
            after = chess.Board(candidate.fen)
            focused = EngineAnalysis(
                config={}, raw_result=game["focused"][str(candidate.move_index)]
            )
            evidence = compose_candidate_evidence(
                before_board=before,
                after_board=after,
                history_san=game["moves"][: candidate.move_index + 1],
                initial_fen=None,
                position_id="recorded",
                fast_analysis=candidate.before_analysis,
                reply_analysis=candidate.after_analysis,
                focused_analysis=focused,
                played_move=game["moves"][candidate.move_index],
                swing_cp=candidate.swing_cp,
            )
            labels = reference_labels(
                before, after, game["deep_before"][str(candidate.move_index)], focused.raw_result
            )
            opportunity = LearningOpportunity(
                opportunity_id="recorded",
                position_id="recorded",
                player_id="recorded",
                played_move=game["moves"][candidate.move_index],
                engine_eval_before=0,
                engine_eval_after=-candidate.swing_cp / 100,
            )
            for code, score in scores.items():
                detector = registry.get(kind=PluginKind.PATTERN_DETECTOR, name=code)
                assert isinstance(detector, PatternDetector)
                detected = await detector.detect(
                    opportunity, evidence, PlayerContext(player_id="recorded")
                )
                predicted = arbitrate_diagnoses(detected, evidence, trusted_only=False) is not None
                if predicted:
                    if code in labels:
                        score.tp += 1
                    else:
                        score.fp += 1
                elif code in labels:
                    score.fn += 1
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    scores = asyncio.run(measure())
    print("skill code                              precision support TP FP FN trusted")
    for code, score in scores.items():
        print(
            f"{code:40} {score.precision:.3f} {score.support:7} "
            f"{score.tp:3} {score.fp:3} {score.fn:3} {'yes' if score.trusted else 'no'}"
        )
    if args.check:
        from scan64.learning.diagnosis.trust import TRUSTED_CODES

        if not check_scores(scores, TRUSTED_CODES):
            raise SystemExit("Trusted-code precision/support regression (requires >=0.80 and >=10)")


if __name__ == "__main__":
    main()

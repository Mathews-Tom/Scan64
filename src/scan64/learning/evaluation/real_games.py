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
from scan64.learning.evaluation.deep_reference import m79_reference_labels, reference_labels
from scan64.learning.evidence.composer import allowed_mate_line, compose_candidate_evidence
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry

CORPUS = Path(__file__).resolve().parents[4] / "tests/fixtures/m48/recorded.json"
FRESH_CORPUS = CORPUS.with_name("fresh80_recorded.json")
ALLOWED_MATE = "threat_processing.allowed_mate"
MISSED_CAPTURE = "threat_processing.missed_capture"
M79_CORPUS = CORPUS.with_name("m79_fresh80_recorded.json")
M79_PREREGISTRATION = "4afbe47d09138606ecd7d3dbabc4659e1ef2c6ed"


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
    if path == FRESH_CORPUS and (
        corpus["provenance"]["game_count"] != 80
        or len(corpus["games"]) != 80
        or corpus["provenance"].get("preregistration_commit") != "c4490fa"
    ):
        raise ValueError("Fresh holdout must contain exactly the preregistered next80")
    if path == M79_CORPUS and (
        corpus["provenance"]["game_count"] != 80
        or len(corpus["games"]) != 80
        or corpus["provenance"].get("preregistration_commit") != M79_PREREGISTRATION
        or corpus["provenance"].get("selection")
        != "Next 80 qualifying games after original40 and M78 fresh80; ordinal 121..200."
    ):
        raise ValueError("M79 holdout must contain exactly preregistered ordinals 121..200")
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    scores = {code: Score() for code in SEED_CODES}
    # The synthetic reference game is exercised separately, not part of real-game precision.
    for game in corpus["games"][: corpus["provenance"]["game_count"]]:
        adapter = RecordedAdapter(game)
        fast_pass = await FastPassOrchestrator(adapter, FastPassConfig(nodes=10_000)).run_fast_pass(
            game["moves"]
        )
        for candidate in fast_pass.candidates:
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
                mate_source_analysis=EngineAnalysis(
                    config={"nodes": 1_000_000, "multipv": 4},
                    raw_result=game["deep_before"][str(candidate.move_index)],
                ),
                focused_analysis=focused,
                played_move=game["moves"][candidate.move_index],
                swing_cp=candidate.swing_cp,
            )
            labeller = m79_reference_labels if path == M79_CORPUS else reference_labels
            labels = labeller(
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
                if any(allowed_mate_line(item.payload) is not None for item in evidence):
                    if (
                        code
                        in {
                            "board_awareness.hanging_piece",
                            "threat_processing.missed_direct_threat",
                            "threat_processing.missed_capture",
                        }
                        and predicted
                    ):
                        raise AssertionError("Confirmed mate was emitted as a material diagnosis")
                if predicted:
                    if code in labels:
                        score.tp += 1
                    else:
                        score.fp += 1
                        if code == ALLOWED_MATE:
                            print(
                                json.dumps(
                                    {
                                        "allowed_mate_false_positive": game["id"],
                                        "move_index": candidate.move_index,
                                        "before_fen": before.fen(),
                                        "deep_before": game["deep_before"][
                                            str(candidate.move_index)
                                        ][0],
                                        "deep_reply": focused.raw_result[0],
                                        "fast_reply": candidate.after_analysis.raw_result[0],
                                    },
                                    sort_keys=True,
                                )
                            )
                elif code in labels:
                    score.fn += 1
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    scores = asyncio.run(measure())
    fresh_scores = asyncio.run(measure(FRESH_CORPUS))
    m79_scores = asyncio.run(measure(M79_CORPUS))
    for name, measured in (
        ("original40 development post-fix", scores),
        ("M78 fresh80 development for missed capture", fresh_scores),
        ("M79 fresh80 missed-capture holdout", m79_scores),
    ):
        print(name)
        print("skill code                              precision support TP FP FN trusted")
        for code, score in measured.items():
            print(
                f"{code:40} {score.precision:.3f} {score.support:7} "
                f"{score.tp:3} {score.fp:3} {score.fn:3} {'yes' if score.trusted else 'no'}"
            )
    if args.check:
        from scan64.learning.diagnosis.trust import TRUSTED_CODES

        if not check_scores(scores, TRUSTED_CODES - {ALLOWED_MATE, MISSED_CAPTURE}):
            raise SystemExit("Original trusted-code precision/support regression")
        if ALLOWED_MATE in TRUSTED_CODES and not fresh_scores[ALLOWED_MATE].trusted:
            raise SystemExit("Fresh80 allowed-mate precision/support regression")
        if MISSED_CAPTURE in TRUSTED_CODES and not m79_scores[MISSED_CAPTURE].trusted:
            raise SystemExit("M79 fresh80 missed-capture precision/support regression")
        print("PASS: declared trust gates and confirmed-mate material suppression")


if __name__ == "__main__":
    main()

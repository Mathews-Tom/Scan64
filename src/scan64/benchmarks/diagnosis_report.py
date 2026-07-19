import argparse
import asyncio
import json
from pathlib import Path

from scan64.learning.diagnosis.detectors.board_awareness import HangingPieceDetector
from scan64.learning.diagnosis.detectors.calculation import StoppedCalculationEarlyDetector
from scan64.learning.diagnosis.detectors.opening import DelayedDevelopmentDetector
from scan64.learning.diagnosis.detectors.positional import KingSafetyNeglectDetector
from scan64.learning.diagnosis.detectors.tactics import (
    KnightForkDetector,
    OverloadedDefenderDetector,
    PinDetector,
)
from scan64.learning.diagnosis.detectors.threat_processing import (
    MissedCaptureDetector,
    MissedCheckDetector,
    MissedDirectThreatDetector,
)
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.interfaces import PatternDetector

SEED_CODES = [
    "board_awareness.hanging_piece",
    "threat_processing.missed_check",
    "threat_processing.missed_capture",
    "threat_processing.missed_direct_threat",
    "tactics.fork.knight",
    "tactics.pin",
    "tactics.overloaded_defender",
    "calculation.stopped_too_early",
    "opening.delayed_development",
    "positional.king_safety_neglect",
]


async def run_report(fixtures_dir: Path) -> None:
    corpus_path = fixtures_dir / "golden_corpus.json"
    with open(corpus_path) as f:
        fixtures = json.load(f)

    detectors: list[PatternDetector] = [
        HangingPieceDetector(),
        MissedCheckDetector(),
        MissedCaptureDetector(),
        MissedDirectThreatDetector(),
        KnightForkDetector(),
        PinDetector(),
        OverloadedDefenderDetector(),
        StoppedCalculationEarlyDetector(),
        DelayedDevelopmentDetector(),
        KingSafetyNeglectDetector(),
    ]

    # Initialize stats
    stats: dict[str, dict[str, int]] = {code: {"tp": 0, "fp": 0, "fn": 0} for code in SEED_CODES}

    ctx = PlayerContext(player_id="bench_player")

    for fixture in fixtures:
        expected = fixture.get("expected_label")

        # Prepare opportunity and evidence if available
        mock_evidence = fixture.get("mock_evidence", {})
        opp_data = mock_evidence.get("opportunity", {})
        ev_list_data = mock_evidence.get("evidence_list", [])

        opp = LearningOpportunity(
            opportunity_id=fixture["id"],
            position_id=fixture["id"],
            player_id="bench_player",
            played_move="e4",
            engine_eval_before=opp_data.get("engine_eval_before", 0.0),
            engine_eval_after=opp_data.get("engine_eval_after", 0.0),
        )

        ev_list = [
            Evidence(
                evidence_id=f"ev_{i}",
                kind=ev["kind"],
                position_id=fixture["id"],
                engine_analysis_id="ea_1",
                claim="",
                payload=ev.get("payload", {}),
            )
            for i, ev in enumerate(ev_list_data)
        ]

        predicted = set()
        for detector in detectors:
            candidates = await detector.detect(opp, ev_list, ctx)
            for c in candidates:
                predicted.add(c.skill_id)

        for code in SEED_CODES:
            is_expected = expected == code
            is_predicted = code in predicted

            if is_expected and is_predicted:
                stats[code]["tp"] += 1
            elif not is_expected and is_predicted:
                stats[code]["fp"] += 1
            elif is_expected and not is_predicted:
                stats[code]["fn"] += 1

    print("Diagnosis Precision/Recall Report")
    print("=" * 60)
    print(f"{'Skill Code':<40} | {'Precision':<10} | {'Recall':<10}")
    print("-" * 60)
    for code in SEED_CODES:
        tp = stats[code]["tp"]
        fp = stats[code]["fp"]
        fn = stats[code]["fn"]

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

        print(f"{code:<40} | {precision:<10.2f} | {recall:<10.2f}")

    # Check non-zero requirement
    for code in SEED_CODES:
        tp = stats[code]["tp"]
        if tp == 0:
            print(f"\nWARNING: {code} has 0 true positives!")
            exit(1)

    print("\nAll 10 seed detectors achieved non-zero precision and recall.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, type=str)
    args = parser.parse_args()

    asyncio.run(run_report(Path(args.fixtures)))


if __name__ == "__main__":
    main()

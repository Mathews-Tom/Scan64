"""Replay stored narrator claims through the current validator, offline (#431).

Reads the committed raw records (``results/runs_*.jsonl``), rebuilds each
claim's grounding context from the run's positions file and validates the
stored claim text again. No model is called. A run whose recorded
``positions_sha256`` differs from the current positions file cannot be rebuilt
and is skipped, and so is an Analysis Board run (a ``board-lines`` positions
file, #449): the replay covers the lesson pipeline.

The result compares each claim's stored ``rejection_reason`` with today's:
``newly_rejected`` (accepted then, rejected now) by reason code, and
``newly_accepted`` (rejected then, accepted now) by the stored code. On each
rebuilt position it also checks the #431 example ("You are up a piece.") and
its wrong-side form against the position's material facts.

    python -m benchmarks.narrator.replay_grounding [--examples N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from chess_lesson_spec import ExplanationClaim

from benchmarks.narrator.build_board_positions import BOARD_KIND
from benchmarks.narrator.run_benchmark import (
    ContextBuilder,
    _claim_reason,
    _resolve,
    load_positions,
    positions_kind,
)
from scan64.explanations.validator import GroundedExplanationContext

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "benchmarks" / "narrator" / "results"


@dataclass
class Replay:
    runs_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    accepted_before: int = 0
    rejected_before: int = 0
    newly_rejected: Counter[str] = field(default_factory=Counter)
    newly_accepted: Counter[str] = field(default_factory=Counter)
    examples: list[tuple[str, str, str, str]] = field(default_factory=list)
    accepted_examples: list[tuple[str, str, str, str]] = field(default_factory=list)
    # Probes per rebuilt position: (claim, expected rejected, rejected).
    probes: list[tuple[str, str, bool, bool]] = field(default_factory=list)


ISSUE_EXAMPLE = "You are up a piece."
WRONG_SIDE = "Your opponent is up a piece."


def _probe(out: Replay, position_id: str, context: GroundedExplanationContext) -> None:
    """The #431 example and its wrong side, on the first evidence item."""
    assert context.slots is not None
    evidence_id = context.evidence[0].evidence_id
    balances = context.slots.claim_facts(evidence_id).balances
    for text, sign in ((ISSUE_EXAMPLE, 1), (WRONG_SIDE, -1)):
        claim = ExplanationClaim(text=text, evidence_ref=evidence_id, disclosure_level=1)
        reason, _ = _claim_reason(claim, context)
        expected = not any(sign * balance in (3, 4) for balance in balances)
        out.probes.append((position_id, text, expected, reason is not None))


def _records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                yield json.loads(line)


def replay(results: Path = RESULTS, root: Path = ROOT) -> Replay:
    """Validate every stored claim of every rebuildable slot-path run again."""
    out = Replay()
    for path in sorted(results.glob("runs_*.jsonl")):
        records = _records(path)
        header = next(records)
        positions_file = root / header["positions_file"]
        if (
            not positions_file.exists()
            or hashlib.sha256(positions_file.read_bytes()).hexdigest() != header["positions_sha256"]
            or positions_kind(positions_file) == BOARD_KIND
        ):
            out.skipped_files.append(path.name)
            continue
        builder = cast(ContextBuilder, _resolve(header["pipeline"]["grounding_context"]))
        positions = {p.position_id: p for p in load_positions(positions_file)}
        contexts: dict[str, GroundedExplanationContext] = {}
        used = False
        for record in records:
            if record.get("record") != "run" or "slots_used" not in record:
                continue  # not a run, or a run before the slot path (#386)
            used = True
            position_id = record["position_id"]
            if position_id not in contexts:
                p = positions[position_id]
                contexts[position_id] = builder(p.diagnosis, list(p.evidence), p.fen)
                if contexts[position_id].slots is not None:
                    _probe(out, position_id, contexts[position_id])
            for stored in record.get("claims") or ():
                claim = ExplanationClaim(
                    text=stored["text"],
                    evidence_ref=stored["evidence_ref"],
                    line=tuple(stored.get("line") or ()),
                    certainty=stored.get("certainty", "observed"),
                    disclosure_level=stored.get("disclosure_level", 1),
                )
                reason, _ = _claim_reason(claim, contexts[position_id])
                before = stored.get("rejection_reason")
                if before is None:
                    out.accepted_before += 1
                    if reason is not None:
                        out.newly_rejected[reason] += 1
                        out.examples.append((path.name, position_id, reason, stored["text"]))
                else:
                    out.rejected_before += 1
                    if reason is None:
                        out.newly_accepted[before] += 1
                        out.accepted_examples.append(
                            (path.name, position_id, before, stored["text"])
                        )
        (out.runs_files if used else out.skipped_files).append(path.name)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--examples", type=int, default=20, help="newly rejected claims shown")
    args = parser.parse_args()
    result = replay()
    print(f"replayed files: {', '.join(result.runs_files)}")
    print(
        "skipped files (positions changed, Analysis Board, or no slot-path run): "
        f"{len(result.skipped_files)}"
    )
    print(f"claims accepted before: {result.accepted_before}")
    print(f"newly rejected: {sum(result.newly_rejected.values())} {dict(result.newly_rejected)}")
    print(f"claims rejected before: {result.rejected_before}")
    print(f"newly accepted: {sum(result.newly_accepted.values())} {dict(result.newly_accepted)}")
    for text in (ISSUE_EXAMPLE, WRONG_SIDE):
        probes = [p for p in result.probes if p[1] == text]
        false = [p for p in probes if p[2]]
        print(
            f"probe {text!r}: rejected on {sum(p[3] for p in false)} of {len(false)} positions "
            f"where no fact supports it; accepted on {sum(not p[3] for p in probes if not p[2])} "
            f"of {len(probes) - len(false)} where one does"
        )
    print("newly rejected claims:")
    for name, position_id, reason, text in result.examples[: args.examples]:
        print(f"  {reason}: {text!r} ({name}, {position_id})")
    print("newly accepted claims (stored reason):")
    for name, position_id, reason, text in result.accepted_examples[: args.examples]:
        print(f"  {reason}: {text!r} ({name}, {position_id})")


if __name__ == "__main__":
    main()

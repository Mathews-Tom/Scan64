"""Coverage counts the key engine facts an accepted narration states (#369)."""

import json
from pathlib import Path

import pytest

from benchmarks.narrator.run_benchmark import coverage, key_facts

POSITIONS = Path(__file__).resolve().parents[2] / "benchmarks/narrator/positions.json"


def _payload(position_id: str) -> dict[str, object]:
    positions = json.loads(POSITIONS.read_text(encoding="utf-8"))["positions"]
    item = next(p for p in positions if p["position_id"] == position_id)
    return dict(item["evidence"][0]["payload"])


def _claims(*texts: str, line: tuple[str, ...] = ()) -> list[dict[str, object]]:
    return [{"text": text, "line": list(line)} for text in texts]


# Black played b5 and missed b6c5 (bxc5), which wins the queen.
MISSED_CAPTURE = "recorded:https://lichess.org/6x5nq6qd:25"


def test_key_facts_of_a_missed_capture() -> None:
    facts = key_facts(_payload(MISSED_CAPTURE))
    assert facts.played == ("b5", "b6b5")
    assert facts.key_move == ("b6c5", "bxc5")
    assert facts.result == ("queen",)


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        # Passes the validator but states none of the three facts.
        (_claims("the fast-pass principal variation starts with a legal capture"), 0.0),
        (_claims("The move b5 was played instead of the best move b6c5."), 2 / 3),
        (
            _claims(
                "The move b5 was played instead of the best move b6c5.",
                "The move b6c5 results in the capture of the Queen on c5.",
            ),
            1.0,
        ),
        # SAN counts, and a move given only in the claim's line counts.
        (_claims("bxc5 takes the queen."), 2 / 3),
        (_claims("You played b5.", line=("b6c5",)), 2 / 3),
        # A longer move that starts with the played square does not count as b5.
        (_claims("The line b5c4 follows."), 0.0),
    ],
)
def test_coverage_counts_stated_facts(claims: list[dict[str, object]], expected: float) -> None:
    assert coverage(claims, key_facts(_payload(MISSED_CAPTURE))) == pytest.approx(expected)


def test_every_benchmark_position_has_three_key_facts() -> None:
    positions = json.loads(POSITIONS.read_text(encoding="utf-8"))["positions"]
    for item in positions:
        facts = key_facts(item["evidence"][0]["payload"])
        assert facts.played and facts.key_move and facts.result, item["position_id"]

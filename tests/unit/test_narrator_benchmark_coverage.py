"""Coverage counts the key engine facts an accepted narration states (#369)."""

import json
from pathlib import Path
from typing import Any

import chess
import pytest
from chess_lesson_spec import ExplanationClaim

from benchmarks.narrator.run_benchmark import (
    _claim_record,
    coverage,
    key_facts,
    load_config,
    load_positions,
    run_coverage,
    slot_coverage,
)
from scan64.explanations.assembly import grounding_context_for

ROOT = Path(__file__).resolve().parents[2]
POSITIONS = ROOT / "benchmarks/narrator/positions.json"


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


# --- Slot pipeline (#386) ----------------------------------------------------------


def _position(position_id: str) -> Any:
    config = load_config(ROOT / "benchmarks/narrator/narrator.toml")
    return next(p for p in load_positions(config.positions_file) if p.position_id == position_id)


def _slot_records(text: str) -> list[dict[str, Any]]:
    position = _position(MISSED_CAPTURE)
    context = grounding_context_for(position.diagnosis, list(position.evidence), position.fen)
    claim = ExplanationClaim(
        text=text, evidence_ref=position.diagnosis.evidence_refs[0], disclosure_level=1
    )
    record = _claim_record(claim, context, chess.Board(context.fen))
    slots: list[dict[str, Any]] = record["slots"]
    return slots


def test_claim_records_keep_the_slots_and_their_rendered_values() -> None:
    slots = _slot_records("You played {played}, but {capture:best}. {nope} too.")
    assert [slot["name"] for slot in slots] == ["played", "capture:best", "nope"]
    assert slots[0]["value"] == "b5" and slots[0]["moves"] == ["b6b5"]
    assert slots[1]["value"] == "bxc5 captures the white queen on c5"
    assert slots[2]["value"] is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("You played {played}, but {capture:best}.", 1.0),
        ("You played {played}; {best} was stronger.", 2 / 3),
        ("{mistake}: slow down.", 0.0),
        # An unlisted slot states nothing; prose outside slots counts for nothing.
        ("{nope} was the queen move bxc5.", 0.0),
    ],
)
def test_slot_coverage_counts_the_facts_the_slots_state(text: str, expected: float) -> None:
    facts = key_facts(_payload(MISSED_CAPTURE))
    assert slot_coverage(_slot_records(text), facts) == pytest.approx(expected)


def test_records_without_slots_keep_the_claim_text_count() -> None:
    facts = key_facts(_payload(MISSED_CAPTURE))
    claims = _claims("bxc5 takes the queen.")
    assert run_coverage({"claims": claims}, facts) == coverage(claims, facts)
    assert run_coverage({"claims": claims, "slots_used": []}, facts) == 0.0

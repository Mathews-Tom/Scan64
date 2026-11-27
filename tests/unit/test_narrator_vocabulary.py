"""Vocabulary rule (#408): a chess word outside slots only when the facts contain it.

Notation (digits, ``#``, ``O-O``) stays rejected everywhere; a chess word passes
when the position's facts name it: the mistake class, the evidence tactic, a
slot name of the cited evidence item, or a passed factor change.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from chess_lesson_spec import Diagnosis, ExplanationClaim

from scan64.explanations.assembly import grounding_context_for
from scan64.explanations.slots import (
    NOTATION,
    SlotCatalogue,
    chess_words_outside_slots,
    vocabulary,
)
from scan64.explanations.validator import (
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    validate_generated_explanation,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import GeneratedExplanation
from tests.unit.test_claim_text_grounding import ADVERSARIAL, CORRECT

POSITIONS: dict[str, dict[str, Any]] = {
    item["position_id"]: item
    for item in json.loads(
        (Path(__file__).parents[2] / "benchmarks/narrator/positions.json").read_text("utf-8")
    )["positions"]
}
MISSED_THREAT = "recorded:https://lichess.org/6x5nq6qd:57"  # king safety change passed
NO_THREAT = "recorded:https://lichess.org/a301qp92:21"  # missed capture, no threat fact
NO_KING = "recorded:https://lichess.org/fiv37vzi:42"  # missed direct threat, no king fact


def _context(position_id: str) -> tuple[GroundedExplanationContext, str]:
    item = POSITIONS[position_id]
    context = grounding_context_for(
        Diagnosis(**item["diagnosis"]), [Evidence(**e) for e in item["evidence"]], item["fen"]
    )
    return context, context.evidence[0].evidence_id


def _validate(text: str, position_id: str) -> str:
    context, evidence_id = _context(position_id)
    claim = ExplanationClaim(text=text, evidence_ref=evidence_id, line=(), disclosure_level=1)
    return validate_generated_explanation(GeneratedExplanation(claims=(claim,)), context).text


def _reason(text: str, position_id: str) -> str:
    with pytest.raises(GroundedExplanationValidationError) as raised:
        _validate(text, position_id)
    return raised.value.reason


def test_the_issue_example_passes_on_a_missed_direct_threat() -> None:
    text = "Your move, {played}, missed a direct threat, as seen in line {line:L2}."
    rendered = _validate(text, MISSED_THREAT)
    assert rendered.startswith("Your move, ") and "missed a direct threat" in rendered


def test_a_square_outside_a_slot_still_fails() -> None:
    text = "After {played}, your piece on d2 became a {mistake}."
    assert _reason(text, MISSED_THREAT) == "chess_token_outside_slot"


def test_threat_fails_on_a_position_with_no_threat_fact() -> None:
    context, evidence_id = _context(NO_THREAT)
    assert context.slots is not None and "threat" not in context.slots.vocabulary(evidence_id)
    assert _reason("{played} missed a threat.", NO_THREAT) == "chess_token_outside_slot"


def test_a_passed_factor_name_is_allowed_and_an_absent_one_is_not() -> None:
    text = "After {played}, your king safety suffered: {factor:king_safety:best}."
    assert "king safety is" in _validate(text, MISSED_THREAT)
    plain = "After {played}, your king safety suffered."
    assert _reason(plain, NO_KING) == "chess_token_outside_slot"


@pytest.mark.parametrize(
    "text",
    [
        "{played} missed a threat on g5.",  # square
        "{played} missed a threat: Bxg5.",  # SAN
        "{played} missed a threat, e2e4.",  # UCI
        "{played} cost 3 pawns of king safety.",  # number
        "{played} lost king safety, +1.5.",  # evaluation
        "Castle with O-O to escape the threat.",  # castling notation
        "{played} allows Qh7#, a threat.",  # mate suffix
        "The white king lost its safety after {played}.",  # colour word
        "{played} was a threat to win material.",  # words no fact names
        "Your advantage is gone after {played}.",  # evaluation word
    ],
)
def test_notation_and_unnamed_words_stay_rejected(text: str) -> None:
    assert _reason(text, MISSED_THREAT) == "chess_token_outside_slot"


@pytest.mark.parametrize("position_id", [MISSED_THREAT, NO_THREAT, NO_KING])
@pytest.mark.parametrize("text", [text for _, text, _ in ADVERSARIAL])
def test_every_adversarial_claim_text_is_rejected_as_free_prose(
    text: str, position_id: str
) -> None:
    """Every #366 and #384 adversarial text holds notation or an unnamed word."""
    assert _reason(text, position_id) == "chess_token_outside_slot"


@pytest.mark.parametrize("position_id", [MISSED_THREAT, NO_THREAT, NO_KING])
@pytest.mark.parametrize("text", [text for _, text in CORRECT])
def test_a_correct_grammar_text_passes_only_without_notation(text: str, position_id: str) -> None:
    """Free prose passes only when it has no notation and every chess word is a fact."""
    try:
        _validate(text, position_id)
    except GroundedExplanationValidationError as error:
        assert error.reason == "chess_token_outside_slot"
        return
    assert NOTATION.search(text) is None
    # For example "Check the opponent's captures before moving.": check and
    # capture slots are listed for this evidence item.


def test_the_rule_is_driven_by_the_facts_only() -> None:
    context, evidence_id = _context(MISSED_THREAT)
    assert context.slots is not None
    bare = SlotCatalogue(by_evidence={evidence_id: {}})
    assert chess_words_outside_slots("a direct threat", bare.vocabulary(evidence_id)) == "threat"
    named = dataclasses.replace(bare, terms={evidence_id: ("Missed direct threat",)})
    assert chess_words_outside_slots("a direct threat", named.vocabulary(evidence_id)) is None
    assert chess_words_outside_slots("threats", vocabulary(["threat"])) is None
    assert chess_words_outside_slots("threatened", vocabulary(["threat"])) == "threatened"
    assert chess_words_outside_slots("on d2", vocabulary(["d"])) == "2"


def test_a_quoted_evidence_ref_is_read_without_its_quotes() -> None:
    context, evidence_id = _context(MISSED_THREAT)
    claim = ExplanationClaim(
        text="You played {played}.", evidence_ref=f'"{evidence_id}"', line=(), disclosure_level=1
    )
    explanation = validate_generated_explanation(GeneratedExplanation(claims=(claim,)), context)
    assert explanation.claims[0].evidence_ref == evidence_id
    unknown = claim.model_copy(update={"evidence_ref": '"ev_missing"'})
    with pytest.raises(GroundedExplanationValidationError) as raised:
        validate_generated_explanation(GeneratedExplanation(claims=(unknown,)), context)
    assert raised.value.reason == "unknown_evidence"

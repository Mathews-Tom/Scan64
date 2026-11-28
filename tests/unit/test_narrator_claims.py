"""Relational claims in free prose must be backed by a fact (#431).

"You are up a piece" holds no chess word and no notation, so the vocabulary
rule (#408) let it through on the starting position. The claim rule
(``explanations/claims.py``) reads such claims and checks side and sign against
the facts of the cited evidence item.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import chess
import pytest
from chess_lesson_spec import Diagnosis, ExplanationClaim

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.assembly import grounding_context_for
from scan64.explanations.board_lines import (
    board_slot_grounding,
    build_board_lines_facts,
    scored_legal_lines,
)
from scan64.explanations.claims import ClaimFacts, unsupported_claim
from scan64.explanations.slots import (
    CHESS_WORDS,
    Slot,
    SlotCatalogue,
    chess_words_outside_slots,
)
from scan64.explanations.validator import (
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    validate_generated_explanation,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import GeneratedExplanation

ROOT = Path(__file__).parents[2]
BOARD = json.loads((ROOT / "tests/fixtures/analysis_board_lines.json").read_text("utf-8"))
LESSONS: dict[str, dict[str, Any]] = {
    item["position_id"]: item
    for item in json.loads((ROOT / "benchmarks/narrator/positions.json").read_text("utf-8"))[
        "positions"
    ]
}
UP_A_PIECE = "recorded:https://lichess.org/y3m3quag:35"  # learner ahead 3 to 9 points throughout
LEVEL = "recorded:https://lichess.org/61wsdfp4:30"  # learner never more than a pawn ahead
THREATS_FELL = "recorded:https://lichess.org/15lzmn9d:7"  # Threats change against the learner


def _board(name: str) -> tuple[GroundedExplanationContext, str]:
    entry = BOARD["positions"][name]
    board = chess.Board(entry["fen"])
    analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
    facts = build_board_lines_facts(board, scored_legal_lines(board, analysis)[:3])
    return board_slot_grounding(facts), "line-1"


def _lesson(position_id: str) -> tuple[GroundedExplanationContext, str]:
    item = LESSONS[position_id]
    context = grounding_context_for(
        Diagnosis(**item["diagnosis"]), [Evidence(**e) for e in item["evidence"]], item["fen"]
    )
    return context, context.evidence[0].evidence_id


def _validate(text: str, context: GroundedExplanationContext, evidence_id: str) -> str:
    claim = ExplanationClaim(text=text, evidence_ref=evidence_id, line=(), disclosure_level=1)
    return validate_generated_explanation(GeneratedExplanation(claims=(claim,)), context).text


def _rejects(
    text: str,
    context: GroundedExplanationContext,
    evidence_id: str,
    reason: str = "unsupported_claim",
) -> bool:
    """Whether validation rejects ``text``; a rejection must carry ``reason``."""
    try:
        _validate(text, context, evidence_id)
    except GroundedExplanationValidationError as error:
        assert error.reason == reason
        return True
    return False


def _facts(context: GroundedExplanationContext, evidence_id: str) -> ClaimFacts:
    assert context.slots is not None
    return context.slots.claim_facts(evidence_id)


ISSUE_CLAIMS = [
    "You are up a piece.",
    "You're a piece up after {first:line-1}.",
    "You are a pawn down.",
    "You won the exchange.",
    "You are winning.",
    "Your opponent is better.",
    "Your king is safe.",
    "The opponent's king is exposed.",
    "The opponent has no counterplay.",
    "You have the initiative.",
    "Your pieces are passive.",
    "The side to move has an extra pawn.",
]


@pytest.mark.parametrize("text", ISSUE_CLAIMS)
def test_issue_claims_are_rejected_on_the_starting_position(text: str) -> None:
    """A claim with a chess word ("pawn", "king") already meets the slot-token rule."""
    context, evidence_id = _board("start")
    expected = "chess_token_outside_slot" if CHESS_WORDS.search(text) else "unsupported_claim"
    assert _rejects(text, context, evidence_id, expected)
    assert unsupported_claim(text, _facts(context, evidence_id)) is not None


def test_the_issue_example_passes_only_where_the_material_supports_it() -> None:
    supported, supported_id = _lesson(UP_A_PIECE)
    assert {3, 4} & _facts(supported, supported_id).balances
    assert _validate("You are up a piece.", supported, supported_id) == "You are up a piece."
    level, level_id = _lesson(LEVEL)
    assert not {3, 4} & _facts(level, level_id).balances
    assert _rejects("You are up a piece.", level, level_id)


def test_a_claim_about_the_wrong_side_is_rejected() -> None:
    context, evidence_id = _lesson(UP_A_PIECE)
    assert _rejects("Your opponent is up a piece.", context, evidence_id)
    threats, threats_id = _lesson(THREATS_FELL)
    assert _validate("Your opponent has counterplay.", threats, threats_id)
    assert _rejects("Your opponent has no counterplay.", threats, threats_id)
    assert _rejects("You have counterplay.", threats, threats_id)


SUPPORTING = ClaimFacts(
    balances=frozenset({0, -1, 3}),
    changes=frozenset({2, -3}),
    evaluations=frozenset({20, 350}),
    factors=frozenset({("king", 1), ("threats", 1), ("activity", -1)}),
)
NOTHING = ClaimFacts(balances=frozenset({0}), changes=frozenset({0}), evaluations=frozenset({20}))


@pytest.mark.parametrize(
    ("text", "wrong_side"),
    [
        ("You are up a piece.", "Your opponent is up a piece."),
        ("You are a pawn down.", "Your opponent is a pawn down."),
        ("You won the exchange.", "Your opponent won the exchange."),
        ("Your opponent wins a piece.", "You win a piece."),
        ("You are winning.", "Your opponent is winning."),
        ("Your king is safe.", "Your opponent's king is safe."),
        ("The opponent's king is exposed.", "Your king is exposed."),
        ("The opponent has no counterplay.", "You have no counterplay."),
        ("Your opponent's pieces are active.", "Your pieces are active."),
        ("Material is level.", "You are up two pawns."),
    ],
)
def test_each_claim_follows_its_fact_side_and_sign(text: str, wrong_side: str) -> None:
    assert unsupported_claim(text, SUPPORTING) is None
    assert unsupported_claim(text, NOTHING) is not None or text == "Material is level."
    assert unsupported_claim(wrong_side, SUPPORTING) is not None


def test_a_supported_claim_still_needs_its_chess_words_named() -> None:
    """The claim rule only adds rejections; "won" and "exchange" stay vocabulary words."""
    catalogue = SlotCatalogue(by_evidence={"ev": {}}, claims={"ev": SUPPORTING})
    context = GroundedExplanationContext(
        fen=chess.STARTING_FEN,
        evidence=(
            Evidence(
                evidence_id="ev", kind="engine", position_id="p", engine_analysis_id="a", claim="x"
            ),
        ),
        verified_lines={},
        requested_hint_level=1,
        slots=catalogue,
    )
    assert _validate("You are up a piece.", context, "ev") == "You are up a piece."
    assert _rejects("You won the exchange.", context, "ev", "chess_token_outside_slot")
    # An item without claim facts supports no claim.
    bare = dataclasses.replace(context, slots=SlotCatalogue(by_evidence={"ev": {}}))
    assert _rejects("You are up a piece.", bare, "ev")


@pytest.mark.parametrize(
    "text",
    [
        # Review of #431: judgements around a factor name, position subjects, negation.
        "Your king safety is excellent.",
        "Their king safety is poor.",
        "Your pawn shelter is intact.",
        "Your position is better.",
        "Your position is winning.",
        "You are in serious trouble.",
        "You are materially up.",
        "You are cramped.",
        "Your pieces are better.",
        "The evaluation favors you.",
        "You are not up a piece.",
    ],
)
def test_paraphrased_and_negated_claims_need_a_fact(text: str) -> None:
    rejected = unsupported_claim(text, NOTHING)
    assert rejected is not None and rejected.negated == (" not " in text)


def test_a_negated_claim_is_rejected_even_where_the_claim_is_true() -> None:
    """No fact records an absence: "not up a piece" is rejected on a piece-up position."""
    context, evidence_id = _lesson(UP_A_PIECE)
    assert _rejects("You are not up a piece.", context, evidence_id, "negated_claim")
    level, level_id = _lesson(LEVEL)
    assert _rejects("You aren't up a piece.", level, level_id, "negated_claim")


def test_a_judgement_around_a_factor_name_follows_its_sign() -> None:
    context, evidence_id = _lesson(THREATS_FELL)
    assert _validate("Your king safety is worse now.", context, evidence_id)
    assert _rejects("Your king safety is excellent.", context, evidence_id)


@pytest.mark.parametrize(
    "text",
    [
        "You are behind on the clock, so slow down.",
        "You're ahead of the curve.",
        "You are winning the learning game.",
    ],
)
def test_tone_with_a_chess_word_is_no_claim(text: str) -> None:
    """The claim rule does not read it; its chess word stays a vocabulary question (#408)."""
    context, evidence_id = _lesson(LEVEL)
    assert unsupported_claim(text, _facts(context, evidence_id)) is None


NEUTRAL = [
    "They are better prepared than you think.",
    "That shows real initiative.",
    "Good effort! You played {played}, a {mistake}.",
    "Keep going, you are learning fast. Slow down before each move.",
    "That one stings, but it happens to everyone.",
    "You were better off playing {best}.",
    "Look again at {best}: calm and simple.",
    "You are doing well; stay curious and enjoy the game.",
]


@pytest.mark.parametrize("text", NEUTRAL)
def test_tone_and_personality_words_stay_allowed(text: str) -> None:
    context, evidence_id = _lesson(LEVEL)
    assert unsupported_claim(text, _facts(context, evidence_id)) is None
    _validate(text, context, evidence_id)


@pytest.mark.parametrize(
    "text",
    [
        "{label:line-1} starts with {first:line-1}. A calm, classical start.",
        "{label:line-2} starts with {first:line-2}, a quieter choice: {diff:line-2}.",
    ],
)
def test_tone_passes_on_the_analysis_board(text: str) -> None:
    context, _ = _board("start")
    _validate(text, context, "comparison")


def test_a_factor_name_admits_its_words_only_as_the_whole_name() -> None:
    """#431: "rook threats" no longer admits a bare "rook"; "piece placement" no "piece"."""
    catalogue = SlotCatalogue(
        by_evidence={"ev": {"factor:rook_threats:best": Slot("factor:rook_threats:best", "", "")}},
        phrases={"ev": ("rook threats", "piece placement")},
    )
    words = catalogue.vocabulary("ev")
    assert "rook" not in words and "threat" not in words
    phrases = catalogue.phrases["ev"]
    assert chess_words_outside_slots("your rook threats grew", words, phrases) is None
    assert chess_words_outside_slots("your rook is lost", words, phrases) == "rook"
    assert chess_words_outside_slots("a threat", words, phrases) == "threat"
    assert unsupported_claim("You are up a piece.", catalogue.claim_facts("ev")) is not None


def test_a_lesson_factor_name_is_a_phrase_and_its_slot_adds_no_word() -> None:
    context, evidence_id = _lesson(THREATS_FELL)
    assert context.slots is not None
    assert "king safety" in context.slots.phrases[evidence_id]
    assert "safety" not in context.slots.vocabulary(evidence_id)
    text = "After {played}, your king safety suffered."
    assert _validate(text, context, evidence_id).endswith("your king safety suffered.")
    assert _rejects("After {played}, your king safety improved.", context, evidence_id)

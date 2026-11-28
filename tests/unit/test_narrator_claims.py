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
from scan64.explanations.factor_facts import FactorChange
from scan64.explanations.slots import (
    CHESS_WORDS,
    Slot,
    SlotCatalogue,
    chess_words_outside_slots,
    factor_names,
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
    factors=frozenset({("king", 1, "before"), ("threats", 1, "before"), ("activity", -1, "best")}),
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


def test_a_threats_factor_name_admits_its_words_only_with_its_head_noun() -> None:
    """#431: "rook threats" no longer admits a bare "rook"; "piece placement" no "piece"."""
    catalogue = SlotCatalogue(
        by_evidence={"ev": {"factor:rook_threats:best": Slot("factor:rook_threats:best", "", "")}},
        phrases={"ev": ("rook threats", "safe pawn threats", "slider on the queen")},
    )
    words = catalogue.vocabulary("ev")
    assert "rook" not in words and "threat" not in words
    phrases = catalogue.phrases["ev"]
    for text in ("your rook threats grew", "fewer pawn threats", "a threat"):
        assert chess_words_outside_slots(text, words, phrases) is None
    assert chess_words_outside_slots("your rook is lost", words, phrases) == "rook"
    assert chess_words_outside_slots("keep pawns safe", words, phrases) == "pawns"
    # A name not ending in "threats" counts only as a whole.
    assert chess_words_outside_slots("a slider on the queen", words, phrases) is None
    assert chess_words_outside_slots("your queen is trapped", words, phrases) == "queen"
    assert unsupported_claim("You are up a piece.", catalogue.claim_facts("ev")) is not None


def _change(guide_name: str, name: str, kind: str = "term") -> FactorChange:
    return FactorChange(
        key=name.replace(" ", "_"),
        name=name,
        kind=kind,  # type: ignore[arg-type]
        guide_name=guide_name,
        compared_with="best",
        change_pawns=-0.5,
    )


def test_only_threats_factor_names_are_phrases() -> None:
    """A Threats name names an attacker type; others name the side's own king or pieces."""
    changes = [
        _change("King", "king safety", "group"),
        _change("Rook threat", "rook threats"),
        _change("Weak queen", "weak queen"),
        _change("King", "king safety", "group"),
    ]
    assert factor_names(changes) == (("king safety", "weak queen"), ("rook threats",))


def test_a_lesson_factor_name_and_its_slot() -> None:
    context, evidence_id = _lesson(THREATS_FELL)
    assert context.slots is not None
    assert "king safety" in context.slots.terms[evidence_id]
    assert not any(name.startswith("factor:") for name in context.slots.terms[evidence_id])
    text = "After {played}, your king safety suffered."
    assert _validate(text, context, evidence_id).endswith("your king safety suffered.")
    assert _rejects("After {played}, your king safety improved.", context, evidence_id)


def test_a_comparison_with_the_best_move_is_checked_for_its_topic() -> None:
    """A change against the best move is played minus best: about the best move, flip it."""
    context, evidence_id = _lesson(THREATS_FELL)
    factors = _facts(context, evidence_id).factors
    assert ("king", -1, "best") in factors and ("king", 1, "best") not in factors
    for text in (
        "The best move keeps your king safer.",
        "{best} keeps your king safer.",
        "Your king is safe only after the best move.",
        "Your king safety is worse than after the best move.",
    ):
        assert _validate(text, context, evidence_id)
    for text in (
        "The best move makes your king weaker.",
        "With the best move, your king is more exposed.",
        "Your king is safer after your move.",
    ):
        assert _rejects(text, context, evidence_id)


def test_the_pawn_unit_after_an_evaluation_slot_is_not_a_chess_word() -> None:
    context, evidence_id = _lesson(LEVEL)
    assert context.slots is not None and "eval_diff" in context.slots.slots_for(evidence_id)
    text = _validate("The best move scores {eval_diff} pawns better.", context, evidence_id)
    assert text.count("pawns") == 1
    bare = SlotCatalogue(by_evidence={"ev": {}})
    assert chess_words_outside_slots("you are two pawns short", bare.vocabulary("ev")) == "pawns"


RICH_MATERIAL = ClaimFacts(
    balances=frozenset({-4, -1, 0, 1, 2, 3, 4, 9}),
    changes=frozenset({-3, -1, 1, 2, 3, 9}),
    evaluations=frozenset({-300, 20, 400}),
)
SLOT_AS_MATERIAL = [
    "You are {eval_diff} pawns up in material.",
    "You are {eval_diff} pawns ahead.",
    "You are {eval_diff} pawns behind in material.",
    "You are {eval_diff} pawns down.",
    "You are up {eval_diff} pawns.",
    "You are down {eval_diff} pawns in material.",
    "You are ahead by {eval_diff} pawns.",
    "You win {eval_diff} pawns.",
    "That is {eval_diff} pawns of material.",
    "{eval_diff} pawns in material for you.",
    # Pre-push check of 0571977: separated or reworded material wording.
    "You are {eval_diff} pawns, up in material.",
    "You are up in material by {eval_diff} pawns.",
    "Your material edge is {eval_diff} pawns.",
    "You have {eval_diff} more pawns.",
    "You have {eval_diff} extra pawns.",
    "The material balance is {eval_diff} pawns in your favour.",
    "You are {eval_diff} pawns in front.",
    "You are {eval_diff} pawns richer.",
    "You have {eval_diff} pawns more than your opponent.",
    # Pre-push check of ff957df.
    "You are {eval_diff} pawns in the lead.",
    "You are {eval_diff} pawns short.",
    "You are short {eval_diff} pawns.",
    "You are plus {eval_diff} pawns.",
    "Your opponent leads by {eval_diff} pawns.",
    "You capture {eval_diff} pawns.",
    # Pre-push check of 7079a8b: material words inside a score sentence.
    "Score: you capture {eval_diff} pawns.",
    "The evaluation shows you up {eval_diff} pawns of pieces.",
    "Evaluation: you have {eval_diff} pawns more.",
]


@pytest.mark.parametrize("text", SLOT_AS_MATERIAL)
def test_an_evaluation_slot_is_never_a_material_amount(text: str) -> None:
    """#442 re-review: the pawn unit after a slot covers evaluation wording only."""
    prose = text.replace("{eval_diff}", "~")
    assert unsupported_claim(prose, RICH_MATERIAL) is not None
    context, evidence_id = _board("start")
    board_text = text.replace("{eval_diff}", "{eval}")
    reason = None
    try:
        _validate(board_text, context, evidence_id)
    except GroundedExplanationValidationError as error:
        reason = error.reason
    assert reason in ("chess_token_outside_slot", "unsupported_claim")


@pytest.mark.parametrize(
    "text",
    ["You are a pawn up.", "The side to move wins a pawn.", "You are a pawn ahead in material."],
)
def test_material_wording_on_level_material_is_rejected(text: str) -> None:
    context, evidence_id = _board("start")
    assert _facts(context, evidence_id).balances == {0}
    try:
        _validate(text, context, evidence_id)
    except GroundedExplanationValidationError:
        return
    raise AssertionError(f"accepted on level material: {text!r}")


def test_material_wording_with_a_count_still_follows_the_facts() -> None:
    assert unsupported_claim("You are a pawn up in material.", RICH_MATERIAL) is None
    assert unsupported_claim("You are up in material.", RICH_MATERIAL) is None
    assert unsupported_claim("~ scores ~ pawns.", RICH_MATERIAL) is None
    level = ClaimFacts(balances=frozenset({0}))
    for text in ("You have two more pawns.", "Your material edge is two pawns."):
        assert unsupported_claim(text, RICH_MATERIAL) is None
        assert unsupported_claim(text, level) is not None


@pytest.mark.parametrize(
    "text",
    [
        "The best move scores {eval_diff} pawns better.",
        "The evaluation went down by {eval_diff} pawns.",
        "The swing is {eval_diff} pawns in favor of the better choice.",
        "The position before your move had an evaluation of {eval_before} pawns.",
        "The evaluation is down {eval_diff} pawns.",
        "Evaluation: {eval_diff} pawns down.",
        "The score is {eval_diff} pawns down from before.",
        "That costs {eval_diff} pawns of evaluation.",
    ],
)
def test_evaluation_wording_with_the_pawn_unit_passes(text: str) -> None:
    context, evidence_id = _lesson(LEVEL)
    assert _validate(text, context, evidence_id).count("pawns") == 1


EDGE_CLAIMS = [
    # (text, learner material advantages that support it)
    ("Your material edge is two pawns.", {2}),
    ("Your opponent's material edge is two pawns.", {-2}),
    ("Your material lead is a pawn.", {1}),
    ("Their material advantage is a piece.", {-3, -4}),
    ("You are ahead by two pawns.", {2}),
    ("You are two pawns ahead.", {2}),
    ("You are up two pawns.", {2}),
    ("You have two extra pawns.", {2}),
    ("You have an extra piece.", {3, 4}),
    ("You are up in material.", {1, 2, 3, 4, 5, 6, 9}),
    ("You are materially up.", {1, 2, 3, 4, 5, 6, 9}),
    ("You have a material edge.", {1, 2, 3, 4, 5, 6, 9}),
    ("Your opponent is ahead by two pawns.", {-2}),
    ("Your opponent is up in material.", {-1, -2, -3, -4, -5, -6, -9}),
    ("Your opponent has a material lead.", {-1, -2, -3, -4, -5, -6, -9}),
    ("Your opponent has two extra pawns.", {-2}),
    ("They are a piece up.", {-3, -4}),
    # Pre-PR check: owner subjects, "lead by", "N pawns more", "extra pawn", "two-pawn".
    ("Your material is up two pawns.", {2}),
    ("Your side is two pawns ahead.", {2}),
    ("Your opponent's side is two pawns ahead.", {-2}),
    ("Your edge is two pawns.", {2}),
    ("Your plus in material is two pawns.", {2}),
    ("You lead by two pawns.", {2}),
    ("Your opponent leads by two pawns.", {-2}),
    ("You have two pawns more.", {2}),
    ("You have two pawns fewer.", {-2}),
    ("Your extra pawn decides.", {1}),
    ("Their extra pawn matters.", {-1}),
    ("Your extra rook decides.", {5, 6}),
    # Review of #443: no side named (either side, size checked), and "to the good".
    ("A two-pawn lead.", {2, -2}),
    ("With a two-pawn lead, play simply.", {2, -2}),
    ("A pawn more.", {1, -1}),
    ("Two pawns more.", {2, -2}),
    ("A material edge of two pawns.", {2, -2}),
    ("A material edge of two pawns for you.", {2}),
    ("A material edge of two pawns for your opponent.", {-2}),
    ("You are two pawns to the good.", {2}),
    ("Your opponent is two pawns to the good.", {-2}),
    ("Two pawns to the good.", {2, -2}),
    ("You enjoy a two-pawn lead.", {2}),
    ("Your two-pawn edge counts.", {2}),
]


@pytest.mark.parametrize(("text", "supporting"), EDGE_CLAIMS)
@pytest.mark.parametrize("balance", [-9, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 9])
def test_a_material_edge_claim_checks_its_side_and_size(
    text: str, supporting: set[int], balance: int
) -> None:
    """#442 re-review: "your material edge is two pawns" was accepted two pawns down."""
    facts = ClaimFacts(balances=frozenset({balance}))
    assert (unsupported_claim(text, facts) is None) == (balance in supporting)


@pytest.mark.parametrize(
    "text",
    [
        "Your extra pieces are active.",
        "Your additional pieces help.",
        "Your extra effort pays off.",
        "You have two options more.",
        "That is all to the good.",
    ],
)
def test_an_uncounted_extra_is_no_material_claim(text: str) -> None:
    assert unsupported_claim(text, ClaimFacts(balances=frozenset({0}))) is None

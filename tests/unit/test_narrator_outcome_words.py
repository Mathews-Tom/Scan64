"""Outcome words on the Analysis Board lines path, checked against the line (#449).

``material``, ``win``, ``lose``, ``gain``, ``equal``, ``advantage``, ``ahead``
and ``behind`` pass the vocabulary rule on a board evidence item only inside a
material claim that the line's outcome supports, side and size checked
(``claims.outcome_claims``). Each word has a true and a false case on the
recorded positions; outside such a claim the word is still rejected, and the
lesson path is unchanged.

Outcomes used (side to move's view, MultiPV 3): ``scholar_threat`` line 1 wins
1 and ends up 1, line 3 loses 1 and ends down 1; ``back_rank`` lines change
nothing and end up 5; ``start`` lines change nothing and end equal.
"""

from __future__ import annotations

import re

import pytest
from chess_lesson_spec import ExplanationClaim

from benchmarks.narrator.run_benchmark import (
    REPO_ROOT,
    load_board_positions,
    load_positions,
)
from scan64.explanations.assembly import grounding_context_for
from scan64.explanations.board_lines import board_slot_grounding
from scan64.explanations.claims import (
    ClaimFacts,
    OutcomeFacts,
    outcome_claims,
    unsupported_claim,
)
from scan64.explanations.validator import (
    GroundedExplanationValidationError,
    validate_generated_explanation,
)
from scan64.providers.llm.contracts import GeneratedExplanation

BOARD = {
    p.position_id: p
    for p in load_board_positions(REPO_ROOT / "benchmarks/narrator/positions-board.json")
}


def _reason(position: str, evidence_ref: str, text: str) -> str | None:
    context = board_slot_grounding(BOARD[position].facts)
    claim = ExplanationClaim(text=text, evidence_ref=evidence_ref, disclosure_level=1)
    try:
        validate_generated_explanation(GeneratedExplanation(claims=(claim,)), context)
    except GroundedExplanationValidationError as error:
        return error.reason
    return None


def test_the_recorded_outcomes_are_the_ones_the_cases_assume() -> None:
    def outcome(position: str, index: int) -> tuple[int, int]:
        line = BOARD[position].facts.lines[index]
        return line.outcome.material_change, line.outcome.material_at_end.learner_advantage

    assert outcome("scholar_threat", 0) == (1, 1)
    assert outcome("scholar_threat", 2) == (-1, -1)
    assert {outcome("back_rank", i) for i in range(3)} == {(0, 5)}
    assert {outcome("start", i) for i in range(3)} == {(0, 0)}


# (word, position, evidence_ref, true text, false text, why the false text is false)
CASES = [
    (
        "material",
        "scholar_threat",
        "line-1",
        "In {label:line-1}, the side to move wins material.",
        "In {label:line-1}, the opponent wins material.",
        "wrong side",
    ),
    (
        "wins",
        "scholar_threat",
        "line-1",
        "{label:line-1} wins one point of material for the side to move.",
        "{label:line-1} wins three points of material for the side to move.",
        "wrong size",
    ),
    (
        "win",
        "scholar_threat",
        "line-1",
        "With {label:line-1} the side to move can win material.",
        "With {label:line-1} the opponent can win material.",
        "wrong side",
    ),
    (
        "won",
        "scholar_threat",
        "line-1",
        "Over {label:line-1} the side to move has won material.",
        "Over {label:line-3} the side to move has won material.",
        "loss, not gain",
    ),
    (
        "winning",
        "scholar_threat",
        "line-1",
        "{label:line-1} ends with the side to move winning material.",
        "{label:line-1} ends with the side to move winning two points of material.",
        "wrong size",
    ),
    (
        "loses",
        "scholar_threat",
        "line-3",
        "In {label:line-3}, the side to move loses one point of material.",
        "In {label:line-1}, the side to move loses one point of material.",
        "gain, not loss",
    ),
    (
        "lose",
        "scholar_threat",
        "line-3",
        "In {label:line-3}, the side to move will lose material.",
        "In {label:line-3}, the opponent will lose material.",
        "wrong side",
    ),
    (
        "lost",
        "scholar_threat",
        "line-3",
        "By the end of {label:line-3} the side to move has lost material.",
        "By the end of {label:line-1} the side to move has lost material.",
        "gain, not loss",
    ),
    (
        "losing",
        "scholar_threat",
        "line-3",
        "{label:line-3} ends with the side to move losing material.",
        "{label:line-3} ends with the opponent losing material.",
        "wrong side",
    ),
    (
        "gains",
        "scholar_threat",
        "line-1",
        "In {label:line-1}, the side to move gains material.",
        "In {label:line-3}, the side to move gains material.",
        "loss, not gain",
    ),
    (
        "gain",
        "scholar_threat",
        "line-1",
        "In {label:line-1}, the side to move can gain one point of material.",
        "In {label:line-1}, the opponent can gain one point of material.",
        "wrong side",
    ),
    (
        "equal",
        "start",
        "line-1",
        "In {label:line-1}, material is equal.",
        "In {label:line-1}, material is equal.",
        "not equal",
    ),
    (
        "advantage",
        "back_rank",
        "line-1",
        "In {label:line-1}, the side to move has a material advantage.",
        "In {label:line-1}, the opponent has a material advantage.",
        "wrong side",
    ),
    (
        "ahead",
        "back_rank",
        "line-2",
        "In {label:line-2}, the side to move is ahead in material.",
        "In {label:line-2}, the opponent is ahead in material.",
        "wrong side",
    ),
    (
        "behind",
        "back_rank",
        "line-2",
        "In {label:line-2}, the opponent is behind in material.",
        "In {label:line-2}, the side to move is behind in material.",
        "wrong side",
    ),
]


@pytest.mark.parametrize(
    ("word", "position", "evidence_ref", "true_text", "false_text", "why"), CASES
)
def test_each_outcome_word_follows_the_line(
    word: str, position: str, evidence_ref: str, true_text: str, false_text: str, why: str
) -> None:
    assert word in true_text.lower() and word in false_text.lower()
    assert _reason(position, evidence_ref, true_text) is None
    # "equal": the same text on a line whose material is not equal.
    false_position = "scholar_threat" if why == "not equal" else position
    false_ref = re.search(r"\{label:(line-\d)\}", false_text)
    assert false_ref is not None
    assert _reason(false_position, false_ref.group(1), false_text) == "unsupported_claim", why


@pytest.mark.parametrize(
    ("position", "text", "expected"),
    [
        # The slot's own wording, and an equal balance at the end.
        ("back_rank", "In {label:line-1}, the material balance does not change.", None),
        (
            "scholar_threat",
            "In {label:line-1}, the material balance does not change.",
            "unsupported_claim",
        ),
        ("start", "{label:line-1} keeps an equal material balance.", None),
        ("back_rank", "{label:line-1} keeps an equal material balance.", "unsupported_claim"),
        # The balance at the end, not before the line: back rank starts and ends up 5.
        (
            "back_rank",
            "At the end of {label:line-1}, the side to move is up five points of material.",
            None,
        ),
        (
            "back_rank",
            "At the end of {label:line-1}, the side to move is up four points of material.",
            "unsupported_claim",
        ),
        # A side named after the claim.
        ("scholar_threat", "{label:line-1} wins material for the opponent.", "unsupported_claim"),
        # A side-free claim in a sentence that names the opponent is about neither side.
        (
            "scholar_threat",
            "After the opponent replies, {label:line-1} wins material.",
            "chess_token_outside_slot",
        ),
        # A slot is no amount of material.
        (
            "scholar_threat",
            "In {label:line-1}, the side to move wins {eval} points of material.",
            "unsupported_claim",
        ),
        # A negated outcome claim records an absence.
        (
            "back_rank",
            "In {label:line-1}, the side to move is not up a point of material.",
            "negated_claim",
        ),
    ],
)
def test_outcome_claims_on_a_line(position: str, text: str, expected: str | None) -> None:
    assert _reason(position, "line-1", text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Outcome words outside a material claim are still no fact name.
        "{label:line-1} wins for the side to move.",
        "The side to move is winning in {label:line-1}.",
        "{label:line-1} gives the side to move an advantage.",
        "In {label:line-1}, the position is equal.",
        "{label:line-1} results in a material balance that favours the side to move.",
        "The side to move wins a lot of material in {label:line-1}.",
    ],
)
def test_outcome_words_outside_a_material_claim_stay_rejected(text: str) -> None:
    assert _reason("scholar_threat", "line-1", text) == "chess_token_outside_slot"


def test_a_comparison_claim_must_hold_for_every_line() -> None:
    # All three start lines keep material equal; scholar_threat's lines differ.
    text = "In {label:line-1}, {label:line-2} and {label:line-3}, material is equal."
    assert _reason("start", "comparison", text) is None
    assert _reason("scholar_threat", "comparison", text) == "unsupported_claim"
    gain = "{label:line-1} and {label:line-2} show the side to move winning material."
    assert _reason("scholar_threat", "comparison", gain) == "unsupported_claim"


def test_outcome_claims_returns_the_admitted_prose_and_the_false_claim() -> None:
    up_one = (OutcomeFacts(change=1, balance=1),)
    prose, rejected = outcome_claims("so the side to move wins material here", up_one)
    assert "wins" not in prose and "material" not in prose and rejected is None
    prose, rejected = outcome_claims("so the opponent wins material here", up_one)
    assert rejected is not None and rejected.text == "the opponent wins material"
    # A word outside the claim stays for the vocabulary rule.
    prose, rejected = outcome_claims("this line wins", up_one)
    assert "wins" in prose and rejected is None


def test_the_lesson_path_admits_no_outcome_word() -> None:
    position = load_positions(REPO_ROOT / "benchmarks/narrator/positions.json")[0]
    context = grounding_context_for(position.diagnosis, list(position.evidence), position.fen)
    assert context.slots is not None and not context.slots.outcomes
    evidence_ref = position.evidence[0].evidence_id
    claim = ExplanationClaim(
        text="After {played}, the side to move wins material.",
        evidence_ref=evidence_ref,
        disclosure_level=1,
    )
    with pytest.raises(GroundedExplanationValidationError) as error:
        validate_generated_explanation(GeneratedExplanation(claims=(claim,)), context)
    assert error.value.reason in ("chess_token_outside_slot", "unsupported_claim")


def test_no_change_claims_on_the_lesson_path_need_an_unchanged_ply() -> None:
    # The new "does not change" rule reads lesson prose too; it only adds a check.
    unchanged = ClaimFacts(changes=frozenset({0, 3}))
    assert unsupported_claim("the material balance does not change", unchanged) is None
    rejected = unsupported_claim(
        "the material balance does not change", ClaimFacts(changes=frozenset({3}))
    )
    assert rejected is not None


# Pre-PR review (#449): negations and unknown subjects the pattern rules do not
# read must not be checked as the positive claim. scholar_threat line 1 wins a
# pawn, line 3 loses one; every promotion line ends ahead.
PREFIX = "{label:line-1} starts with {first:line-1}, and "
REJECTED = {"negated_claim", "unsupported_claim", "chess_token_outside_slot"}


@pytest.mark.parametrize(
    "tail",
    [
        "the side to move does not win material.",
        "the side to move doesn't win material.",
        "the side to move never wins material.",
        "the side to move fails to win material.",
        "the side to move cannot win material.",
        "the side to move is not winning material.",
        "the side to move does not gain material.",
        "nobody wins material.",
        "neither side wins material.",
        "the line ends without winning material.",
    ],
)
def test_a_negated_outcome_claim_is_rejected(tail: str) -> None:
    # A negation word outside the sentence frame leaves the outcome words to the
    # vocabulary rule; inside it ("is not") the claim is negated.
    assert _reason("scholar_threat", "line-1", PREFIX + tail) in REJECTED


@pytest.mark.parametrize(
    "subject",
    ["the other player", "he", "the rival", "the enemy", "the reply", "the second side"],
)
def test_an_unknown_subject_is_read_as_no_side(subject: str) -> None:
    text = f"{PREFIX}{subject} wins material."
    assert _reason("scholar_threat", "line-1", text) in REJECTED


def test_the_side_to_move_as_subject_across_linking_words() -> None:
    text = "In {label:line-1}, the side to move ends up winning material."
    assert _reason("scholar_threat", "line-1", text) is None
    text = "In {label:line-1}, the side to move ends up losing material."
    assert _reason("scholar_threat", "line-1", text) == "unsupported_claim"


def test_a_negated_comparison_is_rejected() -> None:
    text = "{label:line-3} leaves the side to move ahead in material, and {label:line-1} does not."
    assert _reason("promotion", "comparison", text) in REJECTED
    assert (
        _reason("promotion", "comparison", "All lines leave the side to move ahead in material.")
        is None
    )


# Second pre-PR review (#449): words outside the claim that change its size or
# side. scholar_mate_in_one line 1 wins a pawn (+1, +1); line 3 loses one.
@pytest.mark.parametrize(
    ("evidence_ref", "text"),
    [
        ("line-1", "{label:line-1}: the side to move wins material by nine points."),
        ("line-1", "{label:line-1}: the side to move wins material, worth two points."),
        ("line-1", "{label:line-1}: the side to move wins material three times."),
        ("line-1", "{label:line-1}: the side to move wins material by a wide margin."),
        ("line-1", "{label:line-1}: the side to move wins material decisively."),
        ("line-3", "{label:line-3}: the opponent wins material by two points."),
        ("line-1", "{label:line-1}: the side to move wins material, and so does the opponent."),
        ("line-1", "{label:line-1}: the side to move wins material, as does the opponent."),
        ("line-1", "{label:line-1}: the side to move wins material and the opponent does too."),
        ("line-3", "{label:line-3}: the opponent wins material and so does the side to move."),
        ("line-1", "{label:line-1}: the opponent of the side to move wins material."),
        ("line-1", "{label:line-1}: the player facing the side to move wins material."),
        ("line-1", "{label:line-1}: the opponent and the side to move win material."),
        ("line-3", "{label:line-3}: the side to move and the opponent win material."),
        ("line-1", "{label:line-1}: the side to move wins material for the opponent."),
        ("line-1", "{label:line-1}: the side to move wins material only in theory."),
        ("line-1", "{label:line-1}: the side to move wins material again and again."),
    ],
)
def test_words_around_an_outcome_claim_that_change_it_are_rejected(
    evidence_ref: str, text: str
) -> None:
    assert _reason("scholar_mate_in_one", evidence_ref, text) in REJECTED


def test_material_is_equal_with_an_edge_is_rejected() -> None:
    text = "In {label:line-1}, material is equal, with a huge edge for the side to move."
    assert _reason("start", "line-1", text) in REJECTED
    assert (
        _reason("start", "line-1", "In {label:line-1}, material is equal by two points.")
        in REJECTED
    )


def test_two_checked_claims_in_one_sentence() -> None:
    # Both claims are read and both hold on scholar_mate_in_one line 1.
    text = "{label:line-1}: the side to move wins material, while the opponent loses material."
    assert _reason("scholar_mate_in_one", "line-1", text) is None
    text = "{label:line-1}: the side to move wins material, while the opponent wins material."
    assert _reason("scholar_mate_in_one", "line-1", text) == "unsupported_claim"

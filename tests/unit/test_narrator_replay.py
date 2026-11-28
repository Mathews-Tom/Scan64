"""Offline replay of the committed narrator records through today's validator (#431).

No model runs: each stored claim is validated again against its rebuilt
grounding context (``benchmarks/narrator/replay_grounding.py``).
"""

from __future__ import annotations

from collections import Counter

from benchmarks.narrator.replay_grounding import ISSUE_EXAMPLE, WRONG_SIDE, replay


def test_the_replay_rejects_only_the_classified_true_rejections() -> None:
    result = replay()
    assert result.accepted_before >= 5000
    # Each one classified by hand as a true rejection (PR #442):
    # - "you lost {material_gain} pawns": the slot is points of material, not pawns;
    # - "your opponent's position was much better after your move": still +0.61;
    # - "your opponent's king is not in immediate danger ... {check:played}": negated;
    # - "{factor:safe_pawn_threats:played} ... keep pawns safe": misreads the factor.
    assert result.newly_rejected == Counter(
        {"chess_token_outside_slot": 2, "unsupported_claim": 1, "negated_claim": 1}
    )
    # Every newly accepted claim is the unit after an evaluation slot: "{eval_diff} pawns".
    assert set(result.newly_accepted) == {"chess_token_outside_slot"}
    assert all(" pawn" in text for *_, text in result.accepted_examples)


def test_the_issue_example_and_its_wrong_side_follow_the_material() -> None:
    probes = replay().probes
    for text in (ISSUE_EXAMPLE, WRONG_SIDE):
        mine = [p for p in probes if p[1] == text]
        assert mine and any(expected for _, _, expected, _ in mine)
        assert all(expected == rejected for _, _, expected, rejected in mine)

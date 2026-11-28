"""Factor changes across a mistake as narrator facts (#408)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import chess
import pytest
from chess_lesson_spec import Diagnosis

from scan64.explanations.factor_facts import (
    FACTOR_CONFIG,
    FACTOR_SOURCE,
    GROUP_NAMES,
    TERM_NAMES,
    factor_key,
    line_factor_facts,
    mistake_factor_facts,
    passed_changes,
    quiet_ply,
)
from scan64.explanations.factors import GUIDE_COMMIT, PARTS
from scan64.explanations.facts import NarratorFacts, build_narrator_facts
from scan64.learning.evidence.models import Evidence

POSITIONS: dict[str, dict[str, Any]] = {
    item["position_id"]: item
    for item in json.loads(
        (Path(__file__).parents[2] / "benchmarks/narrator/positions.json").read_text("utf-8")
    )["positions"]
}
CLEAR_CAUSE = "recorded:https://lichess.org/15lzmn9d:7"
NO_CAUSE = "recorded:https://lichess.org/fiv37vzi:35"
UNQUIET = "recorded:https://lichess.org/l0gjct48:47"


def _facts(position_id: str) -> NarratorFacts:
    item = POSITIONS[position_id]
    return build_narrator_facts(
        Diagnosis(**item["diagnosis"]), [Evidence(**e) for e in item["evidence"]], item["fen"]
    )


def _no_cause_facts() -> NarratorFacts:
    """NO_CAUSE with its factor changes removed: the facts of a mistake with no cause.

    Under the #410 thresholds every quiet benchmark mistake has a factor change;
    the slot and prompt rules for "no cause" are tested on this copy.
    """
    facts = _facts(NO_CAUSE)
    assert facts.factors is not None
    return facts.model_copy(update={"factors": facts.factors.model_copy(update={"changes": ()})})


def test_config_is_one_table_with_the_fitted_thresholds() -> None:
    # Fitted in #410; benchmarks/factor_calibration/calibrate.py --check refits them.
    assert FACTOR_CONFIG.thresholds == {"group": 0.10, "term": 0.10}
    assert FACTOR_CONFIG.threshold("group", "King") == 0.75
    assert FACTOR_CONFIG.threshold("term", "King danger") == 0.45
    assert FACTOR_CONFIG.threshold("group", "Space") == 0.10
    groups = {group for _, group in PARTS}
    assert set(FACTOR_CONFIG.overrides) <= (set(GROUP_NAMES) | set(TERM_NAMES))
    assert not groups & set(TERM_NAMES)
    assert FACTOR_CONFIG.quiet_ply_limit == 8
    assert GUIDE_COMMIT[:7] in FACTOR_SOURCE


def test_every_named_part_has_a_plain_name_and_a_slot_key() -> None:
    stated = {name for name, group in PARTS if group in GROUP_NAMES}
    assert stated == set(TERM_NAMES) | {"Mobility bonus", "Space"}
    for name in (*GROUP_NAMES.values(), *TERM_NAMES.values()):
        key = factor_key(name)
        assert key and set(key) <= set("abcdefghijklmnopqrstuvwxyz_")


# --- Quiet positions -----------------------------------------------------------------


def test_quiet_ply_skips_a_pending_recapture() -> None:
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    # exd5 Qxd5: after exd5 the next move recaptures, so the first quiet ply is 2.
    assert quiet_ply(board, ["e4d5", "d8d5", "b1c3"], 1) == 2


def test_quiet_ply_skips_a_check() -> None:
    board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/5Q2/PPPP1PPP/RNB1KBNR b KQkq - 1 2")
    # ...Bb4 Qxf7+: Qxf7+ is a capture and a check; Kxf7 ends the line.
    assert quiet_ply(board, ["f8b4", "f3f7", "e8f7"], 1) == 3


def test_quiet_ply_needs_no_pending_capture_at_the_line_end() -> None:
    board = chess.Board("4k3/8/8/3q4/8/8/4K3/3Q4 b - - 1 1")
    # After ...Qd6 the line ends and White can take the undefended queen on d6.
    assert quiet_ply(board, ["d5d6"], 1) is None
    # After ...Qa5 no capture is pending.
    assert quiet_ply(board, ["d5a5"], 1) == 1


def test_quiet_ply_respects_the_ply_limit() -> None:
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    assert quiet_ply(board, ["e4d5", "d8d5", "b1c3"], 1, limit=1) is None


# --- Threshold and sign rules ---------------------------------------------------------


def test_threshold_per_kind() -> None:
    config = replace(FACTOR_CONFIG, thresholds={"group": 0.30, "term": 0.15}, overrides={})
    played = {("group", "King"): -0.29, ("term", "King danger"): -0.15}
    changes = passed_changes(played, {}, -100, "before", config)
    assert [(c.kind, c.key, c.change_pawns) for c in changes] == [("term", "king_attack", -0.15)]
    played = {("group", "King"): -0.30}
    assert [c.name for c in passed_changes(played, {}, -100, "best", config)] == ["king safety"]


def test_an_override_replaces_the_kind_threshold() -> None:
    played = {("group", "King"): -0.74, ("group", "Space"): -0.10}
    assert [c.key for c in passed_changes(played, {}, -100, "before")] == ["space"]
    played = {("group", "King"): -0.75}
    assert [c.key for c in passed_changes(played, {}, -100, "before")] == ["king_safety"]


def test_a_factor_disagreeing_in_sign_with_the_engine_is_no_fact() -> None:
    played = {("group", "King"): 0.9, ("group", "Mobility"): -0.5}
    changes = passed_changes(played, {}, -300, "before")
    # King safety rose while the engine says the move lost: the engine judges, no fact.
    assert [c.key for c in changes] == ["mobility"]
    assert passed_changes(played, {}, 0, "before") == []


def test_excluded_groups_and_the_cap() -> None:
    played = {("group", name): -1.0 - index / 10 for index, name in enumerate(GROUP_NAMES)}
    changes = passed_changes(played, {}, -100, "before")
    assert len(changes) == FACTOR_CONFIG.max_changes
    assert [abs(c.change_pawns) for c in changes] == sorted(
        (abs(c.change_pawns) for c in changes), reverse=True
    )


# --- Three points per mistake -----------------------------------------------------------


def test_a_mistake_with_a_clear_factor_cause() -> None:
    facts = _facts(CLEAR_CAUSE)
    factors = facts.factors
    assert factors is not None and factors.source == FACTOR_SOURCE
    assert [p.point for p in factors.points] == ["before", "played", "best"]
    assert all(p.quiet for p in factors.points)
    played_point = factors.points[1]
    assert played_point.uci[0] == facts.position.played_move.uci
    assert factors.points[2].uci[0] == facts.position.best_move.uci  # type: ignore[union-attr]
    assert factors.engine_swing_cp is not None and factors.engine_swing_cp < 0
    king = [c for c in factors.changes if c.key == "king_safety"]
    assert {c.compared_with for c in king} == {"before", "best"}
    assert all(c.change_pawns <= -FACTOR_CONFIG.threshold("group", "King") for c in king)


def test_a_mistake_with_no_factor_cause() -> None:
    # 1.Nf3 instead of 1.Nc3, scored 50 cp worse: no factor falls by a threshold.
    factors = mistake_factor_facts(chess.Board(), ["g1f3", "g8f6"], ["b1c3", "g8f6"], -50)
    assert all(p.quiet for p in factors.points)
    assert factors.changes == ()


def test_no_quiet_position_means_no_measurement() -> None:
    factors = _facts(UNQUIET).factors
    assert factors is not None
    assert [p.quiet for p in factors.points] == [True, False, True]
    assert factors.changes == ()


@pytest.mark.parametrize("position_id", sorted(POSITIONS))
def test_every_change_agrees_with_the_engine_swing(position_id: str) -> None:
    factors = _facts(position_id).factors
    if factors is None or not factors.changes:
        return
    assert factors.engine_swing_cp is not None
    for change in factors.changes:
        assert (change.change_pawns > 0) == (factors.engine_swing_cp > 0)
        threshold = FACTOR_CONFIG.threshold(change.kind, change.guide_name)
        assert abs(change.change_pawns) >= threshold - 0.005


def test_a_flipped_engine_swing_drops_every_disagreeing_change() -> None:
    facts = _facts(CLEAR_CAUSE)
    factors = facts.factors
    assert factors is not None and factors.engine_swing_cp is not None
    board = chess.Board(facts.position.fen)
    played, best = factors.points[1].uci, factors.points[2].uci
    flipped = mistake_factor_facts(board, played, best, -factors.engine_swing_cp)
    assert all(c.change_pawns > 0 for c in flipped.changes)
    assert not {(c.key, c.compared_with) for c in flipped.changes} & {
        (c.key, c.compared_with) for c in factors.changes
    }


def test_analysis_board_line_against_line_one() -> None:
    root = chess.Board()
    best = ["e2e4", "e7e5", "g1f3"]
    worse = ["f2f3", "e7e5", "g2g4"]
    facts = line_factor_facts(root, best, worse, -200)
    assert [p.point for p in facts.points] == ["best", "line"]
    assert all(c.compared_with == "best" and c.change_pawns < 0 for c in facts.changes)


# --- Slots ---------------------------------------------------------------------------


def test_lesson_factor_slots_are_code_rendered_and_labelled() -> None:
    from scan64.explanations.slots import lesson_slot_catalogue

    facts = _facts(CLEAR_CAUSE)
    slots = lesson_slot_catalogue(facts).slots_for(facts.evidence[0].evidence_id)
    factor = {name: slot for name, slot in slots.items() if name.startswith("factor:")}
    assert factor
    assert len(factor) == len(facts.factors.changes)  # type: ignore[union-attr]
    king = factor["factor:king_safety:best"]
    change = next(
        c
        for c in facts.factors.changes  # type: ignore[union-attr]
        if (c.key, c.compared_with) == ("king_safety", "best")
    )
    assert king.value == (
        f"after your move, king safety is {abs(change.change_pawns):.2f} pawns worse for you "
        "than after the best move"
    )
    before = next(c for c in facts.factors.changes if c.compared_with == "before")  # type: ignore[union-attr]
    assert factor[f"factor:{before.key}:played"].value == (
        f"after your move, {before.name} fell by {abs(before.change_pawns):.2f} pawns for you"
    )
    assert king.source == FACTOR_SOURCE
    assert all(slot.source == FACTOR_SOURCE for slot in factor.values())


def test_no_factor_slot_without_a_passed_change() -> None:
    from scan64.explanations.slots import lesson_slot_catalogue

    catalogue = lesson_slot_catalogue(_no_cause_facts())
    assert not any(name.startswith("factor:") for name in catalogue.names())


def test_analysis_board_factor_slots_name_their_line() -> None:
    from scan64.chess.analysis.models import EngineAnalysis
    from scan64.explanations.board_lines import (
        board_slot_catalogue,
        build_board_lines_facts,
        scored_legal_lines,
    )

    recording = json.loads(
        (Path(__file__).parents[1] / "fixtures/analysis_board_lines.json").read_text("utf-8")
    )
    entry = recording["positions"]["en_passant"]
    board = chess.Board(entry["fen"])
    analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
    facts = build_board_lines_facts(board, scored_legal_lines(board, analysis)[:3])
    assert facts.lines[0].factors is None
    catalogue = board_slot_catalogue(facts)
    for line in facts.lines[1:]:
        assert line.factors is not None and line.factors.source == FACTOR_SOURCE
        slots = catalogue.slots_for(line.line_id)
        for change in line.factors.changes:
            slot = slots[f"factor:{change.key}:{line.line_id}"]
            assert slot.value.startswith(f"in line {line.rank}, {change.name} is ")
            assert slot.value.endswith("for the side to move than in line 1")
            assert slot.source == FACTOR_SOURCE
    assert any(line.factors and line.factors.changes for line in facts.lines[1:])


# --- Prompt -----------------------------------------------------------------------------


def test_the_prompt_lists_the_factor_slots_and_names_causes_only_through_them() -> None:
    from scan64.explanations.assembly import _explanation_request
    from scan64.explanations.slots import FACTOR_SLOT_SUFFIX

    facts = _facts(CLEAR_CAUSE)
    system, slots = _explanation_request(facts).messages
    assert "Name a cause only through a {factor:...} slot" in system.content
    assert "With no factor slot, name no cause." in system.content
    for change in facts.factors.changes:  # type: ignore[union-attr]
        suffix = FACTOR_SLOT_SUFFIX[change.compared_with]
        assert f"{{factor:{change.key}:{suffix}}} = after your move, {change.name} " in (
            slots.content
        )
    assert f"come from {FACTOR_SOURCE}." in slots.content
    assert "king" in slots.content.split("Allowed plain words: ")[1]
    none = _explanation_request(_no_cause_facts()).messages[1].content
    assert "{factor:" not in none and FACTOR_SOURCE not in none
    assert "No factor slot: no cause was measured, so name no cause." in none


def test_a_slot_claim_without_a_line_cites_the_line_of_its_slots() -> None:
    from chess_lesson_spec import ExplanationClaim

    from scan64.explanations.assembly import grounding_context_for
    from scan64.explanations.validator import validate_generated_explanation
    from scan64.providers.llm import GeneratedExplanation

    item = POSITIONS[CLEAR_CAUSE]
    context = grounding_context_for(
        Diagnosis(**item["diagnosis"]), [Evidence(**e) for e in item["evidence"]], item["fen"]
    )
    evidence_id = context.evidence[0].evidence_id
    verified = context.verified_lines[evidence_id]
    claims = (
        ExplanationClaim(text="See {line:L1}.", evidence_ref=evidence_id, disclosure_level=1),
        ExplanationClaim(text="Keep calm.", evidence_ref=evidence_id, disclosure_level=1),
    )
    explanation = validate_generated_explanation(GeneratedExplanation(claims=claims), context)
    assert explanation.claims[0].line in verified
    assert explanation.claims[1].line == ()


def test_a_board_comparison_claim_is_linked_through_its_slots() -> None:
    from chess_lesson_spec import ExplanationClaim

    from scan64.chess.analysis.models import EngineAnalysis
    from scan64.explanations.board_lines import (
        COMPARISON_EVIDENCE_ID,
        _validated_provider_explanation,
        build_board_lines_facts,
        claim_line_id,
        scored_legal_lines,
    )
    from scan64.providers.llm import GeneratedExplanation

    entry = json.loads(
        (Path(__file__).parents[1] / "fixtures/analysis_board_lines.json").read_text("utf-8")
    )["positions"]["start"]
    board = chess.Board(entry["fen"])
    analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
    facts = build_board_lines_facts(board, scored_legal_lines(board, analysis)[:3])
    claim = ExplanationClaim(
        text="{label:line-2} scores {diff:line-2}.",
        evidence_ref=COMPARISON_EVIDENCE_ID,
        disclosure_level=1,
    )
    [linked] = _validated_provider_explanation(GeneratedExplanation(claims=(claim,)), facts).claims
    assert linked.line == facts.lines[1].uci[:1]
    assert claim_line_id(linked, facts) == "line-2"

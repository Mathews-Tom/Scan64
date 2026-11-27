"""Slot narration: the model writes prose, code writes every chess token (#386).

A claim on the slot path passes only if every slot names a fact listed for its
evidence item and the prose outside the slots holds no chess content. The
rendered text holds the code-rendered slot values.
"""

from __future__ import annotations

import dataclasses
import random
from typing import Any

import chess
import pytest
from chess_lesson_spec import Diagnosis, ExplanationClaim

from scan64.explanations import assembly
from scan64.explanations.assembly import resolve_explanation
from scan64.explanations.board_lines import COMPARISON_EVIDENCE_ID, board_slot_grounding
from scan64.explanations.facts import build_narrator_facts
from scan64.explanations.slots import (
    CHESS_PROSE,
    SlotCatalogue,
    lesson_slot_catalogue,
    numbered_san,
    slot_names,
)
from scan64.explanations.validator import (
    REJECTION_REASONS,
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    validate_generated_explanation,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import ExplanationRequest, GeneratedExplanation
from tests.unit.test_board_lines import POSITIONS
from tests.unit.test_board_lines import _facts as _board_facts
from tests.unit.test_claim_text_grounding import ADVERSARIAL, CORRECT
from tests.unit.test_narrator_facts import _FEN, _missed_capture


def _context() -> tuple[GroundedExplanationContext, str]:
    diagnosis, evidence = _missed_capture()
    facts = build_narrator_facts(diagnosis, evidence, _FEN)
    return assembly._grounding_context(facts, evidence, _FEN), facts.evidence[0].evidence_id


def _claim(text: str, evidence_ref: str, line: tuple[str, ...] = ()) -> ExplanationClaim:
    return ExplanationClaim(text=text, evidence_ref=evidence_ref, line=line, disclosure_level=1)


def _render(text: str, line: tuple[str, ...] = ()) -> str:
    context, evidence_id = _context()
    explanation = validate_generated_explanation(
        GeneratedExplanation(claims=(_claim(text, evidence_id, line),)), context
    )
    return explanation.text


def _reason(text: str, context: GroundedExplanationContext | None = None) -> str:
    if context is None:
        context, evidence_id = _context()
    else:
        evidence_id = context.evidence[0].evidence_id
    with pytest.raises(GroundedExplanationValidationError) as raised:
        validate_generated_explanation(
            GeneratedExplanation(claims=(_claim(text, evidence_id),)), context
        )
    assert raised.value.reason in REJECTION_REASONS
    return raised.value.reason


def test_the_catalogue_renders_values_from_the_facts() -> None:
    context, evidence_id = _context()
    assert context.slots is not None
    values = {name: slot.value for name, slot in context.slots.slots_for(evidence_id).items()}
    assert values["played"] == "Nf3"
    assert values["best"] == "Bxg5"
    assert values["capture:best"] == "Bxg5 captures the black queen on g5"
    assert values["mistake"] == "Missed capture"
    assert values["eval_before"] == "+8.50 pawns"
    assert values["eval_after"] == "-0.40 pawns"
    assert values["eval_diff"] == "8.90 pawns"
    line_values = [value for name, value in values.items() if name.startswith("line:")]
    assert "3.Bxg5 Nf6 4.Bxf6" in line_values
    assert "3.Nf3 Qxg2 4.Rg1" in line_values


@pytest.mark.parametrize(
    ("text", "rendered"),
    [
        (
            "You played {played}, but {capture:best}.",
            "You played Nf3, but Bxg5 captures the black queen on g5.",
        ),
        (
            "The evaluation went from {eval_before} to {eval_after}.",
            "The evaluation went from +8.50 pawns to -0.40 pawns.",
        ),
        ("{played} cost you {eval_diff}.", "Nf3 cost you 8.90 pawns."),
        ("{mistake}: look for {best} next time.", "Missed capture: look for Bxg5 next time."),
        ("{piece:1} was there for you.", "The black queen on g5 was there for you."),
        ("Slow down and compare forcing moves.", "Slow down and compare forcing moves."),
    ],
)
def test_valid_slots_render(text: str, rendered: str) -> None:
    assert _render(text) == rendered


def test_rendered_claims_keep_their_contract_fields() -> None:
    context, evidence_id = _context()
    explanation = validate_generated_explanation(
        GeneratedExplanation(
            claims=(
                _claim("You played {played}.", evidence_id, ("g1f3",)),
                _claim("{best} was stronger.", evidence_id),
            )
        ),
        context,
    )
    assert explanation.text == "You played Nf3. Bxg5 was stronger."
    assert [claim.text for claim in explanation.claims] == ["You played Nf3.", "Bxg5 was stronger."]
    assert explanation.claims[0].line == ("g1f3",)
    assert explanation.claims[0].evidence_ref == evidence_id


@pytest.mark.parametrize(
    "text",
    [
        "{nonsense} was the best move.",
        "{best_move} was the best move.",
        "{line:L9} is the line.",
        "{move:L1:9} follows.",
        "{Played} was the mistake.",
        "You played {played.",
        "You played {played}} there.",
        "You played {} there.",
    ],
)
def test_an_unknown_or_malformed_slot_rejects(text: str) -> None:
    assert _reason(text) == "unknown_slot"


def test_a_slot_of_another_evidence_item_rejects() -> None:
    context, evidence_id = _context()
    assert context.slots is not None
    other = dict(context.slots.slots_for(evidence_id))
    foreign = {"material_gain": other.pop("material_gain")}
    slots = SlotCatalogue(by_evidence={evidence_id: other, "ev_other": foreign})
    assert _reason("That left {material_gain}.", dataclasses.replace(context, slots=slots)) == (
        "slot_not_in_evidence"
    )


@pytest.mark.parametrize(
    "text",
    [
        # Raw squares, moves (SAN, UCI, castling), move numbers.
        "Your move left g5 alone.",
        "You played {played}; Bxg5 was better.",
        "You played {played}; c1g5 was better.",
        "Castling with O-O was safer.",
        # Pieces, piece-on-square, colours.
        "Your bishop could have helped.",
        "The queen was loose after {played}.",
        "{best} lets White keep the extra piece.",
        "Black is in trouble after {played}.",
        # Evaluations and mate counts.
        "{played} costs 8.9 pawns.",
        "The position is +150 cp.",
        "{best} leads to mate in 2.",
        # Check, mate, capture, tactic, threat and material words.
        "{best} gives check.",
        "{best} is checkmate.",
        "{best} captures with tempo.",
        "{best} takes everything.",
        "{best} is a fork.",
        "{best} sets up a pin.",
        "{best} creates a threat.",
        "{best} attacks two targets.",
        "Your piece was hanging.",
        "{best} wins material.",
        "You lose time after {played}.",
        "You were ahead before {played}.",
        "The game is equal after {played}.",
        "Open the f-file next time.",
    ],
)
def test_chess_content_in_the_prose_rejects(text: str) -> None:
    assert _reason(text) == "chess_token_outside_slot"


@pytest.mark.parametrize(
    ("text", "_"),
    [(text, reason) for _, text, reason in ADVERSARIAL]
    + [(text, "correct") for _, text in CORRECT],
)
def test_every_free_prose_grammar_text_is_rejected_on_the_slot_path(text: str, _: str) -> None:
    """The #366 and #384 suites written as free prose cannot pass a slot claim."""
    if not CHESS_PROSE.search(text):
        pytest.fail(f"free prose without chess content: {text!r}")
    assert _reason(text) == "chess_token_outside_slot"


# --- Fuzzed positions ----------------------------------------------------------------


class _Provider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requests: list[ExplanationRequest] = []

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        self.requests.append(request)
        return GeneratedExplanation(claims=(_claim(self.text, "ev_fuzz"),))


def _random_line(board: chess.Board, rng: random.Random, plies: int) -> list[str]:
    replay = board.copy(stack=False)
    line = []
    for _ in range(plies):
        moves = list(replay.legal_moves)
        if not moves:
            break
        move = rng.choice(moves)
        line.append(move.uci())
        replay.push(move)
    return line


def _fuzzed_position(rng: random.Random) -> tuple[Diagnosis, Evidence, str] | None:
    board = chess.Board()
    for _ in range(rng.randrange(0, 80)):
        moves = list(board.legal_moves)
        if not moves:
            return None
        board.push(rng.choice(moves))
    moves = list(board.legal_moves)
    if len(moves) < 2:
        return None
    played, best = rng.sample(moves, 2)
    after = board.copy(stack=False)
    after.push(played)
    fast_line = [best.uci(), *_random_line(_pushed(board, best), rng, rng.randrange(0, 10))]
    payload: dict[str, Any] = {
        "played_move": board.san(played),
        "before_fen": board.fen(),
        "after_fen": after.fen(),
        "best_move": best.uci(),
        "fast_line": fast_line,
        "fast_score_cp": rng.randrange(-900, 900),
        "reply_score_cp": rng.randrange(-900, 900),
        "swing_cp": rng.randrange(0, 900),
    }
    focused = _random_line(after, rng, rng.randrange(0, 10))
    if focused:
        payload["focused_multipv"] = [{"pv": focused, "score_mate": rng.choice([-3, 2])}]
    evidence = Evidence(
        evidence_id="ev_fuzz",
        kind="fixture",
        position_id="pos_fuzz",
        engine_analysis_id="ea_fuzz",
        claim="fuzzed evidence",
        payload=payload,
    )
    diagnosis = Diagnosis(
        primary="threat_processing.missed_check", confidence=0.9, evidence_refs=["ev_fuzz"]
    )
    return diagnosis, evidence, board.fen()


def _pushed(board: chess.Board, move: chess.Move) -> chess.Board:
    after = board.copy(stack=False)
    after.push(move)
    return after


@pytest.mark.asyncio
async def test_slots_render_and_the_template_floor_holds_on_3000_fuzzed_positions() -> None:
    rng = random.Random(386)
    checked = 0
    while checked < 3000:
        fuzzed = _fuzzed_position(rng)
        if fuzzed is None:
            continue
        diagnosis, evidence, fen = fuzzed
        facts = build_narrator_facts(diagnosis, [evidence], fen)
        catalogue = lesson_slot_catalogue(facts)
        slots = catalogue.slots_for("ev_fuzz")
        text = " ".join(f"{{{name}}}." for name in slots)
        assert slot_names(text) == list(slots)
        context = assembly._grounding_context(facts, [evidence], fen)
        rendered = validate_generated_explanation(
            GeneratedExplanation(claims=(_claim(text, "ev_fuzz"),)), context
        ).text
        assert "{" not in rendered and "}" not in rendered
        for slot in slots.values():
            assert slot.value
            assert slot.value.lower() in rendered.lower()
        if True:
            template = await resolve_explanation(diagnosis, [evidence], fen, environment={})
            provider = _Provider(f"{slots['played'].value} was the mistake.")
            explanation = await resolve_explanation(
                diagnosis, [evidence], fen, local_provider=provider, environment={}
            )
            assert explanation == template
            assert len(provider.requests) == 1
        checked += 1


# --- Analysis Board ------------------------------------------------------------------


@pytest.mark.parametrize("position", sorted(POSITIONS))
def test_every_board_slot_renders_on_every_recorded_position(position: str) -> None:
    facts = _board_facts(position, 3)
    context = board_slot_grounding(facts)
    assert context.slots is not None
    for evidence_id, slots in context.slots.by_evidence.items():
        text = " ".join(f"{{{name}}}." for name in slots)
        line = facts.lines[0].uci[:1] if evidence_id == COMPARISON_EVIDENCE_ID else ()
        rendered = validate_generated_explanation(
            GeneratedExplanation(claims=(_claim(text, evidence_id, line),)), context
        ).text
        assert "{" not in rendered
        for slot in slots.values():
            assert slot.value.lower() in rendered.lower()
    by_line = context.slots.slots_for("line-1")
    assert by_line["label:line-1"].value == "line 1"
    assert by_line["first:line-1"].value == numbered_san(
        chess.Board(facts.position.fen), facts.lines[0].uci[:1]
    )
    assert "line:line-2" not in by_line

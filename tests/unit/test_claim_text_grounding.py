"""Claim text may state only chess facts its cited evidence or board proves (#366).

Each scenario is a real position with evidence payloads shaped like
``learning/evidence/composer.py`` output. Adversarial texts imitate LLM
inventions; correct texts state only what the payload and boards prove.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import chess
import pytest
from chess_lesson_spec import Diagnosis, ExplanationClaim

from scan64.explanations import assembly
from scan64.explanations.assembly import resolve_explanation, template_fallback_counts
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.explanations.validator import (
    EVALUATION_TOLERANCE_CP,
    REJECTION_REASONS,
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    validate_generated_explanation,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import ExplanationRequest, GeneratedExplanation, LLMProviderError


@dataclass(frozen=True)
class Scenario:
    fen: str
    evidence: Evidence
    verified_lines: tuple[tuple[str, ...], ...]


def _after(fen: str, san: str) -> str:
    board = chess.Board(fen)
    board.push_san(san)
    return board.fen()


def _scenario(evidence_id: str, fen: str, played: str, payload: dict[str, Any]) -> Scenario:
    full_payload = {
        "played_move": played,
        "before_fen": fen,
        "after_fen": _after(fen, played),
        **payload,
    }
    lines = tuple(
        tuple(full_payload[name]) for name in ("fast_line",) if name in full_payload
    ) + tuple((full_payload[name],) for name in ("best_move",) if name in full_payload)
    return Scenario(
        fen=fen,
        evidence=Evidence(
            evidence_id=evidence_id,
            kind="fixture",
            position_id="pos_fixture",
            engine_analysis_id="ea_fixture",
            claim="fixture evidence",
            payload=full_payload,
        ),
        verified_lines=lines,
    )


# 1.e4 e5 2.d3 Qg5: White missed Bxg5 and played Nf3.
MISSED_CAPTURE = _scenario(
    "ev_capture",
    "rnb1kbnr/pppp1ppp/8/4p1q1/4P3/3P4/PPP2PPP/RNBQKBNR w KQkq - 1 3",
    "Nf3",
    {
        "missed_type": "capture",
        "best_move": "c1g5",
        "fast_line": ["c1g5"],
        "captured_square": "g5",
        "captured_piece": "q",
        "material_gain": 9,
        "exchange_gain": 9,
        "swing_cp": 850,
        "focused_line": ["g5g6"],
        "focused_multipv": [{"pv": ["g5g6"], "score_cp": -120}],
    },
)

# 1.f3 e5 2.g4?? allows Qh4#.
ALLOWED_MATE = _scenario(
    "ev_mate",
    "rnbqkbnr/pppp1ppp/8/4p3/8/5P2/PPPPP1PP/RNBQKBNR w KQkq - 0 2",
    "g4",
    {
        "blunder_type": "allowed_mate",
        "mating_line": ["d8h4"],
        "proving_line": ["d8h4"],
        "mate_in": 1,
        "after_score_mate": 1,
        "swing_cp": 10000,
    },
)

# 1.e4 e5 2.Ba6?? hangs the bishop to bxa6.
HANGING_PIECE = _scenario(
    "ev_hanging",
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    "Ba6",
    {
        "blunder_type": "hanging_piece_lost",
        "hanging_square": "a6",
        "hanging_piece": "B",
        "proving_line": ["b7a6"],
        "threat_move": "b7a6",
        "material_gain": 3,
        "exchange_gain": 3,
        "swing_cp": 300,
    },
)


def _validate(scenario: Scenario, text: str) -> None:
    context = GroundedExplanationContext(
        fen=scenario.fen,
        evidence=(scenario.evidence,),
        verified_lines={scenario.evidence.evidence_id: scenario.verified_lines},
        requested_hint_level=3,
    )
    generated = GeneratedExplanation(
        claims=(
            ExplanationClaim(
                text=text,
                evidence_ref=scenario.evidence.evidence_id,
                disclosure_level=1,
            ),
        )
    )
    validate_generated_explanation(generated, context)


CORRECT = [
    (MISSED_CAPTURE, "You played Nf3, but Bxg5 captures the queen on g5."),
    (MISSED_CAPTURE, "The engine line begins with c1g5, winning the black queen on g5."),
    (MISSED_CAPTURE, "White to move wins material: the bishop on c1 takes the queen."),
    (MISSED_CAPTURE, "Missing it costs about 8.5 pawns."),
    (MISSED_CAPTURE, "The swing is 850 centipawns."),
    (MISSED_CAPTURE, "Your bishop to g5 wins the queen."),
    (MISSED_CAPTURE, "A swing of 8.4 pawns is within the stated tolerance."),
    (ALLOWED_MATE, "After g4, Black replies Qh4#, mate in 1."),
    (ALLOWED_MATE, "Your move g4 allows the opponent's queen to h4 with checkmate."),
    (ALLOWED_MATE, "It is White's turn, and g2g4 allows mate in one."),
    (ALLOWED_MATE, "The king on e1 is checkmated after the queen move."),
    (HANGING_PIECE, "After Ba6, your bishop on a6 is left hanging: bxa6 wins it."),
    (HANGING_PIECE, "The b7 pawn captures on a6 and wins a bishop worth 3 pawns."),
    (HANGING_PIECE, "Check the opponent's captures before moving."),
]

ADVERSARIAL = [
    # Invented moves: legal but in no verified line or payload field.
    (MISSED_CAPTURE, "Qh5 was the best move.", "move_not_verified"),
    (ALLOWED_MATE, "Black should answer with Qg5.", "move_not_verified"),
    (MISSED_CAPTURE, "Your knight to c3 was stronger.", "move_not_verified"),
    # Illegal moves in text.
    (MISSED_CAPTURE, "Nxd5 wins a pawn.", "illegal_move"),
    (MISSED_CAPTURE, "e1e3 secures the king.", "illegal_move"),
    # Wrong piece, wrong square, wrong colour.
    (MISSED_CAPTURE, "The knight on c1 takes the queen.", "piece_not_on_square"),
    (MISSED_CAPTURE, "The queen on h4 was the target.", "piece_not_on_square"),
    (HANGING_PIECE, "The black bishop on a6 is hanging.", "piece_not_on_square"),
    (MISSED_CAPTURE, "Your queen on g5 is attacked.", "piece_not_on_square"),
    (MISSED_CAPTURE, "The weakness on f7 decides.", "square_not_in_evidence"),
    # Invented evaluations.
    (MISSED_CAPTURE, "Missing Bxg5 costs 3.2 pawns.", "evaluation_not_supported"),
    (MISSED_CAPTURE, "The position is +150 cp for Black.", "evaluation_not_supported"),
    (MISSED_CAPTURE, "Bxg5 leads to mate in 2.", "mate_not_supported"),
    (ALLOWED_MATE, "Qh4# is mate in 2.", "mate_not_supported"),
    # Wrong side to move.
    (MISSED_CAPTURE, "Black to move can save the queen.", "wrong_side_to_move"),
    (MISSED_CAPTURE, "It is Black's turn here.", "wrong_side_to_move"),
    (ALLOWED_MATE, "White replies Qh4#.", "wrong_side_to_move"),
    # Invented material, checks, tactics and move types.
    (HANGING_PIECE, "bxa6 wins the queen.", "material_not_supported"),
    (HANGING_PIECE, "bxa6 wins a pawn.", "material_not_supported"),
    (MISSED_CAPTURE, "Bxg5+ wins the queen.", "check_not_supported"),
    (MISSED_CAPTURE, "Bxg5 is a fork.", "tactic_not_supported"),
    (MISSED_CAPTURE, "Bxg5 is a back-rank tactic.", "tactic_not_supported"),
    (MISSED_CAPTURE, "Bxg5 is a back rank tactic.", "tactic_not_supported"),
    (MISSED_CAPTURE, "Castling was safer.", "move_type_not_supported"),
    # Review of #380: texts that crashed the validator or slipped past it.
    (MISSED_CAPTURE, "The bishop can capture the queen on G5.", "unparsed_chess_token"),
    (MISSED_CAPTURE, "The knight wins the queen on G5.", "unparsed_chess_token"),
    (MISSED_CAPTURE, "Play e4e4 now.", "illegal_move"),
    (MISSED_CAPTURE, "The learner is up a rook.", "material_not_supported"),
    (MISSED_CAPTURE, "The side to move is up a rook.", "material_not_supported"),
    (MISSED_CAPTURE, "The learner's queen on g5 is attacked.", "piece_not_on_square"),
    (MISSED_CAPTURE, "The side to move's queen on g5 is attacked.", "piece_not_on_square"),
    (MISSED_CAPTURE, "The evaluation drops to 7.4.", "evaluation_not_supported"),
    (MISSED_CAPTURE, "The evaluation is –2.3.", "evaluation_not_supported"),
    (MISSED_CAPTURE, "Bxg5, scored 0.41, keeps the queen safe.", "evaluation_not_supported"),
    # Chess tokens outside the grammar.
    (MISSED_CAPTURE, "White is completely winning here.", "unparsed_chess_token"),
    (MISSED_CAPTURE, "Play Ng1-f3 now.", "unparsed_chess_token"),
]


@pytest.mark.parametrize(
    "text",
    [
        "The learner's opponent has a queen on g5.",
        "The swing is 8.50.",
        "The side to move can win the queen.",
    ],
)
def test_prompt_side_names_and_unsigned_evaluations_pass_when_grounded(text: str) -> None:
    _validate(MISSED_CAPTURE, text)


@pytest.mark.parametrize(("scenario", "text"), CORRECT)
def test_grounded_claim_text_passes(scenario: Scenario, text: str) -> None:
    _validate(scenario, text)


@pytest.mark.parametrize(("scenario", "text", "reason"), ADVERSARIAL)
def test_ungrounded_claim_text_is_rejected(scenario: Scenario, text: str, reason: str) -> None:
    with pytest.raises(GroundedExplanationValidationError) as raised:
        _validate(scenario, text)
    assert raised.value.reason == reason
    assert raised.value.reason in REJECTION_REASONS


def test_fixture_sets_meet_the_issue_minimums() -> None:
    assert len(ADVERSARIAL) >= 15
    assert len(CORRECT) >= 10


def test_evaluation_tolerance_boundary() -> None:
    within = (850 - EVALUATION_TOLERANCE_CP) / 100
    _validate(MISSED_CAPTURE, f"The swing is {within:.2f} pawns.")
    with pytest.raises(GroundedExplanationValidationError) as raised:
        _validate(MISSED_CAPTURE, f"The swing is {within - 0.01:.2f} pawns.")
    assert raised.value.reason == "evaluation_not_supported"


def test_text_without_chess_tokens_passes() -> None:
    _validate(MISSED_CAPTURE, "Slow down and compare forcing moves before you commit.")


def test_claim_text_is_checked_against_its_own_evidence_only() -> None:
    other = MISSED_CAPTURE.evidence.model_copy(
        update={"evidence_id": "ev_other", "payload": {"swing_cp": 850}}
    )
    context = GroundedExplanationContext(
        fen=MISSED_CAPTURE.fen,
        evidence=(MISSED_CAPTURE.evidence, other),
        verified_lines={"ev_capture": MISSED_CAPTURE.verified_lines},
        requested_hint_level=3,
    )
    generated = GeneratedExplanation(
        claims=(
            ExplanationClaim(
                text="Bxg5 wins the queen.", evidence_ref="ev_other", disclosure_level=1
            ),
        )
    )
    with pytest.raises(GroundedExplanationValidationError) as raised:
        validate_generated_explanation(generated, context)
    assert raised.value.reason == "move_not_verified"


class _FixedProvider:
    def __init__(self, text: str | None) -> None:
        self._text = text

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        if self._text is None:
            raise LLMProviderError("fixture unavailable")
        return GeneratedExplanation(
            claims=(
                ExplanationClaim(
                    text=self._text,
                    evidence_ref=MISSED_CAPTURE.evidence.evidence_id,
                    disclosure_level=1,
                ),
            )
        )


def _diagnosis() -> Diagnosis:
    return Diagnosis(
        primary="threat_processing.missed_capture",
        confidence=0.9,
        evidence_refs=[MISSED_CAPTURE.evidence.evidence_id],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "reason"),
    # The pipeline checks claim text on the slot path (#386).
    [
        ("Qh5 was the best move.", "chess_token_outside_slot"),
        ("{nonsense} was the best move.", "unknown_slot"),
        (None, "provider_error"),
    ],
)
async def test_template_fallback_is_logged_and_counted_by_reason(
    text: str | None, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    before = template_fallback_counts().get(reason, 0)
    # A rejected generation falls back to the template, not to the phrase bank (#461).
    template = await TemplateExplanationProvider().explain(_diagnosis(), [MISSED_CAPTURE.evidence])
    with caplog.at_level(logging.INFO, logger=assembly.__name__):
        explanation = await resolve_explanation(
            _diagnosis(),
            [MISSED_CAPTURE.evidence],
            MISSED_CAPTURE.fen,
            local_provider=_FixedProvider(text),
            environment={},
        )
    assert explanation == template
    assert template_fallback_counts()[reason] == before + 1
    records = [
        r for r in caplog.records if getattr(r, "event", None) == "explanation_template_fallback"
    ]
    assert [getattr(record, "reason", None) for record in records] == [reason]
    for record in records:
        assert "ea_fixture" not in record.getMessage()
        assert "g5" not in record.getMessage()


@pytest.mark.asyncio
async def test_grounded_generation_is_not_counted_as_fallback() -> None:
    before = template_fallback_counts()
    explanation = await resolve_explanation(
        _diagnosis(),
        [MISSED_CAPTURE.evidence],
        MISSED_CAPTURE.fen,
        local_provider=_FixedProvider("You played {played}, but {capture:best}."),
        environment={},
    )
    assert explanation.text == "You played Nf3, but Bxg5 captures the black queen on g5."
    assert template_fallback_counts() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        (MISSED_CAPTURE, "threat_processing.missed_capture"),
        (HANGING_PIECE, "board_awareness.hanging_piece"),
    ],
)
async def test_template_wording_satisfies_the_claim_grammar(scenario: Scenario, code: str) -> None:
    """The deterministic floor's own sentences must pass the grammar it backs up."""
    template = await TemplateExplanationProvider().explain(
        Diagnosis(primary=code, confidence=0.9, evidence_refs=[scenario.evidence.evidence_id]),
        [scenario.evidence],
    )
    _validate(scenario, template.text)

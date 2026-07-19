from __future__ import annotations

from scan64.explanations.claims import ExplanationClaim
from scan64.explanations.validator import GroundedExplanationContext, validate_generated_explanation
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm.contracts import GeneratedExplanation

_STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _evidence() -> Evidence:
    return Evidence(
        evidence_id="ev_1",
        kind="engine_line",
        position_id="position_1",
        engine_analysis_id="analysis_1",
        claim="e4 is a verified candidate move",
    )


def test_validator_accepts_evidence_referenced_verified_claim() -> None:
    generated = GeneratedExplanation(
        claims=(
            ExplanationClaim(
                text="The verified move develops central control.",
                evidence_ref="ev_1",
                line=("e2e4",),
                certainty="observed",
                disclosure_level=1,
            ),
        ),
    )
    context = GroundedExplanationContext(
        fen=_STARTING_FEN,
        evidence=(_evidence(),),
        verified_lines={"ev_1": (("e2e4", "e7e5"),)},
        requested_hint_level=1,
    )

    explanation = validate_generated_explanation(generated, context)

    assert explanation.text == "The verified move develops central control."
    assert explanation.claims[0].evidence_ref == "ev_1"

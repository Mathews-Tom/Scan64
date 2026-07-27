"""Conformance test: every seeded taxonomy code must render a grounded explanation.

M33's production evidence composer (``learning/evidence/composer.py``) and its
detectors (``learning/diagnosis/detectors/*.py``) emit a provenance-bearing,
code-specific payload for each of the ten ``SEED_CODES`` taxonomy entries. M35
requires that ``TemplateExplanationProvider`` render an explanation naming the
concrete square, piece, move, or line drawn from that payload for every one of
those ten codes, with no code falling back to a generic sentence.

A missing template or a required evidence field a code's template needs must
fail this test, not silently render a fallback -- see
``test_missing_template_and_missing_field_fail_loudly`` below.
"""

from __future__ import annotations

from typing import Any

import pytest
from chess_lesson_spec import Diagnosis

from scan64.explanations.templates.provider import (
    ExplanationTemplateError,
    TemplateExplanationProvider,
)
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.evidence.models import Evidence

# Representative payloads drawn from the real shapes M33's composer emits for
# each seeded code (see learning/evidence/composer.py and
# learning/diagnosis/detectors/*.py). The values are fixtures, not invented
# facts: every field name here is one M33 actually populates for this code.
_FIXTURE_PAYLOADS: dict[str, dict[str, Any]] = {
    "board_awareness.hanging_piece": {
        "blunder_type": "hanging_piece_lost",
        "hanging_square": "e5",
        "hanging_piece": "n",
        "is_hanging_piece_blunder": True,
        "played_move": "Rd8",
        "fast_analysis_id": "fa_1",
        "focused_analysis_id": "foc_1",
        "focused_multipv": [],
        "focused_line": ["d4e5"],
        "swing_cp": 220,
    },
    "threat_processing.missed_check": {
        "missed_type": "check",
        "best_move": "d1h5",
        "was_unique_best": True,
        "played_move": "Nf3",
        "fast_analysis_id": "fa_2",
        "focused_analysis_id": "foc_2",
        "focused_multipv": [],
        "focused_line": [],
        "swing_cp": 320,
    },
    "threat_processing.missed_capture": {
        "missed_type": "capture",
        "best_move": "f3d4",
        "captured_square": "d4",
        "captured_piece": "n",
        "was_only_winning_line": True,
        "played_move": "Bb5",
        "fast_analysis_id": "fa_3",
        "focused_analysis_id": "foc_3",
        "focused_multipv": [],
        "focused_line": [],
        "swing_cp": 250,
    },
    "threat_processing.missed_direct_threat": {
        "blunder_type": "missed_direct_threat",
        "threat_move": "c4f7",
        "threatened_square": "f7",
        "threatened_piece": "p",
        "opponent_executed_threat": True,
        "played_move": "O-O",
        "fast_analysis_id": "fa_4",
        "focused_analysis_id": "foc_4",
        "focused_multipv": [],
        "focused_line": [],
        "swing_cp": 300,
    },
    "tactics.fork.knight": {
        "tactic_type": "knight_fork",
        "fork_square": "e7",
        "targets": [
            {"square": "d5", "piece": "q"},
            {"square": "f5", "piece": "r"},
        ],
        "results_in_material_gain": True,
        "played_move": "Bc4",
        "best_move": "g5e7",
        "fast_analysis_id": "fa_5",
        "focused_analysis_id": "foc_5",
        "focused_multipv": [],
        "focused_line": [],
        "swing_cp": 260,
    },
    "tactics.pin": {
        "tactic_type": "pin",
        "pinned_square": "e7",
        "pinned_piece": "n",
        "pinning_move": "f1b5",
        "wins_material": True,
        "pinning_color": "white",
        "played_move": "d4d5",
        "best_move": "f1b5",
        "fast_analysis_id": "fa_6",
        "focused_analysis_id": "foc_6",
        "focused_multipv": [],
        "focused_line": [],
        "swing_cp": 210,
    },
    "tactics.overloaded_defender": {
        "tactic_type": "overloaded_defender",
        "defender_square": "d7",
        "defender_piece": "q",
        "defended_targets": [
            {"square": "d8", "piece": "r"},
            {"square": "b7", "piece": "p"},
        ],
        "wins_material": True,
        "played_move": "Rfd1",
        "best_move": "d1d7",
        "fast_analysis_id": "fa_7",
        "focused_analysis_id": "foc_7",
        "focused_multipv": [],
        "focused_line": [],
        "swing_cp": 230,
    },
    "calculation.stopped_too_early": {
        "error_type": "stopped_early",
        "sequence_plies": 3,
        "sharp_eval_swing": True,
        "played_move": "Qd2",
        "fast_analysis_id": "fa_8",
        "focused_analysis_id": "foc_8",
        "focused_multipv": [],
        "focused_line": ["d2d7", "e8d7", "c3d5"],
        "swing_cp": 240,
    },
    "opening.delayed_development": {
        "issue": "delayed_development",
        "tempo_loss": 2.5,
        "pawn_moves": 5,
        "repeated_piece_moves": 1,
        "minor_pieces_developed": 1,
        "played_move": "a2a3",
        "fast_analysis_id": "fa_9",
        "focused_analysis_id": "foc_9",
        "focused_multipv": [],
        "focused_line": [],
        "swing_cp": 0,
    },
    "positional.king_safety_neglect": {
        "issue": "king_safety_neglect",
        "incoming_threat": "d8h4",
        "played_move": "g2g3",
        "fast_analysis_id": "fa_10",
        "focused_analysis_id": "foc_10",
        "focused_multipv": [],
        "focused_line": [],
        "swing_cp": 150,
    },
}

# The concrete, literal fact each code's explanation must name, drawn straight
# from the fixture payload above (a square or a UCI move/line).
_REQUIRED_LITERAL: dict[str, str] = {
    "board_awareness.hanging_piece": "e5",
    "threat_processing.missed_check": "d1h5",
    "threat_processing.missed_capture": "d4",
    "threat_processing.missed_direct_threat": "c4f7",
    "tactics.fork.knight": "e7",
    "tactics.pin": "f1b5",
    "tactics.overloaded_defender": "d7",
    "calculation.stopped_too_early": "d2d7 e8d7 c3d5",
    "opening.delayed_development": "2.5",
    "positional.king_safety_neglect": "d8h4",
}


def test_fixture_payloads_cover_every_seeded_code() -> None:
    """The fixtures above must enumerate the whole taxonomy, not a subset."""
    assert set(_FIXTURE_PAYLOADS) == set(SEED_CODES)
    assert set(_REQUIRED_LITERAL) == set(SEED_CODES)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", sorted(SEED_CODES))
async def test_every_taxonomy_code_names_its_grounded_evidence(code: str) -> None:
    fixture = Evidence(
        evidence_id=f"ev_{code}",
        kind="fixture",
        position_id="pos_fixture",
        engine_analysis_id="ea_fixture",
        claim="fixture evidence for the M35 coverage conformance test",
        payload=_FIXTURE_PAYLOADS[code],
    )
    diagnosis = Diagnosis(primary=code, confidence=1.0, evidence_refs=[fixture.evidence_id])

    explanation = await TemplateExplanationProvider().explain(diagnosis, evidence=[fixture])

    required = _REQUIRED_LITERAL[code]
    assert required in explanation.text, (
        f"{code} explanation must name {required!r} from its evidence payload, "
        f"got: {explanation.text!r}"
    )


@pytest.mark.asyncio
async def test_a_code_without_a_registered_template_fails_loudly() -> None:
    diagnosis = Diagnosis(primary="unknown.pattern", confidence=0.5, evidence_refs=[])
    with pytest.raises(ExplanationTemplateError, match="unknown.pattern"):
        await TemplateExplanationProvider().explain(diagnosis, evidence=[])


@pytest.mark.asyncio
async def test_evidence_missing_a_required_field_fails_loudly() -> None:
    incomplete_payload = dict(_FIXTURE_PAYLOADS["board_awareness.hanging_piece"])
    del incomplete_payload["hanging_square"]
    fixture = Evidence(
        evidence_id="ev_incomplete",
        kind="fixture",
        position_id="pos_fixture",
        engine_analysis_id="ea_fixture",
        claim="fixture evidence missing a required field",
        payload=incomplete_payload,
    )
    diagnosis = Diagnosis(
        primary="board_awareness.hanging_piece", confidence=1.0, evidence_refs=["ev_incomplete"]
    )
    with pytest.raises(ExplanationTemplateError, match="hanging_square"):
        await TemplateExplanationProvider().explain(diagnosis, evidence=[fixture])

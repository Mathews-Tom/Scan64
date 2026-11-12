from __future__ import annotations

import chess
import pytest
from chess_lesson_spec import Diagnosis

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.templates.provider import (
    ExplanationTemplateError,
    TemplateExplanationProvider,
)
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.models import DiagnosisCandidate
from scan64.learning.evidence.composer import (
    compose_candidate_evidence,
    exchange_material_gain,
    hanging_piece_proof,
    line_material_gain,
)
from scan64.learning.evidence.models import Evidence


def test_material_proof_accounts_for_defended_queen_and_equal_exchange() -> None:
    board = chess.Board("7k/8/5n2/8/4Q3/3P4/8/K7 b - - 0 1")
    assert board.is_attacked_by(chess.WHITE, chess.E4)
    proof = hanging_piece_proof(board, chess.WHITE, ["f6e4", "d3e4"])
    assert proof is not None and proof["hanging_square"] == "e4" and proof["material_gain"] == 6
    equal = chess.Board("4k3/8/8/3r4/3R4/3Q4/8/4K3 b - - 0 1")
    assert line_material_gain(equal, ["d5d4", "d3d4"]) == 0
    assert hanging_piece_proof(equal, chess.WHITE, ["d5d4", "d3d4"]) is None
    later = chess.Board("5r1k/8/8/8/8/8/2n5/R4RK1 b - - 0 1")
    line = ["f8f1", "g1f1", "c2a1"]
    assert line_material_gain(later, line) == 5
    assert exchange_material_gain(later, line) == 0
    assert hanging_piece_proof(later, chess.WHITE, line) is None
    assert line_material_gain(board, ["f6e4", "a1a8"]) is None
    assert line_material_gain(board, []) is None


def test_material_proof_counts_en_passant_and_promotion() -> None:
    ep = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
    assert line_material_gain(ep, ["e5d6"]) == 1
    promotion = chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    assert line_material_gain(promotion, ["a7a8q"]) == 8


def test_untrusted_detections_are_visible_to_measurement_not_to_lessons() -> None:
    evidence = Evidence(
        evidence_id="pin",
        kind="missed_tactic",
        position_id="position",
        engine_analysis_id="analysis",
        claim="untrusted pin",
        payload={"swing_cp": 200, "focused_analysis_id": "analysis", "focused_multipv": []},
    )
    candidate = DiagnosisCandidate(skill_id="tactics.pin", confidence=1, evidence_ids=["pin"])
    assert arbitrate_diagnoses([candidate], [evidence]) is None
    measured = arbitrate_diagnoses([candidate], [evidence], trusted_only=False)
    assert measured is not None and measured[1] == ()


@pytest.mark.asyncio
async def test_hanging_explanation_replays_line_instead_of_trusting_flags() -> None:
    payload = {
        "played_move": "Qe4",
        "hanging_square": "e4",
        "hanging_piece": "Q",
        "after_fen": "7k/8/5n2/8/4Q3/3P4/8/K7 b - - 0 1",
        "proving_line": ["f6h7"],
        "wins_material": True,
        "is_hanging_piece_blunder": True,
    }
    evidence = Evidence(
        evidence_id="hanging",
        kind="blunder_analysis",
        position_id="position",
        engine_analysis_id="analysis",
        claim="claimed hanging piece",
        payload=payload,
    )
    diagnosis = Diagnosis(
        primary="board_awareness.hanging_piece", confidence=1, evidence_refs=["hanging"]
    )
    with pytest.raises(ExplanationTemplateError, match="does not win"):
        await TemplateExplanationProvider().explain(diagnosis, [evidence])
    evidence.payload = {**payload, "proving_line": ["f6e4", "d3e4"]}
    explanation = await TemplateExplanationProvider().explain(diagnosis, [evidence])
    assert "net of exchanges" in explanation.text


def test_deep_output_cannot_change_fast_detector_signals() -> None:
    before = chess.Board("7k/8/5n2/8/8/3PQ3/8/K7 w - - 0 1")
    after = before.copy()
    after.push_san("Qe4")
    source = EngineAnalysis(raw_result=[{"pv": ["e3f3"], "score_cp": 0}])
    winning_reply = EngineAnalysis(raw_result=[{"pv": ["f6e4", "d3e4", "h8g8"]}])
    quiet_reply = EngineAnalysis(raw_result=[{"pv": ["f6h7"]}])
    inputs = {
        "before_board": before,
        "after_board": after,
        "history_san": ["Qe4"],
        "initial_fen": before.fen(),
        "position_id": "position",
        "fast_analysis": source,
        "reply_analysis": winning_reply,
        "played_move": "Qe4",
        "swing_cp": 300,
    }
    winning_deep = compose_candidate_evidence(**inputs, focused_analysis=winning_reply)
    quiet_deep = compose_candidate_evidence(**inputs, focused_analysis=quiet_reply)

    def signatures(items: list[Evidence]) -> set[tuple[str, str | None]]:
        return {(item.kind, item.payload.get("blunder_type")) for item in items}

    assert ("blunder_analysis", "hanging_piece_lost") in signatures(winning_deep)
    assert signatures(winning_deep) == signatures(quiet_deep)
    calculation = next(item for item in quiet_deep if item.kind == "calculation_error")
    assert calculation.payload["hypothesis_only"] is True
    assert "unvalidated" in calculation.claim
    inputs["reply_analysis"] = quiet_reply
    fast_quiet = compose_candidate_evidence(**inputs, focused_analysis=winning_reply)
    assert ("blunder_analysis", "hanging_piece_lost") not in signatures(fast_quiet)

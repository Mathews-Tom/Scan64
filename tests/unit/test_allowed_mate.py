from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import chess
import pytest
from chess_lesson_spec import AcceptedMove, Diagnosis

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.templates.provider import (
    ExplanationTemplateError,
    TemplateExplanationProvider,
    _allowed_mate_text,
)
import scan64.learning.diagnosis.arbitration as arbitration
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evidence.composer import compose_candidate_evidence, mating_line_san
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.exercises.sound_moves import sound_moves
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson

CODE = "threat_processing.allowed_mate"
CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parents[1] / "fixtures/allowed_mate.json").read_text()
)["positions"]


def evidence_for(case: dict[str, Any]) -> tuple[chess.Board, list[Evidence]]:
    before = chess.Board(case["fen"])
    after = before.copy()
    move = chess.Move.from_uci(case["move"])
    san = before.san(move)
    after.push(move)
    reply = EngineAnalysis(raw_result=case["after"])
    evidence = compose_candidate_evidence(
        before_board=before,
        after_board=after,
        history_san=[san],
        initial_fen=before.fen(),
        position_id="recorded-mate",
        fast_analysis=EngineAnalysis(raw_result=case["before"]),
        reply_analysis=reply,
        focused_analysis=reply,
        mate_source_analysis=(
            EngineAnalysis(config=case["mate_source_config"], raw_result=case["mate_source"])
            if case["mate_source"] is not None
            else None
        ),
        played_move=san,
        swing_cp=1000,
    )
    return before, evidence


async def detect(evidence: list[Evidence]) -> list[DiagnosisCandidate]:
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    opportunity = LearningOpportunity(
        opportunity_id="mate",
        game_id="recorded",
        position_id="mate",
        player_id="p",
        played_move="recorded",
        engine_eval_before=0.0,
        engine_eval_after=-10.0,
    )
    candidates = []
    for name in registry.names(kind=PluginKind.PATTERN_DETECTOR):
        detector = registry.get(kind=PluginKind.PATTERN_DETECTOR, name=name)
        assert isinstance(detector, PatternDetector)
        candidates.extend(
            await detector.detect(opportunity, evidence, PlayerContext(player_id="p"))
        )
    return candidates


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
async def test_recorded_mate_definition(case: dict[str, Any]) -> None:
    _, evidence = evidence_for(case)
    candidates = await detect(evidence)
    allowed = [candidate for candidate in candidates if candidate.skill_id == CODE]
    assert bool(allowed) == (case["expected_mate"] is not None)
    if allowed:
        payload = next(
            item.payload for item in evidence if item.evidence_id in allowed[0].evidence_ids
        )
        assert payload["mate_in"] == case["expected_mate"]
        assert mating_line_san(
            chess.Board(payload["after_fen"]), payload["mating_line"], payload["mate_in"]
        )
        selected = arbitrate_diagnoses(candidates, evidence, trusted_only=False)
        assert selected is not None and selected[0].skill_id == CODE
        assert selected[1] == ()
    if case["id"] == "material_only":
        selected = arbitrate_diagnoses(candidates, evidence, trusted_only=False)
        assert selected is not None and selected[0].skill_id in {
            "board_awareness.hanging_piece",
            "threat_processing.missed_direct_threat",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES[:3], ids=lambda case: case["id"])
async def test_both_player_colours(case: dict[str, Any]) -> None:
    def mirror_move(uci: str) -> str:
        move = chess.Move.from_uci(uci)
        return chess.Move(
            chess.square_mirror(move.from_square),
            chess.square_mirror(move.to_square),
            move.promotion,
        ).uci()

    mirrored = {
        **case,
        "fen": chess.Board(case["fen"]).mirror().fen(),
        "move": mirror_move(case["move"]),
    }
    for field in ("before", "after", "mate_source"):
        mirrored[field] = [
            {**line, "pv": [mirror_move(move) for move in line["pv"]]} for line in case[field]
        ]
    _, evidence = evidence_for(mirrored)
    assert CODE in {candidate.skill_id for candidate in await detect(evidence)}


@pytest.mark.asyncio
async def test_precedence_and_trusted_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    _, evidence = evidence_for(CASES[0])
    candidates = await detect(evidence)
    mate = next(candidate for candidate in candidates if candidate.skill_id == CODE)
    proof = next(item for item in evidence if item.evidence_id in mate.evidence_ids)
    # All three competing material claims are otherwise eligible at confidence 1.
    proof.payload.update(missed_type="capture", opponent_executed_threat=True)
    candidates = [
        mate,
        *[
            DiagnosisCandidate(skill_id=code, confidence=1.0, evidence_ids=mate.evidence_ids)
            for code in (
                "board_awareness.hanging_piece",
                "threat_processing.missed_capture",
                "threat_processing.missed_direct_threat",
            )
        ],
    ]
    selected = arbitrate_diagnoses(candidates, evidence, trusted_only=False)
    assert selected is not None and selected[0].skill_id == CODE
    monkeypatch.setattr(arbitration, "TRUSTED_CODES", TRUSTED_CODES - {CODE})
    assert arbitrate_diagnoses(candidates, evidence) is None
    monkeypatch.setattr(arbitration, "TRUSTED_CODES", TRUSTED_CODES | {CODE})
    trusted = arbitrate_diagnoses(candidates, evidence)
    assert trusted is not None and trusted[0].skill_id == CODE
    # Suppression is grounded in evidence, not detector registration.
    material_only = [candidate for candidate in candidates if candidate.skill_id != CODE]
    assert arbitrate_diagnoses(material_only, evidence, trusted_only=False) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES[:3], ids=lambda case: case["id"])
async def test_grounded_explanation_and_sound_lesson(case: dict[str, Any]) -> None:
    board, evidence = evidence_for(case)
    candidates = await detect(evidence)
    candidate = next(candidate for candidate in candidates if candidate.skill_id == CODE)
    payload = next(item.payload for item in evidence if item.evidence_id in candidate.evidence_ids)
    text = _allowed_mate_text(payload, CODE)
    assert f"forced mate in {case['expected_mate']}" in text
    assert "#" in text
    source = EngineAnalysis(raw_result=deepcopy(case["source"]))
    accepted = sound_moves(board, source, avoid_mate=True)
    assert chess.Move.from_uci(case["move"]) not in accepted
    assert accepted
    diagnosis = Diagnosis(primary=CODE, confidence=1.0, evidence_refs=candidate.evidence_ids)
    lesson = await generate_exact_replay_exercise(
        diagnosis, board.fen(), "mate", [board.san(move) for move in accepted]
    )
    assert lesson.objective.instruction == "Find a sound move that does not allow mate."
    verify_lesson(lesson, source)
    assert lesson.verification.status == "verified"
    # A mate-losing alternative remains invalid even in positions where the
    # ordinary probability tolerance could accept it alongside a large cp loss.
    losing = source.raw_result[1]
    source.raw_result[0]["score_cp"] = -10000
    assert losing["score_mate"] < 0
    assert len(sound_moves(board, source)) == 2
    assert len(sound_moves(board, source, avoid_mate=True)) == 1
    lesson.interaction.accepted_moves = [
        AcceptedMove(san=board.san(chess.Move.from_uci(losing["pv"][0])), reason="test")
    ]
    with pytest.raises(LessonVerificationError):
        verify_lesson(lesson, source)
    if CODE not in TRUSTED_CODES:
        with pytest.raises(ExplanationTemplateError, match="Untrusted"):
            await TemplateExplanationProvider().explain(diagnosis, evidence)


@pytest.mark.parametrize(
    "bad_line,bad_mate", [(["b3b4"], 1), (["b3b2"], 2), (["not-a-move"], 1), (["b3b2"], 4), ([], 1)]
)
def test_invalid_mating_claim_rejected(bad_line: list[str], bad_mate: int) -> None:
    _, evidence = evidence_for(CASES[0])
    payload = next(
        item.payload for item in evidence if item.payload.get("blunder_type") == "allowed_mate"
    )
    payload.update(mating_line=bad_line, mate_in=bad_mate, after_score_mate=bad_mate)
    with pytest.raises(ExplanationTemplateError):
        _allowed_mate_text(payload, CODE)
    proof = next(item for item in evidence if item.payload is payload)
    candidate = DiagnosisCandidate(skill_id=CODE, confidence=1.0, evidence_ids=[proof.evidence_id])
    assert arbitrate_diagnoses([candidate], evidence, trusted_only=False) is None


def test_lesson_rejects_already_forced_source() -> None:
    case = CASES[3]
    with pytest.raises(ValueError, match="already a forced mate"):
        sound_moves(
            chess.Board(case["fen"]), EngineAnalysis(raw_result=case["before"]), avoid_mate=True
        )


@pytest.mark.asyncio
async def test_deep_source_overrides_shallow_nonmate() -> None:
    # Deliberately conflicting shallow data tests which source the rule uses;
    # the deep source and reply are the recorded already-forced FEN analyses.
    case = {**CASES[3], "before": [{"pv": ["h2h4"], "score_cp": 0}]}
    _, evidence = evidence_for(case)
    assert CODE not in {candidate.skill_id for candidate in await detect(evidence)}


@pytest.mark.asyncio
async def test_missing_deep_source_cannot_claim_mate_or_suppress_material() -> None:
    _, evidence = evidence_for({**CASES[0], "mate_source": None})
    candidates = await detect(evidence)
    assert CODE not in {candidate.skill_id for candidate in candidates}
    selected = arbitrate_diagnoses(candidates, evidence, trusted_only=False)
    assert selected is not None and selected[0].skill_id != CODE

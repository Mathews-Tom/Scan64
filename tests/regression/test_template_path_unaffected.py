from __future__ import annotations

from uuid import uuid4

import pytest
from chess_lesson_spec import Diagnosis

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.verification.verifier import verify_lesson
from scan64.providers.llm.config import LLMProviderConfig, create_llm_provider


@pytest.mark.asyncio
async def test_template_lesson_generation_remains_valid_with_llm_disabled() -> None:
    provider = create_llm_provider(LLMProviderConfig(provider="template"))

    assert provider is None

    diagnosis = Diagnosis(
        primary="threat_processing.missed_capture",
        confidence=0.9,
        evidence_refs=["ev_1"],
    )
    fixture = Evidence(
        evidence_id="ev_1",
        kind="missed_tactic",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="the fast-pass principal variation exposes a tactical opportunity",
        payload={
            "missed_type": "capture",
            "captured_square": "c3",
            "captured_piece": "p",
            "played_move": "Ke2",
            "best_move": "b1c3",
        },
    )
    lesson = await generate_exact_replay_exercise(
        diagnosis=diagnosis,
        fen="r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/2pP1N2/PPP2PPP/RNBQK2R w KQkq - 1 5",
        lesson_id="les_template_regression",
        accepted_moves_san=["Nxc3"],
    )
    lesson.explanation = await TemplateExplanationProvider().explain(diagnosis, evidence=[fixture])
    verify_lesson(
        lesson,
        EngineAnalysis(position_id=uuid4(), raw_result=[{"pv": ["b1c3", "c6b4"]}]),
    )
    assert lesson.explanation is not None
    assert "c3" in lesson.explanation.text

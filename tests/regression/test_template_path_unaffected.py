from __future__ import annotations

import pytest
from chess_lesson_spec import Diagnosis

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
        primary="tactics.fork.knight",
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
            "tactic_type": "knight_fork",
            "fork_square": "c3",
            "targets": [{"square": "d1", "piece": "q"}],
            "results_in_material_gain": True,
            "played_move": "Nc3",
            "best_move": "e4c3",
        },
    )
    lesson = await generate_exact_replay_exercise(
        diagnosis=diagnosis,
        fen="r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 1 5",
        lesson_id="les_template_regression",
        best_move_san="Nc3",
    )
    lesson.explanation = await TemplateExplanationProvider().explain(
        diagnosis, evidence=[fixture]
    )
    verify_lesson(lesson)
    assert lesson.explanation is not None
    assert "c3" in lesson.explanation.text

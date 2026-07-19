from __future__ import annotations

import pytest

from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.verification.verifier import verify_lesson
from scan64.lessonspec.models import Diagnosis
from scan64.providers.llm.config import LLMProviderConfig, create_llm_provider


@pytest.mark.asyncio
async def test_template_lesson_generation_remains_valid_with_llm_disabled() -> None:
    provider = create_llm_provider(LLMProviderConfig(provider="template"))

    assert provider is None

    diagnosis = Diagnosis(
        primary="tactics.knight_fork",
        confidence=0.9,
        evidence_refs=["ev_1"],
    )
    lesson = await generate_exact_replay_exercise(
        diagnosis=diagnosis,
        fen="r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 1 5",
        lesson_id="les_template_regression",
        best_move_san="Nc3",
    )
    lesson.explanation = await TemplateExplanationProvider().explain(diagnosis)
    verify_lesson(lesson)
    assert lesson.explanation is not None
    assert "knight fork" in lesson.explanation.text

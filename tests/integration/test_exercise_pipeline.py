import pytest

from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.lessonspec.models import Diagnosis


@pytest.mark.asyncio
async def test_exact_replay_exercise_generation() -> None:
    diagnosis = Diagnosis(
        primary="tactics.knight_fork",
        confidence=0.9,
        evidence_refs=["ev_1"]
    )
    fen = "r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 1 5"

    lesson = await generate_exact_replay_exercise(
        diagnosis=diagnosis,
        fen=fen,
        lesson_id="les_test123",
        best_move_san="Nc3",
        hints=[]
    )

    assert lesson.source.kind == "player_game"
    assert lesson.source.fen == fen
    assert lesson.diagnosis.primary == "tactics.knight_fork"
    assert lesson.objective.type == "exact_replay"
    assert len(lesson.interaction.accepted_moves) > 0
    assert lesson.interaction.accepted_moves[0].san == "Nc3"


@pytest.mark.asyncio
async def test_template_explanation_provider() -> None:
    provider = TemplateExplanationProvider()

    diagnosis_fork = Diagnosis(
        primary="tactics.knight_fork",
        confidence=0.9,
        evidence_refs=[]
    )
    explanation = await provider.explain(diagnosis_fork)
    assert "knight fork" in explanation.text
    assert "forcing moves" in explanation.text

    diagnosis_unknown = Diagnosis(
        primary="unknown.pattern",
        confidence=0.5,
        evidence_refs=[]
    )
    explanation_unknown = await provider.explain(diagnosis_unknown)
    assert "scan for forcing moves" in explanation_unknown.text

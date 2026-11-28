"""The narrator tells the cause, then the effect, then the concrete line (#401).

A root-cause theme (#400) is the cause, the turning point is the effect, and the
role facts of the move and the engine line (#454) are the proof. Each sentence
is still checked against its own fact, and every fallback keeps the order.
"""

from __future__ import annotations

import re

import chess
import pytest
from chess_lesson_spec import Diagnosis, ExplanationClaim

from scan64.explanations import assembly
from scan64.explanations.assembly import lesson_role_facts, resolve_explanation
from scan64.explanations.facts import NarratorFacts, build_narrator_facts
from scan64.explanations.role_facts import RoleFact
from scan64.explanations.sentence_check import sentence_problems
from scan64.explanations.slots import lesson_slot_catalogue
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.explanations.themes import (
    THEME_SOURCE,
    GameThemeFacts,
    ThemeFacts,
    ThemePiece,
    TurningPointFacts,
    trace_from_scores,
)
from scan64.explanations.voice_pipeline import (
    CAUSE_EXAMPLES,
    CauseFact,
    CauseRole,
    cause_facts,
    cause_problems,
    cause_text,
    select_facts,
    voice_facts,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import ExplanationRequest, GeneratedExplanation
from tests.unit.test_narrator_facts import _FEN, _missed_capture
from tests.unit.test_narrator_voice_pipeline import _Writer

# The missed-capture fixture (3.Nf3?, Bxg5 was available) as a game.
_GAME = ["e2e4", "e7e5", "d2d3", "d8g5", "g1f3"]
_LINE_ROLES = ["the_move", "what_it_misses", "what_follows", "could_have_played"]

_UNSAFE_KING = ThemeFacts(
    key="unsafe_king",
    signal="King group value",
    threshold=-1.0,
    measured=(-1.2, -1.4, -1.5),
    held=(0, 2, 4),
    move_from=1,
    move_to=3,
    pieces=(
        ThemePiece(owner="learner", piece="king", square="e1"),
        ThemePiece(owner="opponent", piece="queen", square="g5"),
    ),
    files=("f",),
)
_WEAK_PAWNS = ThemeFacts(
    key="weak_pawns",
    signal="Pawns group value",
    threshold=-0.5,
    measured=(-0.6, -0.6, -0.7),
    held=(0, 2, 4),
    move_from=1,
    move_to=3,
    squares=("d3",),
    weaknesses=("backward",),
)
_PASSIVE = ThemeFacts(
    key="passive_pieces",
    signal="passive pieces",
    threshold=2,
    measured=(2, 2, 2),
    held=(0, 2, 4),
    move_from=1,
    move_to=3,
    pieces=(
        ThemePiece(owner="learner", piece="bishop", square="c1", squares=("d2",)),
        ThemePiece(owner="learner", piece="rook", square="a1"),
    ),
)
_LAGGING = ThemeFacts(
    key="lagging_development",
    signal="development gap",
    threshold=2,
    measured=(2, 3, 3),
    held=(0, 2, 4),
    move_from=1,
    move_to=3,
    pieces=(
        ThemePiece(owner="learner", piece="knight", square="b1"),
        ThemePiece(owner="learner", piece="bishop", square="c1"),
        ThemePiece(owner="opponent", piece="knight", square="b8"),
    ),
)
_TURN = TurningPointFacts(
    ply=5,
    move_number=3,
    side="white",
    san="Nf3",
    uci="g1f3",
    evaluation_before_cp=0,
    evaluation_after_cp=-850,
    evaluation_end_cp=-850,
    is_mistake=True,
)


def _game(*themes: ThemeFacts, turn: TurningPointFacts | None = _TURN) -> GameThemeFacts:
    return GameThemeFacts(
        source=THEME_SOURCE,
        mistake_ply=5,
        window=(0, 2, 4),
        themes=themes,
        turning_point=turn,
    )


def _line() -> tuple[RoleFact, ...]:
    diagnosis, evidence = _missed_capture()
    facts = lesson_role_facts(build_narrator_facts(diagnosis, evidence, _FEN))
    assert facts
    return facts


async def _voice(writer: _Writer, game: GameThemeFacts | None) -> list[str]:
    diagnosis, evidence = _missed_capture()
    facts = lesson_role_facts(build_narrator_facts(diagnosis, evidence, _FEN))
    assert facts
    voiced = await voice_facts(writer, facts, mover=chess.WHITE, played="3.Nf3", game=game)
    return [sentence.fact.role for sentence in voiced.sentences]


@pytest.mark.asyncio
async def test_with_a_theme_the_cause_and_the_turn_come_before_the_line() -> None:
    writer = _Writer()
    roles = await _voice(writer, _game(_UNSAFE_KING))
    assert roles[:2] == ["the_cause", "the_turn"]
    assert roles[2] == "the_move"
    assert roles[2:] == [role for role in _LINE_ROLES if role in roles[2:]]
    # The cause's prompt carries the cause examples; a line fact's prompt stays as measured.
    assert "Fact (The cause): From move 1 to move 3, White's king on e1" in writer.calls[0][1]
    assert CAUSE_EXAMPLES[0][2] in writer.calls[0][1]
    assert (
        "Fact (The turning point): The game began to turn against White with 3.Nf3"
        in (writer.calls[1][1])
    )
    assert CAUSE_EXAMPLES[0][2] not in writer.calls[2][1]


@pytest.mark.asyncio
async def test_without_a_theme_the_story_starts_with_the_move() -> None:
    line, _ = select_facts(_line())
    expected = [fact.role for fact in line]
    assert await _voice(_Writer(), None) == expected
    assert await _voice(_Writer(), _game(turn=None)) == expected
    # A turning point with no theme is still the effect before the line.
    assert await _voice(_Writer(), _game()) == ["the_turn", *expected]


@pytest.mark.asyncio
async def test_a_later_turning_point_is_not_part_of_this_story() -> None:
    later = _TURN.model_copy(update={"ply": 9, "move_number": 5, "is_mistake": False})
    roles = await _voice(_Writer(), _game(_UNSAFE_KING, turn=later))
    assert roles[:2] == ["the_cause", "the_move"]


@pytest.mark.asyncio
async def test_a_sentence_that_leaves_the_theme_out_falls_back_to_the_theme_fact() -> None:
    # Every try for the cause drops the king and its square; the plain fact is used.
    answers: list[str | None] = [
        *["The storm was gathering!"] * 3,
        *["White's king on h1 was cold."] * 3,
    ]
    writer = _Writer(answers)
    diagnosis, evidence = _missed_capture()
    facts = lesson_role_facts(build_narrator_facts(diagnosis, evidence, _FEN))
    assert facts
    voiced = await voice_facts(
        writer, facts, mover=chess.WHITE, played="3.Nf3", game=_game(_UNSAFE_KING)
    )
    cause, turn = voiced.sentences[0], voiced.sentences[1]
    assert (cause.fact.role, cause.source, cause.calls) == ("the_cause", "fact", 3)
    assert cause.text == cause.fact.text
    assert cause.text.startswith("From move 1 to move 3, White's king on e1 had little cover")
    # The turning point's tries name a square its fact does not hold: its fact is used too.
    assert (turn.fact.role, turn.source) == ("the_turn", "fact")
    assert voiced.sentences[2].fact.role == "the_move"


@pytest.mark.asyncio
async def test_only_the_first_theme_is_told_and_the_rest_are_counted() -> None:
    writer = _Writer()
    diagnosis, evidence = _missed_capture()
    facts = lesson_role_facts(build_narrator_facts(diagnosis, evidence, _FEN))
    assert facts
    _, line_dropped = select_facts(facts)
    voiced = await voice_facts(
        writer, facts, mover=chess.WHITE, played="3.Nf3", game=_game(_UNSAFE_KING, _WEAK_PAWNS)
    )
    assert [s.fact.role for s in voiced.sentences].count("the_cause") == 1
    assert voiced.dropped_facts == line_dropped + 1


@pytest.mark.parametrize("theme", [_UNSAFE_KING, _WEAK_PAWNS, _PASSIVE, _LAGGING])
@pytest.mark.parametrize("mover", [chess.WHITE, chess.BLACK])
def test_every_cause_fact_names_sides_and_passes_its_own_check(
    theme: ThemeFacts, mover: chess.Color
) -> None:
    cause, turn = cause_facts(_game(theme), mover)
    for fact in (cause, turn):
        assert not re.search(r"\byou(r)?\b", fact.text, re.IGNORECASE), fact.text
        assert fact.text[0].isupper() and fact.text.endswith(".")
        assert sentence_problems(fact.text, fact.text, (), dict(fact.sides)) == []
    side = "White" if mover == chess.WHITE else "Black"
    assert f"{side}'s" in cause.text


@pytest.mark.parametrize(
    ("theme", "index", "sentence"),
    [
        (_UNSAFE_KING, 0, "White's king on e1 was perfectly safe."),
        (_UNSAFE_KING, 0, "Black's queen on g5 smiled."),
        (_WEAK_PAWNS, 0, "A pawn on d3 stood proud."),
        (_PASSIVE, 0, "White's rook on a1 hummed a tune."),
        (_LAGGING, 0, "White's knight on b1 danced."),
        (_UNSAFE_KING, 1, "3.Nf3, a quiet knight step."),
        (_UNSAFE_KING, 1, "The game began to turn against Black with 3.Nf3."),
    ],
)
@pytest.mark.asyncio
async def test_a_sentence_that_keeps_a_square_but_not_the_claim_is_replaced(
    theme: ThemeFacts, index: int, sentence: str
) -> None:
    game = _game(theme)
    fact = cause_facts(game, chess.WHITE)[index]
    assert sentence_problems(sentence, fact.text, (), dict(fact.sides)) == []
    assert cause_problems(fact.text, fact) == []
    answers: list[str | None] = [*[None] * index, *[sentence] * 3]
    voiced = await voice_facts(
        _Writer(answers), _line(), mover=chess.WHITE, played="3.Nf3", game=game
    )
    assert voiced.sentences[index].source == "fact"
    assert voiced.sentences[index].text == fact.text


def test_every_cause_example_passes_the_sentence_check() -> None:
    sides = {"g1": "white"}
    for _role, fact, sentence in CAUSE_EXAMPLES:
        assert sentence_problems(sentence, fact, (), sides) == [], (fact, sentence)
        role: CauseRole = "the_cause" if _role == "The cause" else "the_turn"
        assert cause_problems(sentence, CauseFact(role, fact)) == []


def test_the_template_cause_text_keeps_the_order_and_the_player_words() -> None:
    text = cause_text(_game(_UNSAFE_KING))
    first, second = text.split(". ", 1)
    assert first.startswith("From move 1 to move 3, your king on e1 had little cover")
    assert second.startswith("The game began to turn against you with your move 3.Nf3")
    assert cause_text(None) == ""
    assert cause_text(_game(turn=None)) == ""


@pytest.mark.asyncio
async def test_the_template_fallback_tells_the_cause_before_the_tactic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnosis, evidence = _missed_capture()
    real = build_narrator_facts

    def with_theme(*args: object) -> NarratorFacts:
        facts = real(*args)  # type: ignore[arg-type]
        return facts.model_copy(update={"game": _game(_UNSAFE_KING)})

    monkeypatch.setattr(assembly, "build_narrator_facts", with_theme)
    trace = trace_from_scores(None, _GAME, [20, 20, 10, 0, 20, -850])
    template = await TemplateExplanationProvider().explain(diagnosis, evidence)
    # No provider: the template is the explanation.
    explanation = await resolve_explanation(diagnosis, evidence, _FEN, environment={}, game=trace)
    assert explanation.text == f"{cause_text(_game(_UNSAFE_KING))} {template.text}"
    # A provider error during the voice pipeline keeps the same ordered template.
    failed = await resolve_explanation(
        diagnosis,
        evidence,
        _FEN,
        local_provider=_Writer(error=True),  # type: ignore[arg-type]
        game=trace,
    )
    assert failed.text == explanation.text


@pytest.mark.asyncio
async def test_a_real_game_trace_reaches_the_voice_pipeline() -> None:
    # 3.Nf3 is the move after which the game never returned to level: it is the turning point.
    diagnosis, evidence = _missed_capture()
    trace = trace_from_scores(None, _GAME, [20, 20, 10, 0, 20, -850])
    writer = _Writer()
    explanation = await resolve_explanation(
        diagnosis,
        evidence,
        _FEN,
        local_provider=writer,  # type: ignore[arg-type]
        game=trace,
    )
    assert explanation.claims[0].text.startswith("The game began to turn against White with 3.Nf3")
    assert explanation.claims[1].text.startswith("3.Nf3: the knight on g1")


# --- The slot path (#507) ------------------------------------------------------------


class _Claims:
    """A provider that can only return claims, so the slot path runs."""

    def __init__(self, texts: tuple[str, ...], evidence_id: str) -> None:
        self.texts, self.evidence_id = texts, evidence_id
        self.requests: list[ExplanationRequest] = []

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        self.requests.append(request)
        return GeneratedExplanation(
            claims=tuple(
                ExplanationClaim(
                    text=text, evidence_ref=self.evidence_id, line=(), disclosure_level=1
                )
                for text in self.texts
            )
        )


_THEME = "{theme:unsafe_king}."
_TURNED = "{turning_point}."
_BEST = "{capture:best}."


@pytest.mark.parametrize(
    ("texts", "accepted"),
    [
        ((_THEME, _TURNED, _BEST), True),
        ((f"{_THEME} {_TURNED} {_BEST}",), True),
        # A slot that is not part of the line may come first.
        (("Before you played {played}, " + _THEME, _BEST), True),
        (("A {mistake}: " + _THEME, _BEST), True),
        # A theme told first may be named again, and a story without one is not ordered.
        ((_THEME, _BEST, _THEME), True),
        ((_BEST, "You played {played}."), True),
        # The turning point alone is still the effect before the line.
        ((_TURNED, _BEST), True),
        ((_BEST, _THEME), False),
        ((f"{_BEST} {_THEME}",), False),
        (("Look at {best}. " + _THEME,), False),
        (("{piece:1} was there. " + _THEME,), False),
        ((_THEME, _BEST, _TURNED), False),
        ((_TURNED, _THEME, _BEST), False),
    ],
)
@pytest.mark.asyncio
async def test_the_slot_path_tells_the_cause_before_the_line_or_falls_back(
    monkeypatch: pytest.MonkeyPatch, texts: tuple[str, ...], accepted: bool
) -> None:
    real = build_narrator_facts

    def with_theme(
        diagnosis: Diagnosis, evidence: list[Evidence], fen: str, game: object = None
    ) -> NarratorFacts:
        facts = real(diagnosis, evidence, fen)
        return facts.model_copy(update={"game": _game(_UNSAFE_KING)})

    monkeypatch.setattr(assembly, "build_narrator_facts", with_theme)
    reasons: list[str] = []
    monkeypatch.setattr(assembly, "record_template_fallback", reasons.append)
    diagnosis, evidence = _missed_capture()
    provider = _Claims(texts, real(diagnosis, evidence, _FEN).evidence[0].evidence_id)
    explanation = await resolve_explanation(
        diagnosis,
        evidence,
        _FEN,
        local_provider=provider,
        environment={},
        game=trace_from_scores(None, _GAME, [20, 20, 10, 0, 20, -850]),
    )
    assert len(provider.requests) == 1
    template = await TemplateExplanationProvider().explain(diagnosis, evidence)
    fallback = f"{cause_text(_game(_UNSAFE_KING))} {template.text}"
    assert reasons == ([] if accepted else ["cause_after_line"])
    assert (explanation.text != fallback) is accepted


def test_the_slot_prompt_states_the_order_only_with_a_cause() -> None:
    diagnosis, evidence = _missed_capture()
    plain = build_narrator_facts(diagnosis, evidence, _FEN)
    assert "Story order" not in lesson_slot_catalogue(plain).prompt()
    prompt = lesson_slot_catalogue(plain.model_copy(update={"game": _game(_UNSAFE_KING)})).prompt()
    assert "Story order: {theme:unsafe_king}, then {turning_point}, then the moves." in prompt
    assert 'slots: "{theme:unsafe_king}. {turning_point}. You played {played}' in prompt
    assert "so name no cause" not in prompt
    two = plain.model_copy(update={"game": _game(_UNSAFE_KING, _LAGGING)})
    both = lesson_slot_catalogue(two).prompt()
    # The example starts with the first theme, as the order line and the template do.
    assert "Start the first claim with {theme:unsafe_king}." in both
    assert 'slots: "{theme:unsafe_king}. {turning_point}. You played' in both
    turn_only = lesson_slot_catalogue(plain.model_copy(update={"game": _game()})).prompt()
    assert "Story order: {turning_point}, then the moves." in turn_only

"""The narrator voice pipeline: one fact per call, every sentence checked by code (#454)."""

from __future__ import annotations

import chess
import pytest

from scan64.explanations import assembly
from scan64.explanations.assembly import (
    lesson_role_facts,
    narrator_counts,
    resolve_explanation,
    sentence_writer,
)
from scan64.explanations.board_lines import (
    resolve_board_lines_explanation,
    template_board_explanation,
)
from scan64.explanations.facts import build_narrator_facts
from scan64.explanations.role_facts import RoleFact, role_facts
from scan64.explanations.sentence_check import (
    colour_words,
    framing_problems,
    limit_exclamations,
    pin_problems,
    repair,
    sentence_problems,
)
from scan64.explanations.settings import LocalNarratorProvider
from scan64.explanations.voice_pipeline import (
    MAX_RETRIES,
    VOICE_EXAMPLES,
    select_facts,
    voice_facts,
    voice_prompt,
    voice_system_prompt,
)
from scan64.providers.llm import ExplanationRequest, GeneratedExplanation, LLMProviderError
from tests.unit.test_board_lines import _facts as board_facts
from tests.unit.test_narrator_facts import _FEN, _missed_capture

# P1 of the voice experiments (#457): 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nd4 4.Nxe5 Qg5, White to move.
P1 = "r1b1kbnr/pppp1ppp/8/4N1q1/2BnP3/8/PPPP1PPP/RNBQK2R w KQkq - 1 5"


def _p1_facts() -> tuple[RoleFact, ...]:
    return role_facts(P1, "e5f7", "c4f7", best_line=("c4f7",), played_line=("g5g2",))


class _Writer:
    """Answers each call with the next scripted sentence; ``None`` echoes the fact."""

    def __init__(self, answers: list[str | None] | None = None, error: bool = False) -> None:
        self.answers = list(answers or [])
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def rewrite(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.error:
            raise LLMProviderError("model unavailable")
        answer = self.answers.pop(0) if self.answers else None
        if answer is None:
            return user.rsplit("Fact (", 1)[1].split("): ", 1)[1].split("\nSentence:", 1)[0]
        return answer


def test_every_voice_example_passes_the_sentence_check() -> None:
    for _role, fact, sentence in VOICE_EXAMPLES:
        assert sentence_problems(sentence, fact) == [], (fact, sentence)


def test_the_prompt_holds_the_card_and_one_fact_only() -> None:
    prompt = voice_prompt("The move", "White played 5.Nxf7.")
    assert prompt.startswith("Style: Tal\n\nHow this style writes: Lively")
    assert prompt.endswith(
        "Now rewrite this fact.\nFact (The move): White played 5.Nxf7.\nSentence:"
    )
    assert "Do not use" not in prompt
    assert "r1b1kbnr" not in prompt
    assert "Do not use: hunting, orphan." in voice_prompt("The move", "x", ["orphan", "hunting"])
    assert "move Black played" in voice_system_prompt("Black")


@pytest.mark.parametrize(
    ("sentence", "fact", "problem"),
    [
        ("The rook on h8 trembles.", "The knight on f7 attacks the rook on h8.", None),
        ("The knight on g6 strikes.", "The knight on f7 attacks the rook on h8.", "square g6"),
        ("The queen grabs the rook on h8.", "The knight on f7 attacks the rook on h8.", "piece"),
        (
            "7.Qe2 even snags Black's queen on e4!",
            "7.Qe2 also blocks the check, and it attacks Black's queen on e4.",
            "taken",
        ),
        ("A fork on h8!", "The knight on f7 attacks the rook on h8.", "term fork"),
        (
            "Bang. The rook on h8. Gone. Away.",
            "The knight on f7 attacks the rook on h8.",
            "too many",
        ),
        ("Black takes on g2 with 5...Qxg2.", "Black answers 5...Qxg2.", None),
    ],
)
def test_sentence_check(sentence: str, fact: str, problem: str | None) -> None:
    problems = sentence_problems(sentence, fact)
    if problem is None:
        assert problems == []
    else:
        assert any(problem in item for item in problems), problems


def test_repair_removes_a_mark_after_the_played_move_only() -> None:
    assert repair("5.Nxf7! grabs a pawn, and 5.Bxf7+! was better.", "5.Nxf7") == (
        "5.Nxf7 grabs a pawn, and 5.Bxf7+! was better."
    )


@pytest.mark.asyncio
async def test_a_failing_sentence_is_retried_then_the_fact_is_used() -> None:
    facts = _p1_facts()[:1]
    writer = _Writer(["The knight on a1 hops.", "The knight on a2 hops.", "The knight on a3 hops."])
    voiced = await voice_facts(writer, facts, mover=chess.WHITE, played="5.Nxf7")
    assert len(writer.calls) == 1 + MAX_RETRIES
    [sentence] = voiced.sentences
    assert sentence.source == "fact"
    assert sentence.text == facts[0].text
    assert voiced.fallback_sentences == 1


@pytest.mark.asyncio
async def test_a_good_sentence_is_kept_repaired_and_finished() -> None:
    facts = _p1_facts()[:1]
    writer = _Writer(["The greedy 5.Nxf7! snatches the pawn on f7"])
    voiced = await voice_facts(writer, facts, mover=chess.WHITE, played="5.Nxf7")
    assert voiced.text == "The greedy 5.Nxf7 snatches the pawn on f7."
    assert voiced.sentences[0].source == "model"
    assert voiced.calls == 1


@pytest.mark.asyncio
async def test_a_repeated_picture_word_is_written_once_more() -> None:
    facts, _ = select_facts(_p1_facts())
    first = facts[0]
    second = facts[1]
    assert first.role != second.role
    picture = "hungry"
    assert picture not in first.text.lower() and picture not in second.text.lower()
    writer = _Writer([f"A {picture} 5.Nxf7 takes the pawn on f7.", None, None])
    writer.answers.insert(1, f"So {picture}: " + second.text)
    voiced = await voice_facts(writer, facts[:2], mover=chess.WHITE, played="5.Nxf7")
    second_calls = writer.calls[1:]
    assert len(second_calls) == 2
    assert f"Do not use: {picture}." in second_calls[1][1]
    assert picture not in colour_words(voiced.sentences[1].text, second.text)


@pytest.mark.asyncio
async def test_board_lines_are_voiced_one_fact_per_call() -> None:
    facts = board_facts("start", 2)
    before = narrator_counts()["board"]
    writer = _Writer()
    resolved = await resolve_board_lines_explanation(facts, local_provider=writer, environment={})
    assert resolved.source == "narrator"
    claims = resolved.explanation.claims
    assert claims[0] == template_board_explanation(facts).claims[0]
    assert [claim.evidence_ref for claim in claims[1:]] and all(
        claim.evidence_ref in {line.line_id for line in facts.lines} for claim in claims[1:]
    )
    assert any(claim.text.startswith("Line 1: ") for claim in claims)
    # #485: each sentence cites a prefix of its line, and the move itself only its first move.
    lines = {line.line_id: line.uci for line in facts.lines}
    for claim in claims[1:]:
        assert claim.line and lines[claim.evidence_ref][: len(claim.line)] == claim.line
        if claim.text.startswith("Line "):
            assert len(claim.line) == 1
    after = narrator_counts()["board"]
    assert after.sentences > before.sentences
    for system, user in writer.calls:
        assert user.count("\nSentence:") == len(VOICE_EXAMPLES) + 1
        assert facts.position.fen not in user
        assert system.startswith("You rewrite one chess fact")


@pytest.mark.asyncio
async def test_a_provider_error_keeps_the_board_template() -> None:
    facts = board_facts("start", 2)
    resolved = await resolve_board_lines_explanation(
        facts, local_provider=_Writer(error=True), environment={}
    )
    assert resolved.source == "template"
    assert resolved.fallback_reason == "provider_error"


@pytest.mark.asyncio
async def test_a_lesson_is_voiced_from_its_role_facts() -> None:
    diagnosis, evidence = _missed_capture()
    facts = build_narrator_facts(diagnosis, evidence, _FEN)
    all_facts = lesson_role_facts(facts)
    assert all_facts
    assert {fact.role for fact in all_facts} >= {"the_move", "could_have_played"}
    story, dropped = select_facts(all_facts)
    assert 0 <= dropped < len(all_facts)
    writer = _Writer()
    explanation = await resolve_explanation(diagnosis, evidence, _FEN, local_provider=writer)
    assert len(writer.calls) == len(story)
    assert [claim.text for claim in explanation.claims] == [fact.text for fact in story]
    assert explanation.text == " ".join(fact.text for fact in story)


class _ClaimsOnly:
    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        raise LLMProviderError("not used")


def test_only_a_provider_that_writes_plain_text_takes_the_voice_path() -> None:
    assert sentence_writer(_ClaimsOnly()) is None
    assert sentence_writer(LocalNarratorProvider(_ClaimsOnly())) is None
    writer = LocalNarratorProvider(_Writer())  # type: ignore[arg-type]
    assert sentence_writer(writer) is writer
    assert assembly.sentence_writer(_Writer()) is not None  # type: ignore[arg-type]


def test_one_fact_per_role_in_story_order_a_capture_or_check_first() -> None:
    facts = _p1_facts()
    chosen, dropped = select_facts(facts)
    assert 1 <= len(chosen) <= 4
    assert 0 < dropped < len(facts)
    order = ("the_move", "what_it_misses", "what_follows", "could_have_played")
    roles = [fact.role for fact in chosen]
    assert roles == sorted(roles, key=order.index) and len(set(roles)) == len(roles)
    move = next(fact for fact in chosen if fact.role == "the_move")
    assert {"takes", "gives_check"} & {relation.kind for relation in move.relations}


ALT = "White could have played 5.Bxf7+ instead of 5.Nxf7."


@pytest.mark.parametrize(
    ("sentence", "fact", "flagged"),
    [
        (
            "5.Bxf7+ would have been a more spirited move.",
            "White could have played 5.Bxf7+ instead of 5.Nxf7.",
            True,
        ),
        ("5.Bxf7+ was far better.", "White could have played 5.Bxf7+ instead of 5.Nxf7.", True),
        (
            "5.Bxf7+ was the stronger path.",
            "White could have played 5.Bxf7+ instead of 5.Nxf7.",
            True,
        ),
        ("5.Bxf7+ took a safer road.", "White could have played 5.Bxf7+ instead of 5.Nxf7.", True),
        ("5.Bxf7+ was better, as the fact says.", "5.Bxf7+ was better than 5.Nxf7.", False),
        (
            "5.Bxf7+ would have hit harder.",
            "White could have played 5.Bxf7+ instead of 5.Nxf7.",
            False,
        ),
    ],
)
def test_judgement_words_need_their_fact(sentence: str, fact: str, flagged: bool) -> None:
    problems = [item for item in sentence_problems(sentence, fact) if item.startswith("judgement")]
    assert bool(problems) is flagged, problems


@pytest.mark.parametrize(
    ("sentence", "fact", "sides", "problem"),
    [
        (
            "White's queen on g5 eyes the pawn on g2.",
            "Black's queen on g5 already attacks the pawn on g2.",
            {},
            "calls the queen on g5 white",
        ),
        (
            "The rook on g5 eyes the pawn on g2.",
            "Black's queen on g5 already attacks the pawn on g2.",
            {},
            "pairs rook with g5",
        ),
        (
            "The white queen on g5 eyes the pawn on g2.",
            "The queen on g5 attacks the pawn on g2.",
            {"g5": "black", "g2": "white"},
            "calls the queen on g5 white",
        ),
        (
            "Black's queen on g5 stares at the pawn on g2.",
            "The queen on g5 attacks the pawn on g2.",
            {"g5": "black", "g2": "white"},
            None,
        ),
    ],
)
def test_every_piece_on_a_square_matches_its_fact(
    sentence: str, fact: str, sides: dict[str, str], problem: str | None
) -> None:
    problems = sentence_problems(sentence, fact, sides=sides)
    if problem is None:
        assert problems == []
    else:
        assert problem in problems, problems


@pytest.mark.parametrize(
    "verb",
    ["snatches", "grabs", "claims", "collects", "picks up", "wins", "casts off", "steals"],
)
def test_capture_verbs_need_a_capture_in_the_fact(verb: str) -> None:
    attacked = "The knight on f7 attacks the rook on h8."
    assert any(
        "taken" in item
        for item in sentence_problems(f"The knight on f7 {verb} the rook on h8.", attacked)
    )
    taken = "5.Nxf7: the knight on e5 takes the pawn on f7."
    assert not any(
        "taken" in item
        for item in sentence_problems(f"5.Nxf7! The knight {verb} the pawn on f7.", taken)
    )


def test_one_exclamation_per_comment() -> None:
    assert limit_exclamations(["What a move!", "The rook runs!", "Then 1.Re1! wins!"]) == [
        "What a move!",
        "The rook runs.",
        "Then 1.Re1 wins.",
    ]


@pytest.mark.asyncio
async def test_a_voiced_comment_keeps_one_exclamation() -> None:
    facts, _ = select_facts(_p1_facts())
    writer = _Writer([fact.text.rstrip(".") + "!" for fact in facts])
    voiced = await voice_facts(writer, facts, mover=chess.WHITE, played="5.Nxf7")
    assert voiced.text.count("!") <= 1
    assert all(sentence.text.endswith((".", "!", "?")) for sentence in voiced.sentences)


def test_the_alternative_keeps_its_framing_and_its_check() -> None:
    chosen, _ = select_facts(_p1_facts())
    alternative = next(fact for fact in chosen if fact.role == "could_have_played")
    assert alternative.text.startswith("White could have played 5.Bxf7+ instead of 5.Nxf7.")
    kinds = {relation.kind for relation in alternative.relations}
    assert "gives_check" in kinds


@pytest.mark.parametrize(
    ("sentence", "ok"),
    [
        ("The queen on e2 blocks the check.", False),
        ("7.Qe2 would have blocked the check as well.", True),
        ("7.Qe2 instead blocks the check too.", True),
        ("White could have blocked with 7.Qe2.", True),
    ],
)
def test_a_could_have_played_sentence_must_stay_a_possibility(sentence: str, ok: bool) -> None:
    assert (framing_problems(sentence, "could_have_played") == []) is ok
    assert framing_problems(sentence, "what_follows") == []


@pytest.mark.asyncio
async def test_an_alternative_stated_as_played_is_retried() -> None:
    chosen, _ = select_facts(_p1_facts())
    alternative = next(fact for fact in chosen if fact.role == "could_have_played")
    writer = _Writer(["5.Bxf7+ takes the pawn on f7 with check.", None])
    voiced = await voice_facts(writer, [alternative], mover=chess.WHITE, played="5.Nxf7")
    assert len(writer.calls) == 2
    assert "could" in voiced.text or "instead" in voiced.text


# P2 of the voice experiments: 7.Be2 blocks the check, 7...Nf3#; the bishop on e2 is pinned.
P2 = "r1b1kbnr/pppp1Npp/8/8/2Bnq3/8/PPPP1P1P/RNBQKR2 w Qkq - 0 7"


def _p2_facts() -> tuple[RoleFact, ...]:
    chosen, _ = select_facts(
        role_facts(P2, "c4e2", "d1e2", best_line=("d1e2",), played_line=("d4f3",))
    )
    return chosen


def test_a_merged_pin_fact_names_the_pinned_piece() -> None:
    texts = " ".join(fact.text for fact in _p2_facts())
    assert "pins it" not in texts
    assert "pins the bishop on e2 to the king on e1" in texts


def test_a_pin_must_name_the_pinned_piece() -> None:
    pinned = {("bishop", "e2")}
    wrong = "The queen on e4 pins the knight on f3 to the king on e1."
    assert pin_problems(wrong, pinned)
    assert not pin_problems("The queen on e4 pins the bishop on e2 to the king on e1.", pinned)
    assert not pin_problems("The bishop on e2 is pinned, so the knight on f3 stays.", pinned)
    assert pin_problems("The knight on f3 is pinned by the queen on e4.", pinned)


@pytest.mark.asyncio
async def test_a_wrong_pin_target_is_retried() -> None:
    pin_fact = next(fact for fact in _p2_facts() if "pins" in fact.text)
    wrong = "The queen on e4 pins the knight on f3 to the king on e1."
    writer = _Writer([wrong, None])
    voiced = await voice_facts(writer, [pin_fact], mover=chess.WHITE, played="7.Be2")
    assert len(writer.calls) == 2
    assert "pins the knight on f3" not in voiced.text


def test_snipe_is_a_capture_word_and_deadly_a_judgement() -> None:
    attacked = "The knight on f7 attacks the rook on h8."
    problems = sentence_problems("The knight on f7 snipes the rook on h8.", attacked)
    assert any("taken" in item for item in problems)
    problems = sentence_problems("A deadly knight on f7 eyes the rook on h8.", attacked)
    assert any(item.startswith("judgement") for item in problems)


@pytest.mark.parametrize(
    ("sentence", "fact", "kinds", "flagged"),
    [
        # The #484 review: a capture is never "quiet".
        (
            "5.Bxg5 takes the queen on g5, a quiet bishop step.",
            "5.Bxg5: the bishop on c1 takes the queen on g5.",
            ["takes"],
            True,
        ),
        # A check is never "quiet" either.
        (
            "5.Bb5+ quietly checks the king on e8.",
            "5.Bb5+: the bishop on b5 gives check to the king on e8.",
            ["gives_check"],
            True,
        ),
        # A move that takes nothing and gives no check may be quiet (a voice card example).
        ("1.Qd2, a quiet queen step.", "White played 1.Qd2.", ["moves"], False),
    ],
)
def test_quiet_needs_a_move_that_takes_nothing_and_gives_no_check(
    sentence: str, fact: str, kinds: list[str], flagged: bool
) -> None:
    problems = sentence_problems(sentence, fact, kinds)
    assert ("calls a move quiet; the fact takes or gives check" in problems) is flagged, problems


@pytest.mark.parametrize("word", ["strong", "elegant", "risky", "safe", "crushing"])
def test_colour_words_that_judge_need_their_fact(word: str) -> None:
    attacked = "The knight on f7 attacks the rook on h8."
    problems = sentence_problems(f"A {word} knight on f7 eyes the rook on h8.", attacked)
    assert f"judgement {word}" in problems

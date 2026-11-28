"""The phrase-bank narrator (#461): code-written variants, each checked like a model sentence.

The positions are the #458 voice benchmark set (P1-P5 tuned, H1-H5 held out),
the fixtures of ``test_role_facts``.
"""

from __future__ import annotations

import random

import chess
import pytest
from chess_lesson_spec import Diagnosis

from scan64.explanations.assembly import phrase_bank_explanation, resolve_explanation
from scan64.explanations.phrase_bank import (
    SHAPES,
    explanation_seed,
    phrase_facts,
    role_problems,
    shape_of,
    variants,
)
from scan64.explanations.role_facts import RoleFact, role_facts
from scan64.explanations.sentence_check import framing_problems, pin_problems, sentence_problems
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.explanations.voice_pipeline import fact_sides, pinned_pieces, sentence_parts
from tests.unit.test_claim_text_grounding import MISSED_CAPTURE
from tests.unit.test_role_facts import FIXTURES, NAMES


def _facts(name: str) -> tuple[RoleFact, ...]:
    _, fen, played, best, best_line, played_line = next(f for f in FIXTURES if f[0] == name)
    return role_facts(fen, played, best, best_line=best_line, played_line=played_line)


def _problems(text: str, fact: RoleFact) -> list[str]:
    kinds = [relation.kind for relation in fact.relations]
    problems = sentence_problems(
        text, fact.text, kinds, fact_sides(fact), max(1, sentence_parts(fact.text))
    )
    problems += pin_problems(text, pinned_pieces(fact))
    shape = shape_of(fact)
    if shape is not None and shape.name == "could_have_played":
        problems += framing_problems(text, fact.role)
    return problems


@pytest.mark.parametrize("name", NAMES)
def test_every_variant_passes_the_sentence_check(name: str) -> None:
    for fact in _facts(name):
        assert shape_of(fact) is not None, f"no phrase variants for {fact.text!r}"
        options = variants(fact)
        assert len(options) >= 2, fact.text
        assert len(set(options)) == len(options), options
        for text in options:
            assert _problems(text, fact) == [], (fact.text, text)


def test_every_shape_has_several_variants() -> None:
    assert len({shape.name for shape in SHAPES}) == len(SHAPES)
    for shape in SHAPES:
        assert len(shape.variants) >= 2, shape.name


@pytest.mark.parametrize("name", NAMES)
def test_an_explanation_is_checked_whole_and_never_falls_back(name: str) -> None:
    voiced = phrase_facts(_facts(name))
    assert voiced.sentences
    for sentence in voiced.sentences:
        assert sentence.source == "phrase", sentence.text
        assert role_problems(sentence.text, sentence.fact) == []
    assert voiced.text.count("!") <= 1


def test_the_same_facts_read_the_same_way() -> None:
    facts = _facts("P2")
    assert phrase_facts(facts).text == phrase_facts(facts).text
    assert explanation_seed(facts) == explanation_seed(_facts("P2"))
    # Other seeds choose other variants.
    assert len({phrase_facts(facts, seed=seed).text for seed in range(20)}) > 1


def test_no_variant_repeats_within_one_explanation() -> None:
    for name in NAMES:
        for seed in range(30):
            voiced = phrase_facts(_facts(name), seed=seed)
            sentences = [part for s in voiced.sentences for part in s.text.split(". ")]
            # Two facts of one shape may share the plain wording, never a written variant.
            written = [p for p in sentences if not any(p in f.text for f in _facts(name))]
            assert len(written) == len(set(written)), (name, seed, written)


def test_random_positions_stay_faithful() -> None:
    """Off the benchmark set a role may fall back to its plain fact, never to a false one."""
    rng = random.Random(461)
    checked = 0
    while checked < 300:
        board = chess.Board(None)
        for square in rng.sample(range(64), rng.randint(2, 12)):
            board.set_piece_at(
                square, chess.Piece(rng.choice([1, 2, 3, 4, 5, 5]), rng.random() < 0.5)
            )
        for colour in (chess.WHITE, chess.BLACK):
            empty = [s for s in chess.SQUARES if board.piece_at(s) is None]
            board.set_piece_at(rng.choice(empty), chess.Piece(chess.KING, colour))
        board.turn = rng.random() < 0.5
        if not board.is_valid() or not any(board.legal_moves):
            continue
        moves = list(board.legal_moves)
        played, best = rng.choice(moves), rng.choice(moves)
        after = board.copy()
        after.push(played)
        follow = (rng.choice(list(after.legal_moves)).uci(),) if any(after.legal_moves) else ()
        facts = role_facts(board.fen(), played.uci(), best.uci(), played_line=follow)
        for sentence in phrase_facts(facts).sentences:
            if sentence.source == "phrase":
                assert role_problems(sentence.text, sentence.fact) == [], sentence.text
            else:
                assert sentence.text.rstrip(".") in sentence.fact.text
        checked += 1


@pytest.mark.asyncio
async def test_without_a_model_the_phrase_bank_replaces_the_template() -> None:
    diagnosis = Diagnosis(
        primary="threat_processing.missed_capture",
        confidence=0.9,
        evidence_refs=[MISSED_CAPTURE.evidence.evidence_id],
    )
    template = await TemplateExplanationProvider().explain(diagnosis, [MISSED_CAPTURE.evidence])
    explanation = await resolve_explanation(
        diagnosis, [MISSED_CAPTURE.evidence], MISSED_CAPTURE.fen, environment={}
    )
    assert explanation != template
    assert explanation == phrase_bank_explanation(
        diagnosis, [MISSED_CAPTURE.evidence], MISSED_CAPTURE.fen, template
    )
    assert explanation.claims
    evidence_id = MISSED_CAPTURE.evidence.evidence_id
    assert {claim.evidence_ref for claim in explanation.claims} == {evidence_id}
    assert explanation.text == " ".join(claim.text for claim in explanation.claims)

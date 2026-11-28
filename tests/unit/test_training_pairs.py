"""Verified training pairs (#464): code facts in, an annotator's sentence out.

The engine's view is written by hand here, so every rule is tested without an
engine. The game is Tartakower-Burn, the #463 fixture.
"""

from __future__ import annotations

import chess
import pytest

from scan64.explanations.sentence_check import MOVE, SQUARE, judgement_problems, violations
from scan64.learning.annotated_corpus.books import read_games
from scan64.learning.annotated_corpus.corpus import Game, replay
from scan64.learning.training_pairs import (
    DESCRIPTIVE_MOVE,
    EngineView,
    heldout,
    judge,
    position_facts,
    to_san,
)
from tests.unit.test_annotated_corpus import CHESS_STRATEGY, FIXTURE


def _game() -> Game:
    book = read_games(FIXTURE.read_text("utf-8"), CHESS_STRATEGY)
    game = replay("lasker_chess_strategy", book[0])
    assert isinstance(game, Game)
    return game


GAME = _game()
# 8.Nxe5 is ply 15; the annotator: "It would have been better to protect the pawn
# by Q-K2 or P-Q3."
PLY = 15


def _view(
    best_line: tuple[str, ...] = ("d2d3", "e8g8"),
    played_line: tuple[str, ...] = ("e8g8",),
    *,
    score: int = 0,
    mate: bool = False,
) -> EngineView:
    return EngineView(
        best_line=best_line,
        played_line=played_line,
        threat_line=None,
        best_score_cp=score,
        played_score_cp=score,
        best_mate=mate,
        played_mate=mate,
    )


def test_the_game_and_its_comment() -> None:
    assert GAME.sans[PLY - 1] == "Nxe5"
    assert any(c.ply == PLY and "Q-K2 or P-Q3" in c.text for c in GAME.comments)


def test_facts_are_the_live_narrator_facts() -> None:
    facts = position_facts(GAME.sans, PLY, _view())
    assert facts.played == "f3e5"
    assert {fact.role for fact in facts.role} >= {"the_move", "could_have_played"}
    assert "8.Nxe5" in facts.text and "8.d3" in facts.text
    fact_moves = {move.uci() for _, _, move in facts.positions}
    assert fact_moves == {"f3e5", "d2d3", "e8g8"}


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("White played KtxP here.", "White played 8.Nxe5 here."),
        ("P-Q3 protects it.", "8.d3 protects it."),
        ("Black answers Castles.", "Black answers 8...O-O."),
        ("8. P-Q3 first.", "8.d3 first."),
        ("8 P - Q 3 first.", "8.d3 first."),
    ],
)
def test_descriptive_moves_become_the_facts_san(sentence: str, expected: str) -> None:
    text, reason = to_san(sentence, position_facts(GAME.sans, PLY, _view()))
    assert (text, reason) == (expected, None)


@pytest.mark.parametrize(
    ("sentence", "reason"),
    [
        # Q-K2 is a legal move here, but no fact holds it.
        ("It would have been better to protect the pawn by Q-K2.", "move not in the facts"),
        ("The bishop at Kt5 pins nothing.", "descriptive square or piece"),
        ("This is a sequel to the previous move.", "no chess content"),
        ("The KtP is weak.", "descriptive square or piece"),
        # The move number must match: Black's 8th move is not "9. P-Q3".
        ("9. P-Q3 was possible.", "move not in the facts"),
        ("8... P-Q3 was possible.", "move not in the facts"),
        ("KtxP takes the pawn on e5. P-Q3 protects it.", "more than one sentence"),
        ("If KtxP takes the pawn on e5, but it is a mistake.", "unsupported judgement"),
        ("After KtxP the game is a draw.", "engine: not a draw"),
        ("KtxP and mate follows.", "engine: no mate"),
        ("After P-Q3 White is winning.", "engine: level"),
        ("KtxP wins.", "engine: level"),
        ("KtxP develops the knight.", "unsupported term"),
        ("P-Q3 was threatened.", "unsupported term"),
        ("P-Q3 offered the only hope.", "unsupported term"),
        ("KtxP is a blunder.", "unsupported judgement"),
        ("KtxP takes the queen on d8.", "unsupported piece or square"),
    ],
)
def test_each_drop_names_its_reason(sentence: str, reason: str) -> None:
    view = _view(score=400) if "draw" in sentence else _view()
    verdict = judge(sentence, position_facts(GAME.sans, PLY, view), view)
    assert (verdict.outcome, verdict.reason) == ("dropped", reason)


def test_a_backed_sentence_is_kept() -> None:
    view = _view()
    facts = position_facts(GAME.sans, PLY, view)
    verdict = judge("KtxP takes the pawn on e5.", facts, view)
    assert (verdict.outcome, verdict.text) == ("kept", "8.Nxe5 takes the pawn on e5.")


def test_a_judgement_clause_is_trimmed_off() -> None:
    view = _view()
    facts = position_facts(GAME.sans, PLY, view)
    verdict = judge("KtxP takes the pawn on e5, but it is a mistake.", facts, view)
    assert (verdict.outcome, verdict.text, verdict.reason) == (
        "trimmed",
        "8.Nxe5 takes the pawn on e5.",
        "unsupported judgement",
    )
    assert "mistake" in verdict.unmatched_terms


def test_a_mate_claim_stands_when_the_engine_has_mate() -> None:
    view = _view(mate=True)
    facts = position_facts(GAME.sans, PLY, view)
    assert judge("KtxP and mate follows.", facts, view).reason != "engine: no mate"


def test_kept_sentences_are_faithful_over_the_whole_game() -> None:
    """Every sentence of every comment: a kept or trimmed text passes the checks."""
    judged = 0
    for comment in GAME.comments:
        if comment.ply == 0 or comment.ply >= len(GAME.sans):
            continue
        board = chess.Board()
        for san in GAME.sans[: comment.ply]:
            board.push_san(san)
        follow = board.parse_san(GAME.sans[comment.ply]).uci()
        before = chess.Board()
        for san in GAME.sans[: comment.ply - 1]:
            before.push_san(san)
        played = before.parse_san(GAME.sans[comment.ply - 1]).uci()
        view = _view(best_line=(played,), played_line=(follow,))
        facts = position_facts(GAME.sans, comment.ply, view)
        fact_moves = {m.rstrip("+#") for m in MOVE.findall(facts.text)}
        for sentence in comment.sentences:
            verdict = judge(sentence, facts, view)
            judged += 1
            if verdict.text is None:
                assert verdict.outcome == "dropped" and verdict.reason
                continue
            assert not DESCRIPTIVE_MOVE.search(verdict.text), verdict.text
            assert violations(verdict.text, facts.text) == [], verdict.text
            assert judgement_problems(verdict.text, facts.text) == [], verdict.text
            assert {m.rstrip("+#") for m in MOVE.findall(verdict.text)} <= fact_moves
            assert set(SQUARE.findall(verdict.text)) <= set(SQUARE.findall(facts.text)) | {
                m[-2:] for m in fact_moves
            }
    assert judged > 20


def test_the_held_out_split_is_fixed_by_game() -> None:
    assert heldout("lasker_chess_strategy", "1", 10) == heldout("lasker_chess_strategy", "1", 10)
    games = [str(number) for number in range(1, 201)]
    share = sum(heldout("lasker_chess_strategy", game, 10) for game in games) / len(games)
    assert 0.03 < share < 0.2

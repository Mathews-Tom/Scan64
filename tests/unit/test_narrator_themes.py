"""Root-cause themes and the turning point as narrator facts (#400)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import chess
import pytest
from chess_lesson_spec import ExplanationClaim

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations import assembly
from scan64.explanations.claims import ClaimFacts, unsupported_claim
from scan64.explanations.factor_facts import GROUP_NAMES
from scan64.explanations.facts import Material, NarratorFacts, build_narrator_facts
from scan64.explanations.slots import game_slots, lesson_slot_catalogue
from scan64.explanations.themes import (
    THEME_SOURCE,
    GameThemeFacts,
    GameTrace,
    ThemeKey,
    TurningPointFacts,
    check_level_scores,
    game_theme_facts,
    measure_themes,
    theme_phrase,
    trace_from_scores,
    turning_point,
)
from scan64.explanations.validator import (
    validate_generated_explanation,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import GeneratedExplanation
from tests.unit.test_narrator_facts import _FEN, _missed_capture

# The learner (the side to move) has each theme's signal past its threshold.
PRESENT: dict[ThemeKey, str] = {
    # Open g-file next to the king; the black queen on h4 attacks its squares.
    "unsafe_king": "r4rk1/ppp2ppp/2n5/3p1b2/3P3q/2P2N2/P1Q2P1P/R1B2RK1 w - - 0 14",
    # Isolated pawns on a3, c2 and c3.
    "weak_pawns": "r2q1rk1/pp3ppp/2n2n2/8/8/P1P2N2/2P2PPP/R2Q1RK1 w - - 0 14",
    # Black's rook on a8 and bishop on c8 are shut in after move 20.
    "passive_pieces": "r1b2rk1/pp1n1ppp/8/3pP3/8/3B1N2/PP1K1PPP/R6R b - - 2 25",
    # Black's knights and bishop at home; White has developed all but one.
    "lagging_development": "rn1qkbnr/ppp2ppp/3p4/4p3/2B1P1b1/2N2N2/PPPP1PPP/R1BQK2R b KQkq - 0 5",
}
# The same learner without the theme.
ABSENT: dict[ThemeKey, str] = {
    "unsafe_king": "r1bq1rk1/ppp2ppp/2n2n2/3p4/3P4/2PB1N2/P4PPP/R1BQ1RK1 w - - 0 10",
    "weak_pawns": "r1bq1rk1/ppp2ppp/2n2n2/3p4/3P4/2PB1N2/P4PPP/R1BQ1RK1 w - - 0 10",
    # The same position at move 15: pieces at home count as development.
    "passive_pieces": "r1b2rk1/pp1n1ppp/8/3pP3/8/3B1N2/PP1K1PPP/R6R b - - 2 15",
    # The same position after move 20.
    "lagging_development": "rn1qkbnr/ppp2ppp/3p4/4p3/2B1P1b1/2N2N2/PPPP1PPP/R1BQK2R b KQkq - 0 25",
}


def _static_trace(fens: list[str], learner_scores: list[int]) -> GameTrace:
    """A trace of learner positions ``fens`` with an opponent position between them.

    ``learner_scores`` has one score per position (2 per learner position, the
    last one after the mistake), learner's view.
    """
    boards: list[chess.Board] = []
    for fen in fens:
        board = chess.Board(fen)
        boards.append(board)
        other = board.copy(stack=False)
        other.turn = not other.turn
        boards.append(other)
    learner = boards[0].turn
    moves = tuple(next(iter(board.legal_moves)) for board in boards[:-1])
    sign = 1 if learner == chess.WHITE else -1
    scores = tuple(sign * score for score in learner_scores)
    return GameTrace(tuple(boards), moves, scores)


_FALLING = [0, -10, -20, -30, -40, -50, -60, -400]


@pytest.mark.parametrize("key", list(PRESENT))
def test_each_theme_is_present_when_its_signal_holds(key: ThemeKey) -> None:
    trace = _static_trace([PRESENT[key]] * 4, _FALLING)
    learner = trace.boards[0].turn
    window, trend, themes = measure_themes(trace, 6, learner)
    assert window == (0, 2, 4, 6)
    assert trend == -400
    assert key in {theme.key for theme in themes}
    theme = next(theme for theme in themes if theme.key == key)
    assert theme.held == (0, 2, 4, 6)
    assert len(theme.measured) == 4


@pytest.mark.parametrize("key", list(ABSENT))
def test_each_theme_is_absent_without_its_signal(key: ThemeKey) -> None:
    trace = _static_trace([ABSENT[key]] * 4, _FALLING)
    _, _, themes = measure_themes(trace, 6, trace.boards[0].turn)
    assert key not in {theme.key for theme in themes}


def test_a_theme_needs_three_window_positions_including_the_last() -> None:
    present, absent = PRESENT["unsafe_king"], ABSENT["unsafe_king"]
    for fens, expected in (
        ([absent, present, present, present], True),
        ([present, present, absent, present], True),
        ([present, absent, absent, present], False),
        ([present, present, present, absent], False),
    ):
        trace = _static_trace(fens, _FALLING)
        _, _, themes = measure_themes(trace, 6, chess.WHITE)
        assert ("unsafe_king" in {t.key for t in themes}) is expected, fens


def test_no_theme_without_an_engine_fall_across_the_window() -> None:
    flat = [0, 0, 0, 0, 0, 0, 0, -30]
    trace = _static_trace([PRESENT["unsafe_king"]] * 4, flat)
    _, trend, themes = measure_themes(trace, 6, chess.WHITE)
    assert trend == -30
    assert themes == ()


def test_no_theme_before_the_third_learner_move() -> None:
    trace = _static_trace([PRESENT["unsafe_king"]] * 2, [0, 0, 0, -400])
    window, _, themes = measure_themes(trace, 2, chess.WHITE)
    assert window == (0, 2)
    assert themes == ()


def test_theme_phrases_name_pieces_and_squares_without_factor_names_or_numbers() -> None:
    phrases = {}
    for key, fen in PRESENT.items():
        trace = _static_trace([fen] * 4, _FALLING)
        _, _, themes = measure_themes(trace, 6, trace.boards[0].turn)
        phrases[key] = theme_phrase(next(t for t in themes if t.key == key))
    assert phrases["unsafe_king"].startswith("on move 14, your king on g1 had little cover: ")
    assert "the g-file" in phrases["unsafe_king"]
    assert "queen on h4" in phrases["unsafe_king"]
    assert "isolated pawns on c2, a3 and c3" in phrases["weak_pawns"]
    assert "your rook on a8 could go only to b8" in phrases["passive_pieces"]
    assert "your bishop on c8 had no square" in phrases["passive_pieces"]
    assert "your knight on b8" in phrases["lagging_development"]
    assert (
        "while your opponent had only the bishop on c1 at home" in (phrases["lagging_development"])
    )
    for phrase in phrases.values():
        assert not re.search(r"\d\.\d|centipawn|\bcp\b", phrase), phrase
        for name in GROUP_NAMES.values():
            assert name not in phrase, (name, phrase)


# --- Turning point ---------------------------------------------------------------------


def _trace(scores: list[int]) -> GameTrace:
    """A legal game from the start position (White is the learner), learner's view."""
    moves = ["g1f3", "g8f6", "f3g1", "f6g8"] * (len(scores) // 4 + 1)
    return trace_from_scores(None, moves[: len(scores) - 1], scores)


def test_turning_point_is_the_last_level_move_of_a_lost_game() -> None:
    #        0   1    2    3    4    5     6     7     8     9
    scores = [20, 10, 0, -20, -10, -80, -90, -150, -200, -500]
    trace = _trace(scores)
    point = turning_point(trace, 8, chess.WHITE)
    assert point is not None
    assert (point.ply, point.move_number, point.side, point.san) == (5, 3, "white", "Nf3")
    assert (point.evaluation_before_cp, point.evaluation_after_cp) == (-10, -80)
    assert point.evaluation_end_cp == -500
    # The turning point is not the final blunder (ply 9).
    assert point.is_mistake is False


def test_a_return_to_level_moves_the_turning_point_later() -> None:
    scores = [20, 10, 0, -20, -10, -80, -90, -40, 0, -500]
    point = turning_point(_trace(scores), 8, chess.WHITE)
    assert point is not None
    assert (point.ply, point.is_mistake) == (9, True)


@pytest.mark.parametrize(
    "scores",
    [
        [20, 10, 0, -20, -10, -80, -90, -150, -200, -250],  # the game is not lost
        [20, 10, 0, -20, -10, -55, -90, -150, -200, -500],  # the move lost under half a pawn
        [20, 10, 0, -20, -80, -10, -90, -150, -200, -500],  # last level position: opponent to move
        [-80, -90, -100, -120, -150, -200, -300, -400, -450, -500],  # never level
    ],
)
def test_no_turning_point(scores: list[int]) -> None:
    assert turning_point(_trace(scores), 8, chess.WHITE) is None


def test_mates_and_unscored_positions() -> None:
    trace = _trace([20, 10, 0, -20, -10, -80, -90, -150, -200, 0])
    mated = GameTrace(trace.boards, trace.moves, (*trace.scores[:-1], -99_998))
    point = turning_point(mated, 8, chess.WHITE)
    assert point is not None and point.evaluation_end_cp == -1000
    gaps = GameTrace(trace.boards, trace.moves, (*trace.scores[:6], None, None, None, -99_998))
    point = turning_point(gaps, 8, chess.WHITE)
    assert point is not None and point.ply == 5


# --- Narrator facts and slots ---------------------------------------------------------------

# The missed-capture fixture (3.Nf3?, Bxg5 was available) as a game.
_GAME = ["e2e4", "e7e5", "d2d3", "d8g5", "g1f3"]


def _game_facts(scores: list[int | None]) -> NarratorFacts:
    diagnosis, evidence = _missed_capture()
    trace = trace_from_scores(None, _GAME, scores)
    assert trace.boards[4].fen() == chess.Board(_FEN).fen()
    return build_narrator_facts(diagnosis, evidence, _FEN, trace)


def test_build_narrator_facts_adds_the_turning_point_and_its_slot() -> None:
    facts = _game_facts([20, 20, 10, 0, 20, -850])
    assert facts.game is not None
    assert facts.game.source == THEME_SOURCE
    assert facts.game.mistake_ply == 5
    assert facts.game.window == (0, 2, 4)
    point = facts.game.turning_point
    assert point is not None and point.is_mistake and point.san == "Nf3"
    NarratorFacts.model_validate_json(facts.model_dump_json(), strict=True)
    catalogue = lesson_slot_catalogue(facts)
    slot = catalogue.slots_for(facts.evidence[0].evidence_id)["turning_point"]
    assert slot.value.startswith("the game began to turn against you with your move 3.Nf3")
    assert "{turning_point}" in catalogue.prompt() or "turning_point" in catalogue.prompt()


def test_without_the_game_the_facts_and_prompt_are_unchanged() -> None:
    diagnosis, evidence = _missed_capture()
    facts = build_narrator_facts(diagnosis, evidence, _FEN)
    assert facts.game is None
    assert "theme" not in lesson_slot_catalogue(facts).prompt()


def test_a_game_that_does_not_reach_the_position_adds_nothing() -> None:
    diagnosis, evidence = _missed_capture()
    trace = trace_from_scores(None, ["d2d4", "d7d5"], [0, 0, 0])
    assert build_narrator_facts(diagnosis, evidence, _FEN, trace).game is None
    assert game_theme_facts(trace, chess.Board(_FEN), chess.Move.from_uci("g1f3")) is None


def _point(ply: int) -> TurningPointFacts:
    return TurningPointFacts(
        ply=ply,
        move_number=(ply + 1) // 2,
        side="white" if ply % 2 else "black",
        san="Qd3",
        uci="d1d3",
        evaluation_before_cp=-10,
        evaluation_after_cp=-90,
        evaluation_end_cp=-1000,
        is_mistake=False,
    )


def test_a_later_turning_point_is_no_slot_of_this_lesson() -> None:
    base = GameThemeFacts(source=THEME_SOURCE, mistake_ply=21, window=(14, 16, 18, 20))
    earlier = base.model_copy(update={"turning_point": _point(13)})
    later = base.model_copy(update={"turning_point": _point(23)})
    assert [s.name for s in game_slots(earlier)] == ["turning_point"]
    assert "7.Qd3" in game_slots(earlier)[0].value
    assert game_slots(later) == []
    black = base.model_copy(update={"turning_point": _point(14)})
    assert "7...Qd3" in game_slots(black)[0].value


def _with_unsafe_king(facts: NarratorFacts) -> NarratorFacts:
    trace = _static_trace([PRESENT["unsafe_king"]] * 4, _FALLING)
    _, trend, themes = measure_themes(trace, 6, chess.WHITE)
    game = GameThemeFacts(
        source=THEME_SOURCE,
        mistake_ply=5,
        window=(0, 2, 4),
        evaluation_trend_cp=trend,
        themes=tuple(t for t in themes if t.key == "unsafe_king"),
    )
    return facts.model_copy(update={"game": game})


def _validate(facts: NarratorFacts, evidence: list[Evidence], text: str) -> str:
    context = assembly._grounding_context(facts, evidence, _FEN)
    explanation = validate_generated_explanation(
        GeneratedExplanation(
            claims=(
                ExplanationClaim(
                    text=text,
                    evidence_ref=facts.evidence[0].evidence_id,
                    line=[],
                    disclosure_level=1,
                ),
            )
        ),
        context,
    )
    return explanation.claims[0].text


def test_a_theme_slot_renders_and_supports_its_claim() -> None:
    diagnosis, evidence = _missed_capture()
    facts = _with_unsafe_king(build_narrator_facts(diagnosis, evidence, _FEN))
    text = _validate(facts, evidence, "Before you played {played}, {theme:unsafe_king}.")
    assert text.startswith("Before you played Nf3, on move 14, your king on g1 had little cover")
    # The theme supports the king claim (ClaimFacts: test_theme_claim_facts).
    assert _validate(facts, evidence, "Your king was exposed, and you played {played}.")


def test_theme_claim_facts() -> None:
    root = Material(white=39, black=39, learner_advantage=0)
    facts = ClaimFacts.collect(root, (), (), (), themes=("unsafe_king", "passive_pieces"))
    assert unsupported_claim("your king is exposed", facts) is None
    assert unsupported_claim("your pieces are passive", facts) is None
    assert unsupported_claim("your opponent's king is exposed", facts) is not None
    assert unsupported_claim("your king is exposed", ClaimFacts.collect(root, (), ())) is not None


def _search(deep: dict[int, int | None]) -> tuple[list[int], Any]:
    """White-view deep scores by position, and the positions searched."""
    asked: list[int] = []

    async def search(index: int) -> EngineAnalysis | None:
        asked.append(index)
        score = deep[index]
        if score is None:
            return None
        cp = score if index % 2 == 0 else -score
        return EngineAnalysis(config={}, raw_result=[{"score_cp": cp}])

    return asked, search


def test_a_deep_check_moves_a_noisy_turning_point_to_the_earlier_move() -> None:
    # Fast pass: position 4 looks level (-40). Deeper, it is not.
    fast = _trace([0, 0, 0, -120, -40, -160, -200, -300, -400])
    asked, search = _search({4: -110, 2: 0, 3: -120})
    checked = asyncio.run(check_level_scores(fast, chess.WHITE, search))
    before, after = (turning_point(t, 8, chess.WHITE) for t in (fast, checked))
    assert before is not None and before.ply == 5
    assert after is not None and after.ply == 3
    assert asked == [4, 2, 3]


def test_a_deep_check_finds_a_later_level_position() -> None:
    fast = _trace([0, 0, 0, -120, -70, -160, -200, -300, -400])
    asked, search = _search({4: -10, 5: None})
    point = turning_point(
        asyncio.run(check_level_scores(fast, chess.WHITE, search)), 8, chess.WHITE
    )
    assert point is not None and point.ply == 5
    assert point.evaluation_after_cp == -160  # a search without a score keeps the fast score
    assert asked == [4, 5]


def test_no_deep_check_without_a_lost_game() -> None:
    asked, search = _search({})
    trace = _trace([0, 0, 0, -60, -40])
    assert asyncio.run(check_level_scores(trace, chess.WHITE, search)) is trace
    assert asked == []

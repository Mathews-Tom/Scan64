"""Variant root-cause themes: crazyhouse, three-check and atomic (#402)."""

from __future__ import annotations

import re
from typing import get_args

import chess
import chess.variant
import pytest

from scan64.explanations.claims import THEME_TOPICS, ClaimFacts, unsupported_claim
from scan64.explanations.facts import Material
from scan64.explanations.slots import _THEME_MEANINGS, game_slots
from scan64.explanations.themes import (
    THEME_SOURCE,
    VARIANT_THEME_ORDER,
    GameThemeFacts,
    GameTrace,
    ThemeKey,
    measure_themes,
    theme_phrase,
    trace_from_scores,
    turning_point,
)
from scan64.explanations.variant_themes import (
    checking_moves,
    drop_threats,
    explosion_threats,
    king_drop_squares,
)

_CRAZYHOUSE = chess.variant.CrazyhouseBoard
_THREE_CHECK = chess.variant.ThreeCheckBoard
_ATOMIC = chess.variant.AtomicBoard

# White (the learner, to move) has each theme's signal past its threshold.
PRESENT: dict[ThemeKey, tuple[type[chess.Board], str]] = {
    # Black holds a knight and a pawn in hand; White holds nothing.
    "pocket_deficit": (
        _CRAZYHOUSE,
        "r1bqkb1r/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQK2R[np] w KQkq - 0 6",
    ),
    # After g3 the king on g1 has h1 and g2 unguarded; Black has a knight in hand.
    "king_drop_squares": (
        _CRAZYHOUSE,
        "r1bq1rk1/pppp1ppp/2n2n2/4p3/4P3/3P1NP1/PPP2P1P/RNBQ1RK1[n] w - - 0 7",
    ),
    # N@h3 checks the king; B@h3 attacks the rook on f1. Neither can be taken.
    "drop_threats": (
        _CRAZYHOUSE,
        "r1bq1rk1/pppp1ppp/2n2n2/4p3/4P3/3P1NP1/PPP2P1P/RNBQ1RK1[nb] w - - 0 7",
    ),
    # Black has given two checks, White none.
    "checks_behind": (
        _THREE_CHECK,
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 3+1 0 3",
    ),
    # The queen on h4 checks the king; it can check again from f2, g3 and e4.
    "check_exposure": (
        _THREE_CHECK,
        "rnb1kbnr/pppp1ppp/8/4p3/4P2q/8/PPPP2PP/RNBQKBNR w KQkq - 3+3 0 3",
    ),
    # Nxd2 or Nxe2 explodes next to the king on e1.
    "explosion_threats": (
        _ATOMIC,
        "rnbqkb1r/pppppppp/8/8/8/2n5/PPPPPPPP/R1BQKBNR w KQkq - 0 3",
    ),
}
# The same learner without the theme.
ABSENT: dict[ThemeKey, tuple[type[chess.Board], str]] = {
    # Each side holds a knight.
    "pocket_deficit": (
        _CRAZYHOUSE,
        "r1bqkb1r/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQK2R[Nn] w KQkq - 0 6",
    ),
    # The same holes, but Black has nothing in hand to drop there.
    "king_drop_squares": (
        _CRAZYHOUSE,
        "r1bq1rk1/pppp1ppp/2n2n2/4p3/4P3/3P1NP1/PPP2P1P/RNBQ1RK1 w - - 0 7",
    ),
    # A knight alone: N@h3+ is the only strong drop.
    "drop_threats": (
        _CRAZYHOUSE,
        "r1bq1rk1/pppp1ppp/2n2n2/4p3/4P3/3P1NP1/PPP2P1P/RNBQ1RK1[n] w - - 0 7",
    ),
    # One check each.
    "checks_behind": (
        _THREE_CHECK,
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 2+2 0 3",
    ),
    # After 1.e4 e5 2.f4 only Qh4 checks.
    "check_exposure": (
        _THREE_CHECK,
        "rnbqkbnr/pppp1ppp/8/4p3/4PP2/8/PPPP2PP/RNBQKBNR w KQkq - 3+3 0 2",
    ),
    # White's knight on c6 explodes the king on e8 first (Nxd8).
    "explosion_threats": (
        _ATOMIC,
        "rnbqkb1r/pppppppp/2N5/8/8/2n5/PPPPPPPP/R1BQKB1R w KQkq - 0 3",
    ),
}
_VARIANT_KEYS = [
    key for variant, keys in VARIANT_THEME_ORDER.items() if variant != "chess" for key in keys
]
_FALLING = (0, -10, -20, -30, -40, -50, -60, -400)


def _static_trace(cls: type[chess.Board], fens: list[str]) -> GameTrace:
    """A falling trace of learner positions ``fens`` (White to move) on ``cls`` boards."""
    boards: list[chess.Board] = []
    for fen in fens:
        board = cls(fen)
        boards.append(board)
        other = board.copy(stack=False)
        other.turn = not other.turn
        boards.append(other)
    moves = tuple(next(iter(board.legal_moves)) for board in boards[:-1])
    return GameTrace(tuple(boards), moves, _FALLING)


def _themes(cls: type[chess.Board], fens: list[str]) -> dict[str, object]:
    _, _, themes = measure_themes(_static_trace(cls, fens), 6, chess.WHITE)
    return {theme.key: theme for theme in themes}


def test_every_variant_theme_has_present_and_absent_cases_and_a_meaning() -> None:
    assert set(PRESENT) == set(ABSENT) == set(_VARIANT_KEYS)
    assert set(_THEME_MEANINGS) == set(get_args(ThemeKey))


@pytest.mark.parametrize("key", list(PRESENT))
def test_each_variant_theme_is_present_when_its_signal_holds(key: ThemeKey) -> None:
    cls, fen = PRESENT[key]
    trace = _static_trace(cls, [fen] * 4)
    window, trend, themes = measure_themes(trace, 6, chess.WHITE)
    assert (window, trend) == ((0, 2, 4, 6), -400)
    theme = next(theme for theme in themes if theme.key == key)
    assert theme.held == (0, 2, 4, 6)
    assert all(value is not None and value >= theme.threshold for value in theme.measured)


@pytest.mark.parametrize("key", list(ABSENT))
def test_each_variant_theme_is_absent_without_its_signal(key: ThemeKey) -> None:
    cls, fen = ABSENT[key]
    assert key not in _themes(cls, [fen] * 4)


def test_a_variant_theme_needs_three_window_positions_including_the_last() -> None:
    cls, present = PRESENT["checks_behind"]
    _, absent = ABSENT["checks_behind"]
    assert "checks_behind" in _themes(cls, [absent, present, present, present])
    assert "checks_behind" not in _themes(cls, [present, present, present, absent])


def test_each_variant_measures_only_its_own_themes() -> None:
    # The standard unsafe_king position (open g-file, queen on h4) as a crazyhouse board.
    fen = "r4rk1/ppp2ppp/2n5/3p1b2/3P3q/2P2N2/P1Q2P1P/R1B2RK1 w - - 0 14"
    measured = _themes(_CRAZYHOUSE, [fen] * 4)
    assert set(measured) <= set(VARIANT_THEME_ORDER["crazyhouse"])
    assert "unsafe_king" not in measured
    # A three-check position measures no crazyhouse theme, even with pieces in hand elsewhere.
    cls, three_check = PRESENT["check_exposure"]
    assert set(_themes(cls, [three_check] * 4)) <= set(VARIANT_THEME_ORDER["3check"])
    # A standard board measures no variant theme.
    _, crazy = PRESENT["drop_threats"]
    standard = _static_trace(chess.Board, [crazy.split("[")[0] + " w - - 0 7"] * 4)
    _, _, themes = measure_themes(standard, 6, chess.WHITE)
    assert not {t.key for t in themes} & set(_VARIANT_KEYS)
    # A variant without themes measures none.
    king_of_the_hill = _static_trace(
        chess.variant.KingOfTheHillBoard, [chess.STARTING_FEN.replace(" 0 1", " 0 3")] * 4
    )
    assert measure_themes(king_of_the_hill, 6, chess.WHITE)[2] == ()


def test_board_measures() -> None:
    _, fen = PRESENT["drop_threats"]
    board = _CRAZYHOUSE(fen)
    assert [chess.square_name(s) for s in king_drop_squares(board, chess.WHITE)] == ["h1", "g2"]
    drops = drop_threats(board, chess.WHITE)
    assert [(chess.piece_symbol(d.piece_type), chess.square_name(d.square)) for d in drops] == [
        ("n", "h3"),
        ("b", "h3"),
    ]
    assert drops[0].check and not drops[1].check
    # A check already on the board is no new check; the queen checks again from 3 squares.
    _, fen = PRESENT["check_exposure"]
    moves = checking_moves(_THREE_CHECK(fen), chess.WHITE)
    assert sorted(m.uci() for m in moves) == ["h4e4", "h4f2", "h4g3"]
    _, fen = PRESENT["explosion_threats"]
    assert {m.uci() for m in explosion_threats(_ATOMIC(fen), chess.WHITE)} == {"c3d1", "c3e2"}


def test_variant_phrases_name_pieces_and_squares_without_numbers() -> None:
    phrases = {}
    for key, (cls, fen) in PRESENT.items():
        theme = _themes(cls, [fen] * 4)[key]
        phrases[key] = theme_phrase(theme)  # type: ignore[arg-type]
    assert phrases["pocket_deficit"].endswith(
        "your opponent held a knight and a pawn in hand to drop, and you held nothing"
    )
    assert phrases["king_drop_squares"].endswith(
        "your king on g1 had empty squares next to it that none of your other pieces "
        "guarded, where your opponent could drop a piece: h1 and g2"
    )
    assert phrases["drop_threats"].endswith(
        "a knight on h3 with check and a bishop on h3 attacking your rook on f1"
    )
    assert phrases["checks_behind"].endswith(
        "your opponent had given two checks and you none: one more check would win them the game"
    )
    assert phrases["check_exposure"].endswith(
        "your king on e1 was open to checks: your opponent's queen on h4 could give check "
        "on f2, g3 or e4"
    )
    assert phrases["explosion_threats"].endswith(
        "one capture could blow up your king on e1: your opponent's knight on c3 could "
        "capture on d1 or e2"
    )
    for phrase in phrases.values():
        assert not re.search(r"\d\.\d|centipawn|\bcp\b|pawns? of", phrase), phrase


def test_checks_left_are_stated() -> None:
    cls, fen = PRESENT["checks_behind"]
    one_ahead = fen.replace("3+1", "3+2")
    theme = _themes(cls, [one_ahead] * 4)["checks_behind"]
    assert theme.checks is not None  # type: ignore[attr-defined]
    assert theme_phrase(theme).endswith(  # type: ignore[arg-type]
        "your opponent had given one check and you none: they needed two more checks to win"
    )


def test_variant_themes_become_slots_and_support_king_claims() -> None:
    cls, fen = PRESENT["explosion_threats"]
    _, trend, themes = measure_themes(_static_trace(cls, [fen] * 4), 6, chess.WHITE)
    game = GameThemeFacts(
        source=THEME_SOURCE,
        mistake_ply=7,
        window=(0, 2, 4, 6),
        evaluation_trend_cp=trend,
        themes=themes,
    )
    assert [slot.name for slot in game_slots(game)] == ["theme:explosion_threats"]
    root = Material(white=39, black=39, learner_advantage=0)
    facts = ClaimFacts.collect(root, (), (), (), themes=("explosion_threats",))
    assert unsupported_claim("your king is exposed", facts) is None
    assert THEME_TOPICS["check_exposure"] == "king"


def test_a_crazyhouse_trace_replays_drops_and_names_the_turning_drop() -> None:
    # 1.e4 d5 2.exd5 Qxd5 3.Nc3 Qa5 4.P@b4: White drops the captured pawn.
    moves = ["e2e4", "d7d5", "e4d5", "d8d5", "b1c3", "d5a5", "P@b4"]
    scores = [20, 30, 20, 20, 10, 20, 30, -400]
    trace = trace_from_scores(None, moves, scores, variant="crazyhouse")
    assert trace.variant == "crazyhouse"
    assert isinstance(trace.boards[-1], _CRAZYHOUSE)
    assert trace.moves[-1].drop == chess.PAWN
    point = turning_point(trace, 6, chess.WHITE)
    assert point is not None and point.san == "P@b4"

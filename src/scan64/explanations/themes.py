"""Root-cause themes and the turning point of a game (issue #400).

A lesson explains one mistake. A game is often lost before that move: the king
stood without cover for several moves, or the pieces had no squares. This
module computes such root causes from the game's moves, the engine scores of
the game's positions (the fast pass) and the factor module, and states each in
plain chess words with the concrete pieces and squares.

Window. A theme is measured at the positions where the learner was to move in
their last ``THEME_WINDOW`` moves up to and including the mistake. It holds
when its signal passes its threshold at ``THEME_MIN_POSITIONS`` or more of
them, including the position before the mistake, and the engine score fell
across the window by ``THEME_TREND_CP`` or more (learner's view).

Themes (signal, threshold):

- ``unsafe_king``: the learner's King group value (factor module, learner
  minus opponent, pawns) at or below the "in trouble" end of its health scale
  (``HEALTH_SCALE["King"].empty``), with the opponent's queen on the board,
  and an open file next to the king or an opponent piece attacking the
  squares around it;
- ``weak_pawns``: the learner's Pawns group value at or below
  ``HEALTH_SCALE["Pawns"].empty``, with at least one isolated, doubled or
  backward learner pawn;
- ``passive_pieces``: the learner's Mobility group value at or below
  ``HEALTH_SCALE["Mobility"].empty``, with at least one learner knight,
  bishop, rook or queen that has at most ``PASSIVE_SQUARES`` squares to go to
  that no opponent pawn attacks;
- ``lagging_development``: up to move ``DEVELOPMENT_MOVES``, the learner has
  ``DEVELOPMENT_GAP`` or more knights and bishops on their starting squares,
  and that many more than the opponent.

These four are standard chess only: the factor module and the board parts use
standard rules. Variant games (#402) have their own themes, read with the
variant's board rules (``variant_themes.py``) from a trace of that variant,
scored by an engine that plays it:

- crazyhouse ``pocket_deficit``: the opponent's pieces in hand are worth
  ``POCKET_DEFICIT`` pawns or more than the learner's;
- crazyhouse ``king_drop_squares``: the learner's king has ``KING_DROP_SQUARES``
  or more empty squares next to it that no other learner piece guards, more
  than the opponent's king has, and the opponent has a piece in hand;
- crazyhouse ``drop_threats``: the opponent has ``DROP_THREATS`` or more drops
  on safe squares that give check or attack a learner knight, bishop, rook or
  queen;
- three-check ``checks_behind``: the opponent has given ``CHECKS_BEHIND`` or
  more checks more than the learner;
- three-check ``check_exposure``: the opponent has ``CHECK_EXPOSURE`` or more
  moves that would give check;
- atomic ``explosion_threats``: the opponent has ``EXPLOSION_THREATS`` or more
  captures whose explosion would destroy the learner's king, and the learner
  has no such capture against the opponent's king.

The other supported variants have no theme yet.

Turning point: in a game whose last scored position is ``-LOST_CP`` or
worse for the learner, the learner's move from the last position where the
learner was level or better (``-LEVEL_CP``); every position after that move is
worse than level, so the score never came back, and the move itself lost
``TURNING_STEP_CP`` or more. It may be an earlier move than
the final blunder. Scores are clamped to ``+-SCORE_CLAMP_CP`` (a mate
counts as the clamp).

The engine stays the judge: a theme names a lasting feature of the positions
while the engine score fell; it does not prove that the feature alone lost the
game.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import chess
import chess.variant
from pydantic import BaseModel, ConfigDict, Field

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.variants import STANDARD, VariantName, board_class, board_variant, pgn_san
from scan64.explanations import variant_themes
from scan64.explanations.factor_facts import FACTOR_SOURCE, HEALTH_SCALE, health_values
from scan64.explanations.factors import evaluate_factors

THEME_SOURCE = f"scan64-themes/2, {FACTOR_SOURCE}"

# Window and persistence.
THEME_WINDOW = 4  # learner moves up to and including the mistake
THEME_MIN_POSITIONS = 3  # positions of the window where the signal must pass
THEME_TREND_CP = 50  # engine score fall across the window, learner's view
# Board thresholds.
PASSIVE_SQUARES = 1  # a piece with at most this many squares free of enemy pawns
DEVELOPMENT_MOVES = 20  # lagging development is measured up to this move number
DEVELOPMENT_GAP = 2  # undeveloped knights and bishops, and the lead over the opponent
# Turning point.
LEVEL_CP = 50  # level or better: the learner is at most half a pawn worse
LOST_CP = 300  # the game's last scored position is this much worse or more
TURNING_STEP_CP = 50  # the turning move itself loses at least this much
SCORE_CLAMP_CP = 1000
# Variant thresholds (#402).
POCKET_DEFICIT = 3  # crazyhouse: opponent's pieces in hand minus the learner's, pawns
KING_DROP_SQUARES = 2  # crazyhouse: unguarded empty squares next to the learner's king
DROP_THREATS = 2  # crazyhouse: the opponent's safe drops that check or attack a piece
CHECKS_BEHIND = 1  # three-check: the opponent's checks given minus the learner's
CHECK_EXPOSURE = 2  # three-check: the opponent's moves that would give check
EXPLOSION_THREATS = 1  # atomic: the opponent's captures that would destroy the king
DETAIL_LIMIT = 3  # a variant theme names at most this many pieces or drops

ThemeKey = Literal[
    "unsafe_king",
    "weak_pawns",
    "passive_pieces",
    "lagging_development",
    "pocket_deficit",
    "king_drop_squares",
    "drop_threats",
    "checks_behind",
    "check_exposure",
    "explosion_threats",
]
PieceName = Literal["pawn", "knight", "bishop", "rook", "queen", "king"]

# The health-scale group each factor theme reads.
_THEME_GROUPS: dict[ThemeKey, str] = {
    "unsafe_king": "King",
    "weak_pawns": "Pawns",
    "passive_pieces": "Mobility",
}


class _Facts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ThemePiece(_Facts):
    owner: Literal["learner", "opponent"]
    piece: PieceName
    square: str
    squares: tuple[str, ...] = Field(
        default=(),
        description="passive_pieces: the squares free of enemy pawns the piece can go to; "
        "check_exposure: the squares where the piece would give check; explosion_threats: "
        "the squares where its capture would destroy the king.",
    )


class ThemePockets(_Facts):
    """Crazyhouse: the pieces each side holds in hand, highest value first."""

    learner: tuple[PieceName, ...]
    opponent: tuple[PieceName, ...]


class ThemeChecks(_Facts):
    """Three-check: the checks each side has given; three checks win."""

    learner_given: int = Field(ge=0, le=3)
    opponent_given: int = Field(ge=0, le=3)


class ThemeFacts(_Facts):
    """A theme whose signal passed its threshold over the window."""

    key: ThemeKey
    signal: str = Field(description="What is measured, for example the King group value.")
    threshold: float = Field(description="Signal at or below (pawns) / at or above (counts).")
    measured: tuple[float | None, ...] = Field(
        description="The signal at each window position, oldest first; None: not measured."
    )
    held: tuple[int, ...] = Field(
        description="Game plies (positions, 0 = start) of the window where the theme held."
    )
    move_from: int = Field(description="Move number of the first position where it held.")
    move_to: int = Field(description="Move number of the last position (before the mistake).")
    pieces: tuple[ThemePiece, ...] = ()
    squares: tuple[str, ...] = Field(
        default=(), description="weak_pawns: the weak pawns' squares, isolated first."
    )
    files: tuple[str, ...] = Field(
        default=(), description="unsafe_king: open files next to the king (no learner pawn)."
    )
    weaknesses: tuple[Literal["isolated", "doubled", "backward"], ...] = Field(
        default=(), description="weak_pawns: the weakness of each square in squares."
    )
    targets: tuple[ThemePiece, ...] = Field(
        default=(),
        description="drop_threats: the learner piece each drop in pieces checks or attacks.",
    )
    pockets: ThemePockets | None = Field(default=None, description="pocket_deficit.")
    checks: ThemeChecks | None = Field(default=None, description="checks_behind.")


class TurningPointFacts(_Facts):
    """The learner's move where the engine score began its lasting fall."""

    ply: int = Field(ge=1, description="Game ply of the move (1 = the first move).")
    move_number: int
    side: Literal["white", "black"]
    san: str
    uci: str
    evaluation_before_cp: int = Field(description="Learner's view, clamped.")
    evaluation_after_cp: int
    evaluation_end_cp: int = Field(description="The game's last scored position, clamped.")
    is_mistake: bool = Field(description="True: the turning point is the lesson's mistake.")


class GameThemeFacts(_Facts):
    source: str = Field(description="Theme rules and factor module version.")
    mistake_ply: int = Field(ge=1, description="Game ply of the lesson's mistake.")
    window: tuple[int, ...] = Field(description="Game plies of the window positions.")
    evaluation_trend_cp: int | None = Field(
        default=None,
        description="Engine score after the mistake minus at the window start, learner's view.",
    )
    themes: tuple[ThemeFacts, ...] = ()
    turning_point: TurningPointFacts | None = None


# --- Game trace ----------------------------------------------------------------------


@dataclass(frozen=True)
class GameTrace:
    """A game's positions and engine scores (White's view, mates as large scores).

    ``scores[i]`` is the score of the position after ``i`` plies; ``None`` when
    no search scored it.
    """

    boards: tuple[chess.Board, ...]
    moves: tuple[chess.Move, ...]
    scores: tuple[int | None, ...]

    @property
    def variant(self) -> VariantName:
        """The variant the trace's boards play (their python-chess board class)."""
        return board_variant(self.boards[0])

    def index_of(self, before: chess.Board, played: chess.Move) -> int | None:
        """The ply count of the position ``before`` where ``played`` was played.

        A position and move that recur match their last occurrence.
        """
        key = _position_key(before)
        for index in reversed(range(len(self.moves))):
            if self.moves[index] == played and _position_key(self.boards[index]) == key:
                return index
        return None


def _position_key(board: chess.Board) -> str:
    return " ".join(board.fen().split()[:4])


def _white_score(analysis: EngineAnalysis | None, white_to_move: bool) -> int | None:
    if analysis is None or not analysis.raw_result:
        return None
    raw = analysis.raw_result[0]
    if raw.get("score_mate") is not None:
        mate = int(raw["score_mate"])
        value = (100_000 - abs(mate)) * (1 if mate > 0 else -1) if mate else -100_000
    elif raw.get("score_cp") is not None:
        value = int(raw["score_cp"])
    else:
        return None
    return value if white_to_move else -value


def _start(initial_fen: str | None, variant: VariantName) -> chess.Board:
    cls = board_class(variant)
    return cls(initial_fen) if initial_fen else cls()


def game_trace(
    initial_fen: str | None,
    moves: Sequence[str],
    evaluations: Sequence[EngineAnalysis | None],
    variant: VariantName = STANDARD,
) -> GameTrace:
    """The trace of a game from its UCI moves and one search per reached position.

    ``variant`` picks the board rules; the evaluations must come from an engine
    that plays that variant.
    """
    board = _start(initial_fen, variant)
    boards = [board.copy(stack=False)]
    parsed = []
    for uci in moves:
        move = chess.Move.from_uci(uci)
        board.push(move)
        parsed.append(move)
        boards.append(board.copy(stack=False))
    scores = tuple(
        _white_score(evaluations[i] if i < len(evaluations) else None, b.turn == chess.WHITE)
        for i, b in enumerate(boards)
    )
    return GameTrace(tuple(boards), tuple(parsed), scores)


def trace_from_scores(
    initial_fen: str | None,
    moves: Sequence[str],
    white_cp: Sequence[int | None],
    variant: VariantName = STANDARD,
) -> GameTrace:
    """A trace from UCI or SAN moves and White-view scores (fixtures, hand check)."""
    board = _start(initial_fen, variant)
    boards = [board.copy(stack=False)]
    parsed = []
    for text in moves:
        try:
            move = chess.Move.from_uci(text)
            if move not in board.legal_moves:
                raise ValueError(text)
        except ValueError:
            move = board.parse_san(text)
        board.push(move)
        parsed.append(move)
        boards.append(board.copy(stack=False))
    scores = tuple(white_cp[i] if i < len(white_cp) else None for i in range(len(boards)))
    return GameTrace(tuple(boards), tuple(parsed), scores)


def _learner_score(trace: GameTrace, index: int, learner: chess.Color) -> int | None:
    score = trace.scores[index]
    if score is None:
        return None
    value = score if learner == chess.WHITE else -score
    return max(-SCORE_CLAMP_CP, min(SCORE_CLAMP_CP, value))


# --- Board facts ---------------------------------------------------------------------

_PIECE_NAMES: dict[int, PieceName] = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}
_HOME = {
    chess.WHITE: {
        chess.B1: chess.KNIGHT,
        chess.G1: chess.KNIGHT,
        chess.C1: chess.BISHOP,
        chess.F1: chess.BISHOP,
    },
    chess.BLACK: {
        chess.B8: chess.KNIGHT,
        chess.G8: chess.KNIGHT,
        chess.C8: chess.BISHOP,
        chess.F8: chess.BISHOP,
    },
}


# Every piece's starting square (passive_pieces leaves unmoved pieces out).
_START = {
    square: (piece.color, piece.piece_type)
    for square, piece in chess.Board().piece_map().items()
    if piece.piece_type != chess.PAWN
}


def _owner(colour: chess.Color, learner: chess.Color) -> Literal["learner", "opponent"]:
    return "learner" if colour == learner else "opponent"


def _piece(
    board: chess.Board, square: int, learner: chess.Color, squares: tuple[str, ...] = ()
) -> ThemePiece:
    piece = board.piece_at(square)
    assert piece is not None
    return ThemePiece(
        owner=_owner(piece.color, learner),
        piece=_PIECE_NAMES[piece.piece_type],
        square=chess.square_name(square),
        squares=squares,
    )


def king_exposure(
    board: chess.Board, learner: chess.Color
) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """Open files next to the learner's king (no learner pawn) and the opponent's
    pieces (not pawns or king) that attack the king or a square next to it."""
    king = board.king(learner)
    if king is None:
        return (), ()
    file = chess.square_file(king)
    pawns = board.pieces(chess.PAWN, learner)
    open_files = tuple(
        chess.FILE_NAMES[f]
        for f in range(max(0, file - 1), min(7, file + 1) + 1)
        if not any(chess.square_file(p) == f for p in pawns)
    )
    zone = chess.SquareSet(chess.BB_KING_ATTACKS[king]) | chess.SquareSet.from_square(king)
    attackers: set[int] = set()
    for square in zone:
        for attacker in board.attackers(not learner, square):
            piece = board.piece_at(attacker)
            if piece is not None and piece.piece_type not in (chess.PAWN, chess.KING):
                attackers.add(attacker)
    order = sorted(attackers, key=lambda s: (-_value(board, s), s))
    return open_files, tuple(order)


_VALUES = {chess.QUEEN: 9, chess.ROOK: 5, chess.BISHOP: 3, chess.KNIGHT: 3, chess.PAWN: 1}


def _value(board: chess.Board, square: int) -> int:
    piece = board.piece_at(square)
    return _VALUES.get(piece.piece_type, 0) if piece is not None else 0


def weak_pawns(
    board: chess.Board, side: chess.Color
) -> list[tuple[int, Literal["isolated", "doubled", "backward"]]]:
    """``side``'s isolated, doubled and backward pawns (one weakness each, in that order).

    Isolated: no own pawn on an adjacent file. Doubled: another own pawn on the
    same file (the rear pawn is named). Backward: not isolated, no own pawn on
    an adjacent file level with it or behind it, and its stop square is
    attacked by an enemy pawn.
    """
    pawns = board.pieces(chess.PAWN, side)
    forward = 1 if side == chess.WHITE else -1
    found: list[tuple[int, Literal["isolated", "doubled", "backward"]]] = []
    for square in sorted(pawns):
        file, rank = chess.square_file(square), chess.square_rank(square)
        neighbours = [p for p in pawns if abs(chess.square_file(p) - file) == 1]
        same_file = [p for p in pawns if chess.square_file(p) == file and p != square]
        if not neighbours:
            found.append((square, "isolated"))
            continue
        if any((chess.square_rank(p) - rank) * forward > 0 for p in same_file):
            found.append((square, "doubled"))
            continue
        stop_rank = rank + forward
        if not 0 <= stop_rank <= 7:
            continue
        stop = chess.square(file, stop_rank)
        supported = any((chess.square_rank(p) - rank) * forward <= 0 for p in neighbours)
        enemy_pawns = board.attackers(not side, stop) & board.pieces(chess.PAWN, not side)
        if not supported and enemy_pawns:
            found.append((square, "backward"))
    order = {"isolated": 0, "doubled": 1, "backward": 2}
    found.sort(key=lambda item: (order[item[1]], item[0]))
    return found


def free_squares(board: chess.Board, square: int) -> tuple[int, ...]:
    """Squares the piece on ``square`` can move to (pseudo-legal, not onto its own
    pieces) that no enemy pawn attacks."""
    piece = board.piece_at(square)
    if piece is None:
        return ()
    own = board.occupied_co[piece.color]
    enemy_pawns = board.pieces(chess.PAWN, not piece.color)
    covered = chess.SquareSet()
    for pawn in enemy_pawns:
        covered |= board.attacks(pawn)
    targets = board.attacks(square) & ~chess.SquareSet(own) & ~covered
    return tuple(sorted(targets))


def passive_pieces(board: chess.Board, side: chess.Color) -> list[tuple[int, tuple[int, ...]]]:
    """``side``'s knights, bishops, rooks and queens with few free squares (``PASSIVE_SQUARES``).

    Up to move ``DEVELOPMENT_MOVES`` a piece on its starting square is left out.
    """
    found = []
    for piece_type in (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT):
        for square in sorted(board.pieces(piece_type, side)):
            opening = board.fullmove_number <= DEVELOPMENT_MOVES
            if opening and _START.get(square) == (side, piece_type):
                continue  # in the opening a piece at home is development, not passivity
            squares = free_squares(board, square)
            if len(squares) <= PASSIVE_SQUARES:
                found.append((square, squares))
    return found


def undeveloped(board: chess.Board, side: chess.Color) -> list[int]:
    """``side``'s knights and bishops still on their starting squares."""
    return [
        square
        for square, piece_type in _HOME[side].items()
        if board.piece_type_at(square) == piece_type and board.color_at(square) == side
    ]


# --- Signals -------------------------------------------------------------------------


@dataclass(frozen=True)
class _Measure:
    value: float | None
    holds: bool


def _factor_value(board: chess.Board, learner: chess.Color, group: str) -> float | None:
    try:
        report = evaluate_factors(board)
    except ValueError:  # not exactly one king per side, or a pawn on a back rank
        return None
    return round(health_values(report, learner)[group], 2)


def _measure(key: ThemeKey, board: chess.Board, learner: chess.Color) -> _Measure:
    if key in _VARIANT_KEYS:
        return _variant_measure(key, board, learner)
    if key == "lagging_development":
        mine = len(undeveloped(board, learner))
        theirs = len(undeveloped(board, not learner))
        holds = (
            board.fullmove_number <= DEVELOPMENT_MOVES
            and mine >= DEVELOPMENT_GAP
            and mine - theirs >= DEVELOPMENT_GAP
        )
        return _Measure(float(mine - theirs), holds)
    value = _factor_value(board, learner, _THEME_GROUPS[key])
    if value is None or value > HEALTH_SCALE[_THEME_GROUPS[key]].empty:
        return _Measure(value, False)
    if key == "unsafe_king":
        files, attackers = king_exposure(board, learner)
        has_queen = bool(board.pieces(chess.QUEEN, not learner))
        return _Measure(value, has_queen and bool(files or attackers))
    if key == "weak_pawns":
        return _Measure(value, bool(weak_pawns(board, learner)))
    return _Measure(value, bool(passive_pieces(board, learner)))


_SIGNALS: dict[ThemeKey, str] = {
    "unsafe_king": "King group value, learner minus opponent, pawns (factor module)",
    "weak_pawns": "Pawns group value, learner minus opponent, pawns (factor module)",
    "passive_pieces": "Mobility group value, learner minus opponent, pawns (factor module)",
    "lagging_development": "learner's knights and bishops at home minus the opponent's",
    "pocket_deficit": "opponent's pieces in hand minus the learner's, pawns",
    "king_drop_squares": "empty squares next to the learner's king that no other learner "
    "piece guards (more than next to the opponent's king)",
    "drop_threats": "opponent's drops on safe squares that give check or attack a learner "
    "knight, bishop, rook or queen",
    "checks_behind": "opponent's checks given minus the learner's",
    "check_exposure": "opponent's moves that would give check, if it were their move",
    "explosion_threats": "opponent's captures whose explosion would destroy the learner's king",
}
_VARIANT_THRESHOLDS: dict[ThemeKey, int] = {
    "pocket_deficit": POCKET_DEFICIT,
    "king_drop_squares": KING_DROP_SQUARES,
    "drop_threats": DROP_THREATS,
    "checks_behind": CHECKS_BEHIND,
    "check_exposure": CHECK_EXPOSURE,
    "explosion_threats": EXPLOSION_THREATS,
}
_VARIANT_KEYS = frozenset(_VARIANT_THRESHOLDS)


def _threshold(key: ThemeKey) -> float:
    if key in _VARIANT_THRESHOLDS:
        return float(_VARIANT_THRESHOLDS[key])
    if key == "lagging_development":
        return float(DEVELOPMENT_GAP)
    return HEALTH_SCALE[_THEME_GROUPS[key]].empty


def _details(key: ThemeKey, board: chess.Board, learner: chess.Color) -> dict[str, object]:
    if key in _VARIANT_KEYS:
        return _variant_details(key, board, learner)
    if key == "unsafe_king":
        files, attackers = king_exposure(board, learner)
        king = board.king(learner)
        assert king is not None
        pieces = [_piece(board, king, learner), *(_piece(board, s, learner) for s in attackers)]
        return {"pieces": tuple(pieces), "files": files}
    if key == "weak_pawns":
        weak = weak_pawns(board, learner)
        return {
            "squares": tuple(chess.square_name(s) for s, _ in weak),
            "weaknesses": tuple(kind for _, kind in weak),
        }
    if key == "passive_pieces":
        return {
            "pieces": tuple(
                _piece(board, s, learner, tuple(chess.square_name(t) for t in targets))
                for s, targets in passive_pieces(board, learner)
            )
        }
    pieces = [_piece(board, s, learner) for s in sorted(undeveloped(board, learner))]
    pieces += [_piece(board, s, learner) for s in sorted(undeveloped(board, not learner))]
    return {"pieces": tuple(pieces)}


# --- Variant signals (#402) -----------------------------------------------------------


def _variant_measure(key: ThemeKey, board: chess.Board, learner: chess.Color) -> _Measure:
    """A variant theme's signal and whether it holds; ``board`` plays the theme's variant."""
    opponent = not learner
    threshold = _VARIANT_THRESHOLDS[key]
    if isinstance(board, chess.variant.CrazyhouseBoard):
        if key == "pocket_deficit":
            value = variant_themes.pocket_value(board, opponent) - variant_themes.pocket_value(
                board, learner
            )
            return _Measure(float(value), value >= threshold)
        if key == "king_drop_squares":
            mine = len(variant_themes.king_drop_squares(board, learner))
            theirs = len(variant_themes.king_drop_squares(board, opponent))
            holds = mine >= threshold and mine > theirs
            return _Measure(float(mine), holds and bool(variant_themes.pocket(board, opponent)))
        if key == "drop_threats":
            count = len(variant_themes.drop_threats(board, learner))
            return _Measure(float(count), count >= threshold)
    if isinstance(board, chess.variant.ThreeCheckBoard):
        if key == "checks_behind":
            value = variant_themes.checks_given(board, opponent) - variant_themes.checks_given(
                board, learner
            )
            return _Measure(float(value), value >= threshold)
        if key == "check_exposure":
            count = len(variant_themes.checking_moves(board, learner))
            return _Measure(float(count), count >= threshold)
    if isinstance(board, chess.variant.AtomicBoard) and key == "explosion_threats":
        count = len(variant_themes.explosion_threats(board, learner))
        own = variant_themes.explosion_threats(board, opponent)
        return _Measure(float(count), count >= threshold and not own)
    return _Measure(None, False)


def _variant_details(key: ThemeKey, board: chess.Board, learner: chess.Color) -> dict[str, object]:
    opponent = not learner
    if key == "pocket_deficit":
        assert isinstance(board, chess.variant.CrazyhouseBoard)
        return {
            "pockets": ThemePockets(
                learner=tuple(_PIECE_NAMES[p] for p in variant_themes.pocket(board, learner)),
                opponent=tuple(_PIECE_NAMES[p] for p in variant_themes.pocket(board, opponent)),
            )
        }
    if key == "king_drop_squares":
        king = board.king(learner)
        assert king is not None
        squares = variant_themes.king_drop_squares(board, learner)
        return {
            "pieces": (_piece(board, king, learner),),
            "squares": tuple(chess.square_name(s) for s in squares),
        }
    if key == "drop_threats":
        assert isinstance(board, chess.variant.CrazyhouseBoard)
        drops: list[variant_themes.Drop] = []
        for drop in variant_themes.drop_threats(board, learner):
            if drop.check or all(d.target != drop.target for d in drops):
                drops.append(drop)  # one drop per attacked piece, every check
        drops = drops[:DETAIL_LIMIT]
        return {
            "pieces": tuple(
                ThemePiece(
                    owner="opponent",
                    piece=_PIECE_NAMES[drop.piece_type],
                    square=chess.square_name(drop.square),
                )
                for drop in drops
            ),
            "targets": tuple(_piece(board, drop.target, learner) for drop in drops),
        }
    if key == "checks_behind":
        assert isinstance(board, chess.variant.ThreeCheckBoard)
        return {
            "checks": ThemeChecks(
                learner_given=variant_themes.checks_given(board, learner),
                opponent_given=variant_themes.checks_given(board, opponent),
            )
        }
    moves = (
        variant_themes.checking_moves(board, learner)
        if key == "check_exposure"
        else variant_themes.explosion_threats(board, learner)  # type: ignore[arg-type]
    )
    by_piece: dict[int, list[str]] = {}
    for move in moves:
        by_piece.setdefault(move.from_square, []).append(chess.square_name(move.to_square))
    king = board.king(learner)
    assert king is not None
    pieces = [
        _piece(board, square, learner, tuple(dict.fromkeys(targets)))
        for square, targets in list(by_piece.items())[:DETAIL_LIMIT]
    ]
    return {"pieces": (_piece(board, king, learner), *pieces)}


THEME_ORDER: tuple[ThemeKey, ...] = (
    "unsafe_king",
    "weak_pawns",
    "passive_pieces",
    "lagging_development",
)
# The themes measured in each variant; a variant without an entry has none.
VARIANT_THEME_ORDER: dict[VariantName, tuple[ThemeKey, ...]] = {
    STANDARD: THEME_ORDER,
    "crazyhouse": ("pocket_deficit", "king_drop_squares", "drop_threats"),
    "3check": ("checks_behind", "check_exposure"),
    "atomic": ("explosion_threats",),
}


def window_plies(mistake_index: int) -> tuple[int, ...]:
    """Positions (ply counts) of the window, oldest first; the last is before the mistake."""
    return tuple(
        mistake_index - 2 * back
        for back in reversed(range(THEME_WINDOW))
        if mistake_index - 2 * back >= 0
    )


def measure_themes(
    trace: GameTrace, mistake_index: int, learner: chess.Color
) -> tuple[tuple[int, ...], int | None, tuple[ThemeFacts, ...]]:
    """The window, the engine trend across it and the themes that held."""
    window = window_plies(mistake_index)
    start = _learner_score(trace, window[0], learner)
    end = _learner_score(trace, mistake_index + 1, learner)
    trend = end - start if start is not None and end is not None else None
    if len(window) < THEME_MIN_POSITIONS or trend is None or trend > -THEME_TREND_CP:
        return window, trend, ()
    themes = []
    for key in VARIANT_THEME_ORDER.get(trace.variant, ()):
        measures = [_measure(key, trace.boards[ply], learner) for ply in window]
        held = tuple(ply for ply, m in zip(window, measures, strict=True) if m.holds)
        if len(held) < THEME_MIN_POSITIONS or held[-1] != mistake_index:
            continue
        last = trace.boards[mistake_index]
        themes.append(
            ThemeFacts(
                key=key,
                signal=_SIGNALS[key],
                threshold=_threshold(key),
                measured=tuple(m.value for m in measures),
                held=held,
                move_from=trace.boards[held[0]].fullmove_number,
                move_to=last.fullmove_number,
                **_details(key, last, learner),  # type: ignore[arg-type]
            )
        )
    return window, trend, tuple(themes)


def _last_scored(trace: GameTrace) -> int | None:
    return next(
        (i for i in reversed(range(len(trace.scores))) if trace.scores[i] is not None), None
    )


def turning_point(
    trace: GameTrace, mistake_index: int, learner: chess.Color
) -> TurningPointFacts | None:
    """The learner's move from the last level position of a lost game (``LEVEL_CP``).

    Level: the learner's score is ``-LEVEL_CP`` or better. Every scored
    position after the move is worse than level, and the game's last scored
    position is ``-LOST_CP`` or worse; the move itself loses ``TURNING_STEP_CP``
    or more. A game that ends level or better, whose last level position has
    the opponent to move, or whose last level move loses less, has none.
    """
    end_index = _last_scored(trace)
    if end_index is None:
        return None
    end = _learner_score(trace, end_index, learner)
    assert end is not None
    if end > -LOST_CP:
        return None
    level = [
        index
        for index in range(end_index)
        if (score := _learner_score(trace, index, learner)) is not None and score >= -LEVEL_CP
    ]
    if not level or trace.boards[level[-1]].turn != learner:
        return None
    index = level[-1]
    before = _learner_score(trace, index, learner)
    after = _learner_score(trace, index + 1, learner)
    assert before is not None
    if after is None or before - after < TURNING_STEP_CP:
        return None  # crossing the level line by a small step is not a turning point
    board = trace.boards[index]
    move = trace.moves[index]
    return TurningPointFacts(
        ply=index + 1,
        move_number=board.fullmove_number,
        side="white" if board.turn == chess.WHITE else "black",
        san=pgn_san(board, move),
        uci=move.uci(),
        evaluation_before_cp=before,
        evaluation_after_cp=after,
        evaluation_end_cp=end,
        is_mistake=index == mistake_index,
    )


def game_theme_facts(
    trace: GameTrace, before: chess.Board, played: chess.Move
) -> GameThemeFacts | None:
    """Themes and the turning point for the mistake ``played`` from ``before``."""
    index = trace.index_of(before, played)
    if index is None:
        return None
    learner = before.turn
    window, trend, themes = measure_themes(trace, index, learner)
    return GameThemeFacts(
        source=THEME_SOURCE,
        mistake_ply=index + 1,
        window=window,
        evaluation_trend_cp=trend,
        themes=themes,
        turning_point=turning_point(trace, index, learner),
    )


# --- Plain words ---------------------------------------------------------------------


def _joined(parts: Sequence[str], conjunction: str = "and") -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} {conjunction} {parts[-1]}"


def _named(piece: ThemePiece) -> str:
    owner = "your" if piece.owner == "learner" else "your opponent's"
    return f"{owner} {piece.piece} on {piece.square}"


def _span(theme: ThemeFacts) -> str:
    if theme.move_from == theme.move_to:
        return f"on move {theme.move_to}"
    return f"from move {theme.move_from} to move {theme.move_to}"


def _files(files: Sequence[str]) -> str:
    names = [f"{f}-file" for f in files]
    verb = "was" if len(names) == 1 else "were"
    pronoun = "it" if len(names) == 1 else "them"
    return f"the {_joined(names)} next to it {verb} open, with none of your pawns on {pronoun}"


_NUMBERS = ("no", "one", "two", "three")


def _counted(pieces: Sequence[PieceName]) -> str:
    """``a knight and two pawns``; ``nothing`` for an empty hand."""
    if not pieces:
        return "nothing"
    counts: dict[PieceName, int] = {}
    for piece in pieces:
        counts[piece] = counts.get(piece, 0) + 1
    words = [
        f"a {piece}" if n == 1 else f"{_NUMBERS[n] if n < 4 else n} {piece}s"
        for piece, n in counts.items()
    ]
    return _joined(words)


def _checks(n: int) -> str:
    return "no checks" if n == 0 else "one check" if n == 1 else f"{_NUMBERS[n]} checks"


def _variant_phrase(theme: ThemeFacts, span: str) -> str:
    if theme.key == "pocket_deficit":
        assert theme.pockets is not None
        return (
            f"{span}, your opponent held {_counted(theme.pockets.opponent)} in hand to drop, "
            f"and you held {_counted(theme.pockets.learner)}"
        )
    if theme.key == "king_drop_squares":
        king = theme.pieces[0]
        return (
            f"{span}, {_named(king)} had empty squares next to it that none of your other "
            f"pieces guarded, where your opponent could drop a piece: {_joined(theme.squares)}"
        )
    if theme.key == "drop_threats":
        parts = []
        for drop, target in zip(theme.pieces, theme.targets, strict=True):
            aim = "with check" if target.piece == "king" else f"attacking {_named(target)}"
            parts.append(f"a {drop.piece} on {drop.square} {aim}")
        return f"{span}, your opponent had strong drops ready: {_joined(parts)}"
    if theme.key == "checks_behind":
        checks = theme.checks
        assert checks is not None
        left = 3 - checks.opponent_given
        need = (
            "one more check would win them the game"
            if left == 1
            else (f"they needed {_checks(left).replace('checks', 'more checks')} to win")
        )
        return (
            f"{span}, your opponent had given {_checks(checks.opponent_given)} and you "
            f"{_checks(checks.learner_given).replace('no checks', 'none')}: {need}"
        )
    king, *pieces = theme.pieces
    verb = "could give check on" if theme.key == "check_exposure" else "could capture on"
    parts = [
        f"{"your opponent's" if i == 0 else 'their'} {p.piece} on {p.square} "
        f"{verb} {_joined(p.squares, 'or')}"
        for i, p in enumerate(pieces)
    ]
    if theme.key == "check_exposure":
        return f"{span}, {_named(king)} was open to checks: {_joined(parts)}"
    return f"{span}, one capture could blow up {_named(king)}: {_joined(parts)}"


def theme_phrase(theme: ThemeFacts) -> str:
    """The theme in plain chess words, with its pieces and squares (no factor names)."""
    span = _span(theme)
    if theme.key in _VARIANT_KEYS:
        return _variant_phrase(theme, span)
    if theme.key == "unsafe_king":
        king, *attackers = theme.pieces
        parts = []
        if theme.files:
            parts.append(_files(theme.files))
        if attackers:
            names = _joined([_named(p).replace("your opponent's ", "") for p in attackers])
            parts.append(f"your opponent's {names} attacked the squares around it")
        return f"{span}, {_named(king)} had little cover: {_joined(parts)}"
    if theme.key == "weak_pawns":
        groups: dict[str, list[str]] = {}
        for square, kind in zip(theme.squares, theme.weaknesses, strict=True):
            groups.setdefault(kind, []).append(square)
        parts = [
            f"{'an' if kind == 'isolated' else 'a'} {kind} pawn on {squares[0]}"
            if len(squares) == 1
            else f"{kind} pawns on {_joined(squares)}"
            for kind, squares in groups.items()
        ]
        return f"{span}, your pawns were weak: {_joined(parts)}"
    if theme.key == "passive_pieces":
        parts = []
        for piece in theme.pieces:
            if piece.squares:
                parts.append(f"{_named(piece)} could go only to {piece.squares[0]}")
            else:
                parts.append(
                    f"{_named(piece)} had no square that your opponent's pawns did not cover"
                )
        return f"{span}, your pieces had little room: {_joined(parts)}"
    mine = [p for p in theme.pieces if p.owner == "learner"]
    theirs = [p for p in theme.pieces if p.owner == "opponent"]
    home = _joined([f"{p.piece} on {p.square}" for p in mine])
    if theirs:
        other = _joined([f"{p.piece} on {p.square}" for p in theirs])
        rest = f"while your opponent had only the {other} at home"
    else:
        rest = "while your opponent had brought out every knight and bishop"
    return f"{span}, your {home} were still on their starting squares, {rest}"


def turning_point_phrase(point: TurningPointFacts) -> str:
    """The turning point in plain words: the move, and that the score never recovered."""
    dots = "." if point.side == "white" else "..."
    move = f"{point.move_number}{dots}{point.san}"
    return (
        f"the game began to turn against you with your move {move}: "
        "before it the position was level, and after it the engine never again rated it level"
    )


__all__ = [
    "GameThemeFacts",
    "GameTrace",
    "THEME_SOURCE",
    "ThemeFacts",
    "ThemeKey",
    "ThemeChecks",
    "ThemePiece",
    "ThemePockets",
    "TurningPointFacts",
    "VARIANT_THEME_ORDER",
    "free_squares",
    "game_theme_facts",
    "game_trace",
    "king_exposure",
    "measure_themes",
    "passive_pieces",
    "theme_phrase",
    "trace_from_scores",
    "turning_point",
    "turning_point_phrase",
    "undeveloped",
    "weak_pawns",
    "window_plies",
]

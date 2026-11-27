"""The terms of the Stockfish Evaluation Guide, ported to Python.

Source: https://github.com/hxim/Stockfish-Evaluation-Guide (GPL-3.0), commit
63ff375bb5a8b7b2e4f965c3a3de7698fef7616c. Each guide term is a JavaScript
function ``$name(pos, square)``; called without a square it returns its value
for the whole board, mostly the sum over all 64 squares.

Here each guide term has a whole-board function with the guide's name in
snake case (``Weak unopposed pawn`` -> ``weak_unopposed_pawn``). Per-square
helpers end in ``_sq`` and take a padded square index (see ``board.py``). The
arithmetic follows JavaScript: ``/`` is float division and ``x << 0``
truncates toward zero (``_t``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from .board import (
    DIAG_DIRS,
    FLIP,
    KNIGHT_OFFSETS,
    ON_BOARD,
    ORTH_DIRS,
    SIZE,
    SQUARES,
    STRIDE,
    XS,
    YS,
    GuidePosition,
    idx,
)

Pos = GuidePosition
Num = int | float

N = -STRIDE  # one rank up the board (toward rank 8)
S = STRIDE  # one rank down the board


def _t(value: float) -> int:
    """JavaScript ``value << 0`` for the value range of the guide."""
    return int(value)


def _memo[R](fn: Callable[[Pos], R]) -> Callable[[Pos], R]:
    key = fn.__name__

    def wrapper(p: Pos) -> R:
        memo = p.memo
        if key in memo:
            return cast(R, memo[key])
        value = fn(p)
        memo[key] = value
        return value

    wrapper.__name__ = key
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _total(p: Pos, fn: Callable[[Pos, int], Num]) -> Num:
    return sum(fn(p, i) for i in SQUARES)


def _pieces(p: Pos, chars: str) -> list[int]:
    bd = p.bd
    return [i for i in SQUARES if bd[i] in chars and bd[i] != "-"]


# ---------------------------------------------------------------- Helpers


def rank(p: Pos) -> Num:
    return sum(8 - YS[i] for i in SQUARES)


def file(p: Pos) -> Num:
    return sum(1 + XS[i] for i in SQUARES)


def _count(p: Pos, piece: str) -> int:
    bd = p.bd
    return sum(1 for i in SQUARES if bd[i] == piece)


@_memo
def bishop_count(p: Pos) -> int:
    return _count(p, "B")


@_memo
def queen_count(p: Pos) -> int:
    return _count(p, "Q")


@_memo
def pawn_count(p: Pos) -> int:
    return _count(p, "P")


@_memo
def knight_count(p: Pos) -> int:
    return _count(p, "N")


@_memo
def rook_count(p: Pos) -> int:
    return _count(p, "R")


@_memo
def piece_count(p: Pos) -> int:
    bd = p.bd
    return sum(1 for i in SQUARES if bd[i] in "PNBRQK" and bd[i] != "-")


def opposite_bishops(p: Pos) -> int:
    if bishop_count(p) != 1 or bishop_count(p.flip) != 1:
        return 0
    bd = p.bd
    color = [0, 0]
    for i in SQUARES:
        if bd[i] == "B":
            color[0] = (XS[i] + YS[i]) % 2
        if bd[i] == "b":
            color[1] = (XS[i] + YS[i]) % 2
    return 0 if color[0] == color[1] else 1


def king_distance_sq(p: Pos, i: int) -> int:
    k = p.king
    return max(abs(XS[k] - XS[i]), abs(YS[k] - YS[i]))


def king_distance(p: Pos) -> Num:
    return _total(p, king_distance_sq)


def king_ring_at(p: Pos, x: int, y: int, full: bool) -> int:
    """``King ring`` for any (x, y), on or off the board."""
    bd = p.bd
    i = idx(x, y)
    if not full and bd[i + N + 1] == "p" and bd[i + N - 1] == "p":
        return 0
    k = p.enemy_king
    kx, ky = XS[k], YS[k]
    dx, dy = kx - x, ky - y
    if abs(dx) > 2 or abs(dy) > 2:
        return 0
    if (abs(dx) <= 1 or kx in (0, 7)) and (abs(dy) <= 1 or ky in (0, 7)):
        return 1
    return 0


@_memo
def _ring(p: Pos) -> list[int]:
    ring = [0] * SIZE
    for i in SQUARES:
        ring[i] = king_ring_at(p, XS[i], YS[i], False)
    return ring


def king_ring(p: Pos) -> Num:
    ring = _ring(p)
    return sum(ring[i] for i in SQUARES)


def pawn_attacks_span_sq(p: Pos, i: int) -> int:
    bd = p.bd
    flip = p.flip
    x, y = XS[i], YS[i]
    for yy in range(y):
        for side in (-1, 1):
            t = idx(x + side, yy)
            if bd[t] == "p" and (
                yy == y - 1 or (bd[t + S] != "P" and not backward_sq(flip, FLIP[t]))
            ):
                return 1
    return 0


def pawn_attacks_span(p: Pos) -> Num:
    return _total(p, pawn_attacks_span_sq)


# ---------------------------------------------------------------- Pawns


def isolated_sq(p: Pos, i: int) -> int:
    if p.bd[i] != "P":
        return 0
    x = XS[i]
    files = p.pawn_files
    return 0 if files[x] or files[x + 2] else 1


def opposed_sq(p: Pos, i: int) -> int:
    if p.bd[i] != "P":
        return 0
    bd = p.bd
    for t in range(idx(XS[i], 0), i, STRIDE):
        if bd[t] == "p":
            return 1
    return 0


def phalanx_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "P":
        return 0
    return 1 if bd[i - 1] == "P" or bd[i + 1] == "P" else 0


def supported_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "P":
        return 0
    return (1 if bd[i + S - 1] == "P" else 0) + (1 if bd[i + S + 1] == "P" else 0)


def backward_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "P":
        return 0
    for t in range(i, idx(XS[i], 8), STRIDE):
        if bd[t - 1] == "P" or bd[t + 1] == "P":
            return 0
    if bd[i + 2 * N - 1] == "p" or bd[i + 2 * N + 1] == "p" or bd[i + N] == "p":
        return 1
    return 0


def doubled_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "P" or bd[i + S] != "P":
        return 0
    if bd[i + S - 1] == "P" or bd[i + S + 1] == "P":
        return 0
    return 1


def connected_sq(p: Pos, i: int) -> int:
    return 1 if supported_sq(p, i) or phalanx_sq(p, i) else 0


_SEED = (0, 7, 8, 12, 29, 48, 86)


def connected_bonus_sq(p: Pos, i: int) -> int:
    if not connected_sq(p, i):
        return 0
    r = 8 - YS[i]
    if r < 2 or r > 7:
        return 0
    op = opposed_sq(p, i)
    ph = phalanx_sq(p, i)
    su = supported_sq(p, i)
    return _SEED[r - 1] * (2 + ph - op) + 21 * su


def weak_unopposed_pawn_sq(p: Pos, i: int) -> int:
    if opposed_sq(p, i):
        return 0
    if isolated_sq(p, i):
        return 1
    if backward_sq(p, i):
        return 1
    return 0


def weak_lever_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "P":
        return 0
    if bd[i + N - 1] != "p" or bd[i + N + 1] != "p":
        return 0
    if bd[i + S - 1] == "P" or bd[i + S + 1] == "P":
        return 0
    return 1


def blocked_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "P":
        return 0
    y = YS[i]
    if y not in (2, 3) or bd[i + N] != "p":
        return 0
    return 4 - y


def doubled_isolated_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "P" or not isolated_sq(p, i):
        return 0
    x, y = XS[i], YS[i]
    obe = eop = ene = 0
    for yy in range(8):
        t = idx(x, yy)
        if yy > y and bd[t] == "P":
            obe += 1
        if yy < y and bd[t] == "p":
            eop += 1
        if bd[t - 1] == "p" or bd[t + 1] == "p":
            ene += 1
    return 1 if obe > 0 and ene == 0 and eop > 0 else 0


def _pawn_parts(p: Pos, i: int) -> dict[str, tuple[int, int]]:
    """Middle-game and end-game parts of ``Pawns mg``/``Pawns eg`` for one pawn."""
    parts: dict[str, tuple[int, int]] = {}
    if doubled_isolated_sq(p, i):
        parts["Doubled isolated"] = (-11, -56)
    elif isolated_sq(p, i):
        parts["Isolated"] = (-5, -15)
    elif backward_sq(p, i):
        parts["Backward"] = (-9, -24)
    d = doubled_sq(p, i)
    if d:
        parts["Doubled"] = (-11 * d, -56 * d)
    if connected_sq(p, i):
        cb = connected_bonus_sq(p, i)
        parts["Connected bonus"] = (cb, _t(cb * (8 - YS[i] - 3) / 4))
    w = weak_unopposed_pawn_sq(p, i)
    if w:
        parts["Weak unopposed pawn"] = (-13 * w, -27 * w)
    wl = weak_lever_sq(p, i)
    if wl:
        parts["Weak lever"] = (0, -56 * wl)
    b = blocked_sq(p, i)
    if b:
        parts["Blocked"] = ((0, -11, -3)[b], (0, -4, 4)[b])
    return parts


@_memo
def _pawn_contributions(p: Pos) -> dict[str, tuple[int, int]]:
    totals: dict[str, tuple[int, int]] = {}
    for i in _pieces(p, "P"):
        for name, (mg, eg) in _pawn_parts(p, i).items():
            old = totals.get(name, (0, 0))
            totals[name] = (old[0] + mg, old[1] + eg)
    return totals


def pawns_mg(p: Pos) -> Num:
    return sum(mg for mg, _ in _pawn_contributions(p).values())


def pawns_eg(p: Pos) -> Num:
    return sum(eg for _, eg in _pawn_contributions(p).values())


def isolated(p: Pos) -> Num:
    return _total(p, isolated_sq)


def opposed(p: Pos) -> Num:
    return _total(p, opposed_sq)


def phalanx(p: Pos) -> Num:
    return _total(p, phalanx_sq)


def supported(p: Pos) -> Num:
    return _total(p, supported_sq)


def backward(p: Pos) -> Num:
    return _total(p, backward_sq)


def doubled(p: Pos) -> Num:
    return _total(p, doubled_sq)


def connected(p: Pos) -> Num:
    return _total(p, connected_sq)


def connected_bonus(p: Pos) -> Num:
    return _total(p, connected_bonus_sq)


def weak_unopposed_pawn(p: Pos) -> Num:
    return _total(p, weak_unopposed_pawn_sq)


def weak_lever(p: Pos) -> Num:
    return _total(p, weak_lever_sq)


def blocked(p: Pos) -> Num:
    return _total(p, blocked_sq)


def doubled_isolated(p: Pos) -> Num:
    return _total(p, doubled_isolated_sq)


# ---------------------------------------------------------------- Attack


def pinned_direction(p: Pos) -> Num:
    pin = p.pin
    return sum(pin[i] for i in SQUARES)


def pinned(p: Pos) -> Num:
    pin = p.pin
    return sum(1 for i in SQUARES if pin[i] > 0)


def _board_sum(table: list[int]) -> int:
    return sum(table[i] for i in SQUARES)


def knight_attack(p: Pos) -> Num:
    return _board_sum(p.knight_att)


def bishop_xray_attack(p: Pos) -> Num:
    return _board_sum(p.bishop_att)


def rook_xray_attack(p: Pos) -> Num:
    return _board_sum(p.rook_att)


def queen_attack(p: Pos) -> Num:
    return _board_sum(p.queen_att)


def queen_attack_diagonal(p: Pos) -> Num:
    return _board_sum(p.queen_diag_att)


def pawn_attack(p: Pos) -> Num:
    return _board_sum(p.pawn_att)


def king_attack(p: Pos) -> Num:
    return _board_sum(p.king_att)


def attack(p: Pos) -> Num:
    return _board_sum(p.att)


# ---------------------------------------------------------------- Mobility


@_memo
def _mobility_area(p: Pos) -> list[bool]:
    bd = p.bd
    pin = p.pin
    area = [False] * SIZE
    for i in SQUARES:
        c = bd[i]
        if c == "K" or c == "Q":
            continue
        if bd[i + N - 1] == "p" or bd[i + N + 1] == "p":
            continue
        if c == "P" and (8 - YS[i] < 4 or bd[i + N] != "-"):
            continue
        if pin[i] != 0:
            continue
        area[i] = True
    return area


def mobility_area(p: Pos) -> Num:
    area = _mobility_area(p)
    return sum(1 for i in SQUARES if area[i])


@_memo
def _mobility(p: Pos) -> dict[int, int]:
    bd = p.bd
    area = _mobility_area(p)
    out: dict[int, int] = {}
    for i in _pieces(p, "NBRQ"):
        minor = bd[i] in "NB"
        out[i] = sum(1 for t in p.targets[i] if area[t] and not (minor and bd[t] == "Q"))
    return out


def mobility_sq(p: Pos, i: int) -> int:
    return _mobility(p).get(i, 0)


_MOBILITY_BONUS = (
    (
        (-62, -53, -12, -4, 3, 13, 22, 28, 33),
        (-48, -20, 16, 26, 38, 51, 55, 63, 63, 68, 81, 81, 91, 98),
        (-60, -20, 2, 3, 3, 11, 22, 31, 40, 40, 41, 48, 57, 57, 62),
        (
            -30, -12, -8, -9, 20, 23, 23, 35, 38, 53, 64, 65, 65, 66,
            67, 67, 72, 72, 77, 79, 93, 108, 108, 108, 110, 114, 114, 116,
        ),
    ),
    (
        (-81, -56, -31, -16, 5, 11, 17, 20, 25),
        (-59, -23, -3, 13, 24, 42, 54, 57, 65, 73, 78, 86, 88, 97),
        (-78, -17, 23, 39, 70, 99, 103, 121, 134, 139, 158, 164, 168, 169, 172),
        (
            -48, -30, -7, 19, 40, 55, 59, 75, 78, 96, 96, 100, 121, 127,
            131, 133, 136, 141, 147, 150, 151, 168, 168, 171, 182, 182, 192, 219,
        ),
    ),
)  # fmt: skip


def mobility_bonus_sq(p: Pos, i: int, mg: bool = False) -> int:
    c = p.bd[i]
    if c == "-" or c not in "NBRQ":
        return 0
    return _MOBILITY_BONUS[0 if mg else 1]["NBRQ".index(c)][mobility_sq(p, i)]


def mobility(p: Pos) -> Num:
    return sum(_mobility(p).values())


def mobility_bonus(p: Pos) -> Num:
    return sum(mobility_bonus_sq(p, i, False) for i in _mobility(p))


@_memo
def mobility_mg(p: Pos) -> Num:
    return sum(mobility_bonus_sq(p, i, True) for i in _mobility(p))


@_memo
def mobility_eg(p: Pos) -> Num:
    return sum(mobility_bonus_sq(p, i, False) for i in _mobility(p))


# ---------------------------------------------------------------- Material


_PIECE_VALUE = ((124, 781, 825, 1276, 2538), (206, 854, 915, 1380, 2682))


def piece_value_bonus_sq(p: Pos, i: int, mg: bool = False) -> int:
    c = p.bd[i]
    if c == "-" or c not in "PNBRQ":
        return 0
    return _PIECE_VALUE[0 if mg else 1]["PNBRQ".index(c)]


@_memo
def non_pawn_material(p: Pos) -> Num:
    bd = p.bd
    return sum(
        piece_value_bonus_sq(p, i, True) for i in SQUARES if bd[i] in "NBRQ" and bd[i] != "-"
    )


def piece_value_bonus(p: Pos) -> Num:
    return sum(piece_value_bonus_sq(p, i, False) for i in SQUARES)


@_memo
def piece_value_mg(p: Pos) -> Num:
    return sum(piece_value_bonus_sq(p, i, True) for i in SQUARES)


@_memo
def piece_value_eg(p: Pos) -> Num:
    return sum(piece_value_bonus_sq(p, i, False) for i in SQUARES)


_PSQT = (
    (
        ((-175, -92, -74, -73), (-77, -41, -27, -15), (-61, -17, 6, 12), (-35, 8, 40, 49),
         (-34, 13, 44, 51), (-9, 22, 58, 53), (-67, -27, 4, 37), (-201, -83, -56, -26)),
        ((-53, -5, -8, -23), (-15, 8, 19, 4), (-7, 21, -5, 17), (-5, 11, 25, 39),
         (-12, 29, 22, 31), (-16, 6, 1, 11), (-17, -14, 5, 0), (-48, 1, -14, -23)),
        ((-31, -20, -14, -5), (-21, -13, -8, 6), (-25, -11, -1, 3), (-13, -5, -4, -6),
         (-27, -15, -4, 3), (-22, -2, 6, 12), (-2, 12, 16, 18), (-17, -19, -1, 9)),
        ((3, -5, -5, 4), (-3, 5, 8, 12), (-3, 6, 13, 7), (4, 5, 9, 8),
         (0, 14, 12, 5), (-4, 10, 6, 8), (-5, 6, 10, 8), (-2, -2, 1, -2)),
        ((271, 327, 271, 198), (278, 303, 234, 179), (195, 258, 169, 120), (164, 190, 138, 98),
         (154, 179, 105, 70), (123, 145, 81, 31), (88, 120, 65, 33), (59, 89, 45, -1)),
    ),
    (
        ((-96, -65, -49, -21), (-67, -54, -18, 8), (-40, -27, -8, 29), (-35, -2, 13, 28),
         (-45, -16, 9, 39), (-51, -44, -16, 17), (-69, -50, -51, 12), (-100, -88, -56, -17)),
        ((-57, -30, -37, -12), (-37, -13, -17, 1), (-16, -1, -2, 10), (-20, -6, 0, 17),
         (-17, -1, -14, 15), (-30, 6, 4, 6), (-31, -20, -1, 1), (-46, -42, -37, -24)),
        ((-9, -13, -10, -9), (-12, -9, -1, -2), (6, -8, -2, -6), (-6, 1, -9, 7),
         (-5, 8, 7, -6), (6, 1, -7, 10), (4, 5, 20, -5), (18, 0, 19, 13)),
        ((-69, -57, -47, -26), (-55, -31, -22, -4), (-39, -18, -9, 3), (-23, -3, 13, 24),
         (-29, -6, 9, 21), (-38, -18, -12, 1), (-50, -27, -24, -8), (-75, -52, -43, -36)),
        ((1, 45, 85, 76), (53, 100, 133, 135), (88, 130, 169, 175), (103, 156, 172, 172),
         (96, 166, 199, 199), (92, 172, 184, 191), (47, 121, 116, 131), (11, 59, 73, 78)),
    ),
)  # fmt: skip
_PAWN_PSQT = (
    ((0, 0, 0, 0, 0, 0, 0, 0), (3, 3, 10, 19, 16, 19, 7, -5), (-9, -15, 11, 15, 32, 22, 5, -22),
     (-4, -23, 6, 20, 40, 17, 4, -8), (13, 0, -13, 1, 11, -2, -13, 5),
     (5, -12, -7, 22, -8, -5, -15, -8), (-7, 7, -3, -13, 5, -16, 10, -8), (0, 0, 0, 0, 0, 0, 0, 0)),
    ((0, 0, 0, 0, 0, 0, 0, 0), (-10, -6, 10, 0, 14, 7, -5, -19), (-10, -10, -10, 4, 4, 3, -6, -4),
     (6, -2, -8, -4, -13, -12, -10, -9), (10, 5, 4, -5, -5, -5, 14, 9),
     (28, 20, 21, 28, 30, 7, 6, 13), (0, -11, 12, 21, 25, 19, 4, 7), (0, 0, 0, 0, 0, 0, 0, 0)),
)  # fmt: skip


def psqt_bonus_sq(p: Pos, i: int, mg: bool = False) -> int:
    c = p.bd[i]
    if c == "-" or c not in "PNBRQK":
        return 0
    x, y = XS[i], YS[i]
    phase = 0 if mg else 1
    if c == "P":
        return _PAWN_PSQT[phase][7 - y][x]
    return _PSQT[phase]["NBRQK".index(c)][7 - y][min(x, 7 - x)]


def psqt_bonus(p: Pos) -> Num:
    return sum(psqt_bonus_sq(p, i, False) for i in SQUARES)


@_memo
def psqt_mg(p: Pos) -> Num:
    return sum(psqt_bonus_sq(p, i, True) for i in SQUARES)


@_memo
def psqt_eg(p: Pos) -> Num:
    return sum(psqt_bonus_sq(p, i, False) for i in SQUARES)


# ---------------------------------------------------------------- Imbalance


_QO = (
    (0,),
    (40, 38),
    (32, 255, -62),
    (0, 104, 4, 0),
    (-26, -2, 47, 105, -208),
    (-189, 24, 117, 133, -134, -6),
)
_QT = (
    (0,),
    (36, 0),
    (9, 63, 0),
    (59, 65, 42, 0),
    (46, 39, 24, -24, 0),
    (97, 100, -42, 137, 268, 0),
)


@_memo
def _imbalance_counts(p: Pos) -> tuple[list[int], list[int]]:
    bd = p.bd
    ours = [0] * 6
    theirs = [0] * 6
    for i in SQUARES:
        c = bd[i]
        j = "XPNBRQ".find(c)
        if j > 0:
            ours[j] += 1
            continue
        j = "xpnbrq".find(c)
        if j > 0:
            theirs[j] += 1
    return ours, theirs


def imbalance_sq(p: Pos, i: int) -> int:
    j = "XPNBRQ".find(p.bd[i])
    if j <= 0:
        return 0
    ours, theirs = _imbalance_counts(p)
    v = 0
    for k in range(1, j + 1):
        v += _QO[j][k] * ours[k] + _QT[j][k] * theirs[k]
    if theirs[3] > 1:
        v += _QT[j][0]
    if ours[3] > 1:
        v += _QO[j][0]
    return v


@_memo
def imbalance(p: Pos) -> Num:
    return sum(imbalance_sq(p, i) for i in _pieces(p, "PNBRQ"))


def bishop_pair(p: Pos) -> Num:
    return 1438 if bishop_count(p) >= 2 else 0


@_memo
def imbalance_total(p: Pos) -> Num:
    v = imbalance(p) - imbalance(p.flip) + bishop_pair(p) - bishop_pair(p.flip)
    return _t(v / 16)


# ---------------------------------------------------------------- Pieces


def outpost_square_sq(p: Pos, i: int) -> int:
    r = 8 - YS[i]
    if r < 4 or r > 6:
        return 0
    bd = p.bd
    if bd[i + S - 1] != "P" and bd[i + S + 1] != "P":
        return 0
    if pawn_attacks_span_sq(p, i):
        return 0
    return 1


def outpost_sq(p: Pos, i: int) -> int:
    c = p.bd[i]
    if c != "N" and c != "B":
        return 0
    return 1 if outpost_square_sq(p, i) else 0


def reachable_outpost_sq(p: Pos, i: int) -> int:
    bd = p.bd
    c = bd[i]
    if c != "B" and c != "N":
        return 0
    v = 0
    targets = set(p.targets[i])
    for x in range(8):
        for y in range(2, 5):
            t = idx(x, y)
            if bd[t] in "PNBRQK" and bd[t] != "-":
                continue
            if t in targets and outpost_square_sq(p, t):
                support = 2 if bd[t + S - 1] == "P" or bd[t + S + 1] == "P" else 1
                v = max(v, support)
    return v


def minor_behind_pawn_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "B" and bd[i] != "N":
        return 0
    return 1 if bd[i + N].upper() == "P" else 0


def bishop_pawns_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "B":
        return 0
    c = (XS[i] + YS[i]) % 2
    v = 0
    blocked_count = 0
    for t in SQUARES:
        if bd[t] == "P":
            if c == (XS[t] + YS[t]) % 2:
                v += 1
            if 1 < XS[t] < 6 and bd[t + N] != "-":
                blocked_count += 1
    return v * (blocked_count + (0 if p.pawn_att[i] > 0 else 1))


def rook_on_file_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "R":
        return 0
    open_file = 1
    for t in range(idx(XS[i], 0), idx(XS[i], 8), STRIDE):
        if bd[t] == "P":
            return 0
        if bd[t] == "p":
            open_file = 0
    return open_file + 1


def trapped_rook_sq(p: Pos, i: int) -> int:
    if p.bd[i] != "R":
        return 0
    if rook_on_file_sq(p, i):
        return 0
    if mobility_sq(p, i) > 3:
        return 0
    kx = XS[p.king]
    if (kx < 4) != (XS[i] < kx):
        return 0
    return 1


def weak_queen_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "Q":
        return 0
    for dirs, slider in ((ORTH_DIRS, "r"), (DIAG_DIRS, "b")):
        for dx, dy in dirs:
            step = dx + STRIDE * dy
            count = 0
            t = i
            for _ in range(7):
                t += step
                c = bd[t]
                if c == "x":
                    break
                if c == slider and count == 1:
                    return 1
                if c != "-":
                    count += 1
    return 0


def king_protector_sq(p: Pos, i: int) -> int:
    c = p.bd[i]
    if c != "N" and c != "B":
        return 0
    return king_distance_sq(p, i)


def long_diagonal_bishop_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "B":
        return 0
    x, y = XS[i], YS[i]
    if x - y != 0 and x - (7 - y) != 0:
        return 0
    if min(x, 7 - x) > 2:
        return 0
    x1, y1 = x, y
    for _ in range(min(x1, 7 - x1), 4):
        c = bd[idx(x1, y1)]
        if c == "p" or c == "P":
            return 0
        x1 += 1 if x1 < 4 else -1
        y1 += 1 if y1 < 4 else -1
    return 1


def rook_on_queen_file_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "R":
        return 0
    for t in range(idx(XS[i], 0), idx(XS[i], 8), STRIDE):
        if bd[t] == "Q" or bd[t] == "q":
            return 1
    return 0


def rook_on_king_ring_sq(p: Pos, i: int) -> int:
    if p.bd[i] != "R":
        return 0
    if king_attackers_count_sq(p, i) > 0:
        return 0
    ring = _ring(p)
    for t in range(idx(XS[i], 0), idx(XS[i], 8), STRIDE):
        if ring[t]:
            return 1
    return 0


def bishop_on_king_ring_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "B":
        return 0
    if king_attackers_count_sq(p, i) > 0:
        return 0
    ring = _ring(p)
    for dx, dy in DIAG_DIRS:
        step = dx + STRIDE * dy
        t = i
        for _ in range(7):
            t += step
            c = bd[t]
            if c == "x":
                break
            if ring[t]:
                return 1
            if c == "P" or c == "p":
                break
    return 0


def queen_infiltration_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "Q" or YS[i] > 3:
        return 0
    if bd[i + N + 1] == "p" or bd[i + N - 1] == "p":
        return 0
    if pawn_attacks_span_sq(p, i):
        return 0
    return 1


def outpost_total_sq(p: Pos, i: int) -> int:
    bd = p.bd
    c = bd[i]
    if c != "N" and c != "B":
        return 0
    knight = c == "N"
    if not outpost_sq(p, i):
        if not knight:
            return 0
        return 1 if reachable_outpost_sq(p, i) else 0
    x, y = XS[i], YS[i]
    if knight and (x < 2 or x > 5):
        ea = 0
        cnt = 0
        for t in SQUARES:
            if bd[t] == "-" or bd[t] not in "nbrqk":
                continue
            ax, ay = abs(x - XS[t]), abs(y - YS[t])
            if (ax == 2 and ay == 1) or (ax == 1 and ay == 2):
                ea = 1
            if (XS[t] < 4) == (x < 4):
                cnt += 1
        if not ea and cnt <= 1:
            return 2
    return 4 if knight else 3


def bishop_xray_pawns_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "B":
        return 0
    x, y = XS[i], YS[i]
    return sum(1 for t in SQUARES if bd[t] == "p" and abs(x - XS[t]) == abs(y - YS[t]))


def _piece_parts(p: Pos, i: int) -> dict[str, tuple[int, int]]:
    """Middle-game and end-game parts of ``Pieces mg``/``Pieces eg`` for one piece."""
    c = p.bd[i]
    castling = 1 if p.castling[0] or p.castling[1] else 2
    ot = outpost_total_sq(p, i)
    mbp = minor_behind_pawn_sq(p, i)
    bp = bishop_pawns_sq(p, i)
    bxp = bishop_xray_pawns_sq(p, i)
    rqf = rook_on_queen_file_sq(p, i)
    rkr = rook_on_king_ring_sq(p, i)
    bkr = bishop_on_king_ring_sq(p, i)
    rof = rook_on_file_sq(p, i)
    tr = trapped_rook_sq(p, i)
    wq = weak_queen_sq(p, i)
    qi = queen_infiltration_sq(p, i)
    kp = king_protector_sq(p, i)
    ld = long_diagonal_bishop_sq(p, i)
    return {
        "Outpost total": ((0, 31, -7, 30, 56)[ot], (0, 22, 36, 23, 36)[ot]),
        "Minor behind pawn": (18 * mbp, 3 * mbp),
        "Bishop pawns": (-3 * bp, -7 * bp),
        "Bishop xray pawns": (-4 * bxp, -5 * bxp),
        "Rook on queen file": (6 * rqf, 11 * rqf),
        "Rook on king ring": (16 * rkr, 0),
        "Bishop on king ring": (24 * bkr, 0),
        "Rook on file": ((0, 19, 48)[rof], (0, 7, 29)[rof]),
        "Trapped rook": (-tr * 55 * castling, -tr * 13 * castling),
        "Weak queen": (-56 * wq, -15 * wq),
        "Queen infiltration": (-2 * qi, 14 * qi),
        "King protector": (-(8 if c == "N" else 6) * kp, -9 * kp),
        "Long diagonal bishop": (45 * ld, 0),
    }


@_memo
def _piece_contributions(p: Pos) -> dict[str, tuple[int, int]]:
    totals: dict[str, tuple[int, int]] = {}
    for i in _pieces(p, "NBRQ"):
        for name, (mg, eg) in _piece_parts(p, i).items():
            old = totals.get(name, (0, 0))
            totals[name] = (old[0] + mg, old[1] + eg)
    return totals


def pieces_mg(p: Pos) -> Num:
    return sum(mg for mg, _ in _piece_contributions(p).values())


def pieces_eg(p: Pos) -> Num:
    return sum(eg for _, eg in _piece_contributions(p).values())


def outpost(p: Pos) -> Num:
    return _total(p, outpost_sq)


def outpost_square(p: Pos) -> Num:
    return _total(p, outpost_square_sq)


def reachable_outpost(p: Pos) -> Num:
    return _total(p, reachable_outpost_sq)


def minor_behind_pawn(p: Pos) -> Num:
    return _total(p, minor_behind_pawn_sq)


def bishop_pawns(p: Pos) -> Num:
    return _total(p, bishop_pawns_sq)


def rook_on_file(p: Pos) -> Num:
    return _total(p, rook_on_file_sq)


def trapped_rook(p: Pos) -> Num:
    return _total(p, trapped_rook_sq)


def weak_queen(p: Pos) -> Num:
    return _total(p, weak_queen_sq)


def king_protector(p: Pos) -> Num:
    return _total(p, king_protector_sq)


def long_diagonal_bishop(p: Pos) -> Num:
    return _total(p, long_diagonal_bishop_sq)


def rook_on_queen_file(p: Pos) -> Num:
    return _total(p, rook_on_queen_file_sq)


def rook_on_king_ring(p: Pos) -> Num:
    return _total(p, rook_on_king_ring_sq)


def bishop_on_king_ring(p: Pos) -> Num:
    return _total(p, bishop_on_king_ring_sq)


def queen_infiltration(p: Pos) -> Num:
    return _total(p, queen_infiltration_sq)


def outpost_total(p: Pos) -> Num:
    return _total(p, outpost_total_sq)


def bishop_xray_pawns(p: Pos) -> Num:
    return _total(p, bishop_xray_pawns_sq)


# ---------------------------------------------------------------- King


def pawnless_flank(p: Pos) -> Num:
    bd = p.bd
    pawns = [0] * 8
    for i in SQUARES:
        if bd[i] == "P" or bd[i] == "p":
            pawns[XS[i]] += 1
    kx = XS[p.enemy_king]
    if kx == 0:
        total = pawns[0] + pawns[1] + pawns[2]
    elif kx < 3:
        total = pawns[0] + pawns[1] + pawns[2] + pawns[3]
    elif kx < 5:
        total = pawns[2] + pawns[3] + pawns[4] + pawns[5]
    elif kx < 7:
        total = pawns[4] + pawns[5] + pawns[6] + pawns[7]
    else:
        total = pawns[5] + pawns[6] + pawns[7]
    return 1 if total == 0 else 0


@_memo
def _storm_tables(p: Pos) -> tuple[list[list[int]], list[list[int]]]:
    """Per file and start rank: the nearest shelter pawn ('us') and storm pawn ('them')."""
    bd = p.bd
    us_from = [[0] * 9 for _ in range(8)]
    them_from = [[0] * 9 for _ in range(8)]
    for x in range(8):
        us = 0
        them = 0
        for y in range(7, -1, -1):
            t = idx(x, y)
            if bd[t] == "p" and bd[t + S - 1] != "P" and bd[t + S + 1] != "P":
                us = y
            if bd[t] == "P":
                them = y
            us_from[x][y] = us
            them_from[x][y] = them
    return us_from, them_from


_WEAKNESS = (
    (-6, 81, 93, 58, 39, 18, 25),
    (-43, 61, 35, -49, -29, -11, -63),
    (-10, 75, 23, -2, 32, 3, -45),
    (-39, -13, -29, -52, -48, -67, -166),
)
_UNBLOCKED_STORM = (
    (85, -289, -166, 97, 50, 45, 50),
    (46, -25, 122, 45, 37, -10, 20),
    (-6, 51, 168, 34, -2, -22, -14),
    (-15, -11, 101, 4, 11, -15, -29),
)
_BLOCKED_STORM = ((0, 0, 76, -10, -7, -4, -1), (0, 0, 78, 15, 10, 6, 2))


def strength_square_sq(p: Pos, i: int) -> int:
    us_from, _ = _storm_tables(p)
    y = YS[i]
    kx = min(6, max(1, XS[i]))
    v = 5
    for x in range(kx - 1, kx + 2):
        us = us_from[x][y]
        if us < 7:
            v += _WEAKNESS[min(x, 7 - x)][us]
    return v


def storm_square_sq(p: Pos, i: int, eg: bool = False) -> int:
    us_from, them_from = _storm_tables(p)
    y = YS[i]
    kx = min(6, max(1, XS[i]))
    v = 0
    ev = 5
    for x in range(kx - 1, kx + 2):
        us = us_from[x][y]
        them = them_from[x][y]
        if us > 0 and them == us + 1:
            v += _BLOCKED_STORM[0][them]
            ev += _BLOCKED_STORM[1][them]
        else:
            v += _UNBLOCKED_STORM[min(x, 7 - x)][them]
    return ev if eg else v


def strength_square(p: Pos) -> Num:
    return _total(p, strength_square_sq)


def storm_square(p: Pos) -> Num:
    return _total(p, storm_square_sq)


@_memo
def _shelter(p: Pos) -> tuple[int, int, int]:
    """Shelter strength, storm and end-game storm of the best king square."""
    bd = p.bd
    w, s, e = 0, 1024, 0
    for i in SQUARES:
        x, y = XS[i], YS[i]
        if (
            bd[i] == "k"
            or (p.castling[2] and x == 6 and y == 0)
            or (p.castling[3] and x == 2 and y == 0)
        ):
            w1 = strength_square_sq(p, i)
            s1 = storm_square_sq(p, i)
            if s1 - w1 < s - w:
                w, s, e = w1, s1, storm_square_sq(p, i, True)
    return w, s, e


def shelter_strength(p: Pos) -> Num:
    return _shelter(p)[0]


def shelter_storm(p: Pos) -> Num:
    return _shelter(p)[1]


def endgame_shelter(p: Pos) -> Num:
    return _shelter(p)[2]


def king_pawn_distance(p: Pos) -> Num:
    bd = p.bd
    k = p.king
    kx, ky = XS[k], YS[k]
    v = 6
    for i in SQUARES:
        if bd[i] == "P":
            dist = max(abs(XS[i] - kx), abs(YS[i] - ky))
            if dist < v:
                v = dist
    return v


def _ray_hits_king(bd: list[str], i: int, dirs: tuple[tuple[int, int], ...]) -> bool:
    for dx, dy in dirs:
        step = dx + STRIDE * dy
        t = i
        for _ in range(7):
            t += step
            c = bd[t]
            if c == "k":
                return True
            if c != "-" and c != "q":
                break
    return False


def check_sq(p: Pos, i: int, kind: int | None = None) -> int:
    bd = p.bd
    queen = p.queen_att[i] and (kind is None or kind == 3)
    if (p.rook_att[i] and kind in (None, 2, 4)) or queen:
        if _ray_hits_king(bd, i, ORTH_DIRS):
            return 1
    if (p.bishop_att[i] and kind in (None, 1, 4)) or queen:
        if _ray_hits_king(bd, i, DIAG_DIRS):
            return 1
    if p.knight_att[i] and kind in (None, 0, 4):
        for o in KNIGHT_OFFSETS:
            if bd[i + o] == "k":
                return 1
    return 0


def safe_check_sq(p: Pos, i: int, kind: int | None = None) -> int:
    c = p.bd[i]
    if c != "-" and c in "PNBRQK":
        return 0
    if not check_sq(p, i, kind):
        return 0
    if kind == 3 and safe_check_sq(p, i, 2):
        return 0
    if kind == 1 and safe_check_sq(p, i, 3):
        return 0
    f = FLIP[i]
    flip = p.flip
    if (not flip.att[f] or (weak_squares_sq(p, i) and p.att[i] > 1)) and (
        kind != 3 or not flip.queen_att[f]
    ):
        return 1
    return 0


def _safe_checks(p: Pos, kind: int | None) -> int:
    key = ("safe_check", kind)
    memo = p.memo
    if key not in memo:
        memo[key] = sum(safe_check_sq(p, i, kind) for i in SQUARES)
    return cast(int, memo[key])


def check(p: Pos) -> Num:
    return sum(check_sq(p, i) for i in SQUARES)


def safe_check(p: Pos) -> Num:
    return _safe_checks(p, None)


@_memo
def _king_attackers(p: Pos) -> dict[int, Num]:
    bd = p.bd
    ring = _ring(p)
    out: dict[int, Num] = {}
    for i in _pieces(p, "PNBRQ"):
        if bd[i] == "P":
            v: Num = 0
            for d in (-1, 1):
                x = XS[i] + d
                if 0 <= x <= 7 and king_ring_at(p, x, YS[i] - 1, True):
                    v = v + (0.5 if bd[i + 2 * d] == "P" else 1)
            out[i] = v
        else:
            out[i] = 1 if any(ring[t] for t in p.targets[i]) else 0
    return out


def king_attackers_count_sq(p: Pos, i: int) -> Num:
    return _king_attackers(p).get(i, 0)


def king_attackers_count(p: Pos) -> Num:
    return sum(_king_attackers(p).values())


def king_attackers_weight_sq(p: Pos, i: int) -> int:
    if king_attackers_count_sq(p, i):
        return (0, 81, 52, 44, 10)["PNBRQ".index(p.bd[i])]
    return 0


def king_attackers_weight(p: Pos) -> Num:
    return sum(king_attackers_weight_sq(p, i) for i in _king_attackers(p))


@_memo
def _king_zone(p: Pos) -> frozenset[int]:
    k = p.enemy_king
    return frozenset(
        k + dx + STRIDE * dy
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if (dx or dy) and ON_BOARD[k + dx + STRIDE * dy]
    )


def king_attacks_sq(p: Pos, i: int) -> int:
    c = p.bd[i]
    if c == "-" or c not in "NBRQ":
        return 0
    if king_attackers_count_sq(p, i) == 0:
        return 0
    zone = _king_zone(p)
    return sum(1 for t in p.targets[i] if t in zone)


def king_attacks(p: Pos) -> Num:
    return sum(king_attacks_sq(p, i) for i in _pieces(p, "NBRQ"))


def weak_squares_sq(p: Pos, i: int) -> int:
    if p.att[i]:
        flip = p.flip
        f = FLIP[i]
        a = flip.att[f]
        if a >= 2:
            return 0
        if a == 0:
            return 1
        if flip.king_att[f] or flip.queen_att[f]:
            return 1
    return 0


def weak_squares(p: Pos) -> Num:
    return _total(p, weak_squares_sq)


def weak_bonus_sq(p: Pos, i: int) -> int:
    return 1 if weak_squares_sq(p, i) and _ring(p)[i] else 0


def weak_bonus(p: Pos) -> Num:
    return _total(p, weak_bonus_sq)


def unsafe_checks_sq(p: Pos, i: int) -> int:
    for kind in (0, 1, 2):
        if check_sq(p, i, kind) and _safe_checks(p, kind) == 0:
            return 1
    return 0


def unsafe_checks(p: Pos) -> Num:
    return _total(p, unsafe_checks_sq)


def knight_defender_sq(p: Pos, i: int) -> int:
    return 1 if p.knight_att[i] and p.king_att[i] else 0


def knight_defender(p: Pos) -> Num:
    return _total(p, knight_defender_sq)


def blockers_for_king_sq(p: Pos, i: int) -> int:
    return 1 if p.flip.pin[FLIP[i]] else 0


def blockers_for_king(p: Pos) -> Num:
    return _total(p, blockers_for_king_sq)


def _in_king_flank(p: Pos, i: int) -> bool:
    if YS[i] > 4:
        return False
    x = XS[i]
    kx = XS[p.enemy_king]
    if kx == 0 and x > 2:
        return False
    if kx < 3 and x > 3:
        return False
    if 3 <= kx < 5 and (x < 2 or x > 5):
        return False
    if kx >= 5 and x < 4:
        return False
    if kx == 7 and x < 5:
        return False
    return True


def flank_attack_sq(p: Pos, i: int) -> int:
    if not _in_king_flank(p, i):
        return 0
    a = p.att[i]
    if not a:
        return 0
    return 2 if a > 1 else 1


@_memo
def flank_attack(p: Pos) -> Num:
    return _total(p, flank_attack_sq)


def flank_defense_sq(p: Pos, i: int) -> int:
    if not _in_king_flank(p, i):
        return 0
    return 1 if p.flip.att[FLIP[i]] > 0 else 0


def flank_defense(p: Pos) -> Num:
    return _total(p, flank_defense_sq)


@_memo
def king_danger(p: Pos) -> Num:
    flip = p.flip
    count = king_attackers_count(p)
    weight = king_attackers_weight(p)
    kfa = flank_attack(p)
    v: Num = (
        count * weight
        + 69 * king_attacks(p)
        + 185 * weak_bonus(p)
        - 100 * (1 if knight_defender(flip) > 0 else 0)
        + 148 * unsafe_checks(p)
        + 98 * blockers_for_king(p)
        - 4 * flank_defense(p)
        + _t(3 * kfa * kfa / 8)
        - 873 * (0 if queen_count(p) > 0 else 1)
        - _t(6 * (shelter_strength(p) - shelter_storm(p)) / 8)
        + mobility_mg(p)
        - mobility_mg(flip)
        + 37
        + _t(772 * min(_safe_checks(p, 3), 1.45))
        + _t(1084 * min(_safe_checks(p, 2), 1.75))
        + _t(645 * min(_safe_checks(p, 1), 1.50))
        + _t(792 * min(_safe_checks(p, 0), 1.62))
    )
    return v if v > 100 else 0


def _king_parts(p: Pos) -> dict[str, tuple[int, int]]:
    kd = king_danger(p)
    pf = pawnless_flank(p)
    return {
        "Shelter strength": (-int(shelter_strength(p)), 0),
        "Shelter storm": (int(shelter_storm(p)), 0),
        "King danger": (_t(kd * kd / 4096), _t(kd / 16)),
        "Flank attack": (8 * int(flank_attack(p)), 0),
        "Pawnless flank": (17 * int(pf), 95 * int(pf)),
        "King pawn distance": (0, -16 * int(king_pawn_distance(p))),
        "Endgame shelter": (0, int(endgame_shelter(p))),
    }


@_memo
def _king_contributions(p: Pos) -> dict[str, tuple[int, int]]:
    return _king_parts(p)


def king_mg(p: Pos) -> Num:
    return sum(mg for mg, _ in _king_contributions(p).values())


def king_eg(p: Pos) -> Num:
    return sum(eg for _, eg in _king_contributions(p).values())


# ---------------------------------------------------------------- Threats


def safe_pawn_at(p: Pos, i: int) -> int:
    if p.bd[i] != "P":
        return 0
    if p.att[i]:
        return 1
    if not p.flip.att[FLIP[i]]:
        return 1
    return 0


def safe_pawn(p: Pos) -> Num:
    return _total(p, safe_pawn_at)


def threat_safe_pawn_sq(p: Pos, i: int) -> int:
    c = p.bd[i]
    if c == "-" or c not in "nbrq":
        return 0
    if not p.pawn_att[i]:
        return 0
    if safe_pawn_at(p, i + S - 1) or safe_pawn_at(p, i + S + 1):
        return 1
    return 0


def threat_safe_pawn(p: Pos) -> Num:
    return _total(p, threat_safe_pawn_sq)


def weak_enemies_sq(p: Pos, i: int) -> int:
    bd = p.bd
    c = bd[i]
    if c == "-" or c not in "pnbrqk":
        return 0
    if bd[i + N - 1] == "p" or bd[i + N + 1] == "p":
        return 0
    a = p.att[i]
    if not a:
        return 0
    if a <= 1 and p.flip.att[FLIP[i]] > 1:
        return 0
    return 1


def weak_enemies(p: Pos) -> Num:
    return _total(p, weak_enemies_sq)


def minor_threat_sq(p: Pos, i: int) -> int:
    bd = p.bd
    kind = "pnbrqk".find(bd[i])
    if kind < 0 or bd[i] == "-":
        return 0
    if not p.knight_att[i] and not p.bishop_att[i]:
        return 0
    defended = (
        bd[i + N - 1] == "p" or bd[i + N + 1] == "p" or (p.att[i] <= 1 and p.flip.att[FLIP[i]] > 1)
    )
    if (bd[i] == "p" or not defended) and not weak_enemies_sq(p, i):
        return 0
    return kind + 1


def minor_threat(p: Pos) -> Num:
    return _total(p, minor_threat_sq)


def rook_threat_sq(p: Pos, i: int) -> int:
    kind = "pnbrqk".find(p.bd[i])
    if kind < 0 or p.bd[i] == "-":
        return 0
    if not weak_enemies_sq(p, i):
        return 0
    if not p.rook_att[i]:
        return 0
    return kind + 1


def rook_threat(p: Pos) -> Num:
    return _total(p, rook_threat_sq)


def hanging_sq(p: Pos, i: int) -> int:
    if not weak_enemies_sq(p, i):
        return 0
    if p.bd[i] != "p" and p.att[i] > 1:
        return 1
    if not p.flip.att[FLIP[i]]:
        return 1
    return 0


def hanging(p: Pos) -> Num:
    return _total(p, hanging_sq)


def king_threat_sq(p: Pos, i: int) -> int:
    c = p.bd[i]
    if c == "-" or c not in "pnbrq":
        return 0
    if not weak_enemies_sq(p, i):
        return 0
    if not p.king_att[i]:
        return 0
    return 1


def king_threat(p: Pos) -> Num:
    return _total(p, king_threat_sq)


def pawn_push_threat_sq(p: Pos, i: int) -> int:
    bd = p.bd
    c = bd[i]
    if c == "-" or c not in "pnbrqk":
        return 0
    flip = p.flip
    for ix in (-1, 1):
        j = i + ix
        if (
            bd[j + 2 * S] == "P"
            and bd[j + S] == "-"
            and bd[j - 1] != "p"
            and bd[j + 1] != "p"
            and (p.att[j + S] or not flip.att[FLIP[j + S]])
        ):
            return 1
        if (
            YS[i] == 3
            and bd[j + 3 * S] == "P"
            and bd[j + 2 * S] == "-"
            and bd[j + S] == "-"
            and bd[j - 1] != "p"
            and bd[j + 1] != "p"
            and (p.att[j + S] or not flip.att[FLIP[j + S]])
        ):
            return 1
    return 0


def pawn_push_threat(p: Pos) -> Num:
    return _total(p, pawn_push_threat_sq)


def slider_on_queen_sq(p: Pos, i: int) -> int:
    flip = p.flip
    if queen_count(flip) != 1:
        return 0
    bd = p.bd
    if bd[i] == "P":
        return 0
    if bd[i + N - 1] == "p" or bd[i + N + 1] == "p":
        return 0
    if p.att[i] <= 1:
        return 0
    if not _mobility_area(p)[i]:
        return 0
    f = FLIP[i]
    diagonal = flip.queen_diag_att[f]
    v = 2 if queen_count(p) == 0 else 1
    if diagonal and p.bishop_att[i]:
        return v
    if not diagonal and p.rook_att[i] and flip.queen_att[f]:
        return v
    return 0


def slider_on_queen(p: Pos) -> Num:
    return _total(p, slider_on_queen_sq)


def knight_on_queen_sq(p: Pos, i: int) -> int:
    bd = p.bd
    queens = [t for t in SQUARES if bd[t] == "q"]
    if len(queens) != 1:
        return 0
    if bd[i] == "P":
        return 0
    if bd[i + N - 1] == "p" or bd[i + N + 1] == "p":
        return 0
    flip = p.flip
    if p.att[i] <= 1 and flip.att[FLIP[i]] > 1:
        return 0
    if not _mobility_area(p)[i]:
        return 0
    if not p.knight_att[i]:
        return 0
    v = 2 if queen_count(p) == 0 else 1
    q = queens[0]
    dx, dy = abs(XS[q] - XS[i]), abs(YS[q] - YS[i])
    if (dx == 2 and dy == 1) or (dx == 1 and dy == 2):
        return v
    return 0


def knight_on_queen(p: Pos) -> Num:
    return _total(p, knight_on_queen_sq)


def restricted_sq(p: Pos, i: int) -> int:
    a = p.att[i]
    if a == 0:
        return 0
    flip = p.flip
    f = FLIP[i]
    b = flip.att[f]
    if not b:
        return 0
    if flip.pawn_att[f] > 0:
        return 0
    if b > 1 and a == 1:
        return 0
    return 1


def restricted(p: Pos) -> Num:
    return _total(p, restricted_sq)


def weak_queen_protection_sq(p: Pos, i: int) -> int:
    if not weak_enemies_sq(p, i):
        return 0
    return 1 if p.flip.queen_att[FLIP[i]] else 0


def weak_queen_protection(p: Pos) -> Num:
    return _total(p, weak_queen_protection_sq)


_MINOR_THREAT = ((0, 5, 57, 77, 88, 79, 0), (0, 32, 41, 56, 119, 161, 0))
_ROOK_THREAT = ((0, 3, 37, 42, 0, 58, 0), (0, 46, 68, 60, 38, 41, 0))


@_memo
def _threat_contributions(p: Pos) -> dict[str, tuple[int, int]]:
    h = int(hanging(p))
    kt = 1 if king_threat(p) > 0 else 0
    ppt = int(pawn_push_threat(p))
    tsp = int(threat_safe_pawn(p))
    soq = int(slider_on_queen(p))
    koq = int(knight_on_queen(p))
    r = int(restricted(p))
    wqp = int(weak_queen_protection(p))
    mt_mg = mt_eg = rt_mg = rt_eg = 0
    for i in SQUARES:
        m = minor_threat_sq(p, i)
        if m:
            mt_mg += _MINOR_THREAT[0][m]
            mt_eg += _MINOR_THREAT[1][m]
        rt = rook_threat_sq(p, i)
        if rt:
            rt_mg += _ROOK_THREAT[0][rt]
            rt_eg += _ROOK_THREAT[1][rt]
    return {
        "Hanging": (69 * h, 36 * h),
        "King threat": (24 * kt, 89 * kt),
        "Pawn push threat": (48 * ppt, 39 * ppt),
        "Threat safe pawn": (173 * tsp, 94 * tsp),
        "Slider on queen": (60 * soq, 18 * soq),
        "Knight on queen": (16 * koq, 11 * koq),
        "Restricted": (7 * r, 7 * r),
        "Weak queen protection": (14 * wqp, 0),
        "Minor threat": (mt_mg, mt_eg),
        "Rook threat": (rt_mg, rt_eg),
    }


def threats_mg(p: Pos) -> Num:
    return sum(mg for mg, _ in _threat_contributions(p).values())


def threats_eg(p: Pos) -> Num:
    return sum(eg for _, eg in _threat_contributions(p).values())


# ---------------------------------------------------------------- Passed pawns


def candidate_passed_sq(p: Pos, i: int) -> int:
    bd = p.bd
    if bd[i] != "P":
        return 0
    x, y = XS[i], YS[i]
    ty1 = ty2 = 8
    for yy in range(y - 1, -1, -1):
        t = idx(x, yy)
        if bd[t] == "P":
            return 0
        if bd[t] == "p":
            ty1 = yy
        if bd[t - 1] == "p" or bd[t + 1] == "p":
            ty2 = yy
    if ty1 == 8 and ty2 >= y - 1:
        return 1
    if ty2 < y - 2 or ty1 < y - 1:
        return 0
    if ty2 >= y and ty1 == y - 1 and y < 4:
        if bd[i + S - 1] == "P" and bd[i - 1] != "p" and bd[i + N - 2] != "p":
            return 1
        if bd[i + S + 1] == "P" and bd[i + 1] != "p" and bd[i + N + 2] != "p":
            return 1
    if bd[i + N] == "p":
        return 0
    lever = (1 if bd[i + N - 1] == "p" else 0) + (1 if bd[i + N + 1] == "p" else 0)
    leverpush = (1 if bd[i + 2 * N - 1] == "p" else 0) + (1 if bd[i + 2 * N + 1] == "p" else 0)
    phalanx_count = (1 if bd[i - 1] == "P" else 0) + (1 if bd[i + 1] == "P" else 0)
    if lever - supported_sq(p, i) > 1:
        return 0
    if leverpush - phalanx_count > 0:
        return 0
    if lever > 0 and leverpush > 0:
        return 0
    return 1


@_memo
def candidate_passed(p: Pos) -> Num:
    return sum(candidate_passed_sq(p, i) for i in _pieces(p, "P"))


def passed_leverable_sq(p: Pos, i: int) -> int:
    if not candidate_passed_sq(p, i):
        return 0
    bd = p.bd
    if bd[i + N] != "p":
        return 1
    flip = p.flip
    for d in (-1, 1):
        j = i + d
        c = bd[j]
        if (
            bd[j + S] == "P"
            and (c == "-" or c not in "pnbrqk")
            and (p.att[j] > 0 or flip.att[FLIP[j]] <= 1)
        ):
            return 1
    return 0


@_memo
def _passers(p: Pos) -> list[int]:
    return [i for i in _pieces(p, "P") if passed_leverable_sq(p, i)]


def passed_leverable(p: Pos) -> Num:
    return len(_passers(p))


def king_proximity_sq(p: Pos, i: int) -> int:
    if not passed_leverable_sq(p, i):
        return 0
    r = 8 - YS[i] - 1
    w = 5 * r - 13 if r > 2 else 0
    if w <= 0:
        return 0
    x, y = XS[i], YS[i]
    k = p.enemy_king
    own = p.king
    v = _t(min(max(abs(YS[k] - y + 1), abs(XS[k] - x)), 5) * 19 / 4) * w
    v -= min(max(abs(YS[own] - y + 1), abs(XS[own] - x)), 5) * 2 * w
    if y > 1:
        v -= min(max(abs(YS[own] - y + 2), abs(XS[own] - x)), 5) * w
    return v


def passed_block_sq(p: Pos, i: int) -> int:
    if not passed_leverable_sq(p, i):
        return 0
    if 8 - YS[i] < 4:
        return 0
    bd = p.bd
    if bd[i + N] != "-":
        return 0
    r = 8 - YS[i] - 1
    w = 5 * r - 13 if r > 2 else 0
    flip = p.flip
    x, y = XS[i], YS[i]
    defended = unsafe = wunsafe = defended1 = unsafe1 = 0
    for yy in range(y - 1, -1, -1):
        t = idx(x, yy)
        if p.att[t]:
            defended += 1
        if flip.att[FLIP[t]]:
            unsafe += 1
        if flip.att[FLIP[t - 1]]:
            wunsafe += 1
        if flip.att[FLIP[t + 1]]:
            wunsafe += 1
        if yy == y - 1:
            defended1 = defended
            unsafe1 = unsafe
    for yy in range(y + 1, 8):
        c = bd[idx(x, yy)]
        if c == "R" or c == "Q":
            defended1 = defended = y
        if c == "r" or c == "q":
            unsafe1 = unsafe = y
    if unsafe == 0 and wunsafe == 0:
        k = 35
    elif unsafe == 0:
        k = 20
    elif unsafe1 == 0:
        k = 9
    else:
        k = 0
    k += 5 if defended1 != 0 else 0
    return k * w


def passed_file_sq(p: Pos, i: int) -> int:
    if not passed_leverable_sq(p, i):
        return 0
    f = 1 + XS[i]
    return min(f - 1, 8 - f)


def passed_rank_sq(p: Pos, i: int) -> int:
    if not passed_leverable_sq(p, i):
        return 0
    return 8 - YS[i] - 1


_PASSED_RANK = ((0, 10, 17, 15, 62, 168, 276), (0, 28, 33, 41, 72, 177, 260))


@_memo
def _passed_contributions(p: Pos) -> dict[str, tuple[int, int]]:
    rank_mg = rank_eg = block = file_mg = file_eg = prox = 0
    for i in _passers(p):
        pr = passed_rank_sq(p, i)
        rank_mg += _PASSED_RANK[0][pr]
        rank_eg += _PASSED_RANK[1][pr]
        block += passed_block_sq(p, i)
        pf = passed_file_sq(p, i)
        file_mg -= 11 * pf
        file_eg -= 8 * pf
        prox += king_proximity_sq(p, i)
    return {
        "Passed rank": (rank_mg, rank_eg),
        "Passed block": (block, block),
        "Passed file": (file_mg, file_eg),
        "King proximity": (0, prox),
    }


def passed_mg(p: Pos) -> Num:
    return sum(mg for mg, _ in _passed_contributions(p).values())


def passed_eg(p: Pos) -> Num:
    return sum(eg for _, eg in _passed_contributions(p).values())


def king_proximity(p: Pos) -> Num:
    return sum(king_proximity_sq(p, i) for i in _passers(p))


def passed_block(p: Pos) -> Num:
    return sum(passed_block_sq(p, i) for i in _passers(p))


def passed_file(p: Pos) -> Num:
    return sum(passed_file_sq(p, i) for i in _passers(p))


def passed_rank(p: Pos) -> Num:
    return sum(passed_rank_sq(p, i) for i in _passers(p))


# ---------------------------------------------------------------- Space


def space_area_sq(p: Pos, i: int) -> int:
    x, y = XS[i], YS[i]
    r = 8 - y
    f = 1 + x
    bd = p.bd
    if not (2 <= r <= 4 and 3 <= f <= 6):
        return 0
    if bd[i] == "P" or bd[i + N - 1] == "p" or bd[i + N + 1] == "p":
        return 0
    v = 1
    if (bd[i + N] == "P" or bd[i + 2 * N] == "P" or bd[i + 3 * N] == "P") and not p.flip.att[
        FLIP[i]
    ]:
        v += 1
    return v


def space_area(p: Pos) -> Num:
    return _total(p, space_area_sq)


@_memo
def space(p: Pos) -> Num:
    if non_pawn_material(p) + non_pawn_material(p.flip) < 12222:
        return 0
    bd = p.bd
    piece_total = 0
    blocked_count = 0
    for i in SQUARES:
        c = bd[i]
        if c != "-" and c in "PNBRQK":
            piece_total += 1
        if c == "P" and (
            bd[i + N] == "p" or (bd[i + 2 * N - 1] == "p" and bd[i + 2 * N + 1] == "p")
        ):
            blocked_count += 1
        if c == "p" and (
            bd[i + S] == "P" or (bd[i + 2 * S - 1] == "P" and bd[i + 2 * S + 1] == "P")
        ):
            blocked_count += 1
    weight = piece_total - 3 + min(blocked_count, 9)
    return _t(space_area(p) * weight * weight / 16)


# ---------------------------------------------------------------- Winnable


@_memo
def winnable(p: Pos) -> Num:
    bd = p.bd
    pawns = 0
    flanks = [0, 0]
    for x in range(8):
        has_pawn = False
        for y in range(8):
            c = bd[idx(x, y)]
            if c == "P" or c == "p":
                has_pawn = True
                pawns += 1
        if has_pawn:
            flanks[0 if x < 4 else 1] = 1
    kx = (XS[p.king], XS[p.enemy_king])
    ky = (YS[p.king], YS[p.enemy_king])
    flip = p.flip
    passed_count = candidate_passed(p) + candidate_passed(flip)
    both_flanks = 1 if flanks[0] and flanks[1] else 0
    outflanking = abs(kx[0] - kx[1]) - abs(ky[0] - ky[1])
    pure_pawn = 1 if non_pawn_material(p) + non_pawn_material(flip) == 0 else 0
    almost_unwinnable = 1 if outflanking < 0 and both_flanks == 0 else 0
    infiltration = 1 if ky[0] < 4 or ky[1] > 3 else 0
    return (
        9 * passed_count
        + 12 * pawns
        + 9 * outflanking
        + 21 * both_flanks
        + 24 * infiltration
        + 51 * pure_pawn
        - 43 * almost_unwinnable
        - 110
    )


def _sign(v: Num) -> int:
    return 1 if v > 0 else -1 if v < 0 else 0


def winnable_total_mg(p: Pos, v: Num | None = None) -> Num:
    if v is None:
        v = middle_game_evaluation(p, True)
    return _sign(v) * max(min(winnable(p) + 50, 0), -abs(v))


def winnable_total_eg(p: Pos, v: Num | None = None) -> Num:
    if v is None:
        v = end_game_evaluation(p, True)
    return _sign(v) * max(winnable(p), -abs(v))


# ---------------------------------------------------------------- Main evaluation


def _side_mg(p: Pos) -> Num:
    return (
        piece_value_mg(p)
        + psqt_mg(p)
        + pawns_mg(p)
        + pieces_mg(p)
        + mobility_mg(p)
        + threats_mg(p)
        + passed_mg(p)
        + space(p)
        + king_mg(p)
    )


def _side_eg(p: Pos) -> Num:
    return (
        piece_value_eg(p)
        + psqt_eg(p)
        + pawns_eg(p)
        + pieces_eg(p)
        + mobility_eg(p)
        + threats_eg(p)
        + passed_eg(p)
        + king_eg(p)
    )


def middle_game_evaluation(p: Pos, nowinnable: bool = False) -> Num:
    key = ("middle_game_evaluation", nowinnable)
    memo = p.memo
    if key in memo:
        return cast(Num, memo[key])
    v = _side_mg(p) - _side_mg(p.flip) + imbalance_total(p)
    if not nowinnable:
        v += winnable_total_mg(p, v)
    memo[key] = v
    return v


def end_game_evaluation(p: Pos, nowinnable: bool = False) -> Num:
    key = ("end_game_evaluation", nowinnable)
    memo = p.memo
    if key in memo:
        return cast(Num, memo[key])
    v = _side_eg(p) - _side_eg(p.flip) + imbalance_total(p)
    if not nowinnable:
        v += winnable_total_eg(p, v)
    memo[key] = v
    return v


def scale_factor(p: Pos, eg: Num | None = None) -> Num:
    if eg is None:
        eg = end_game_evaluation(p)
    flip = p.flip
    pw, pb = (p, flip) if eg > 0 else (flip, p)
    sf = 64
    pc_w, pc_b = pawn_count(pw), pawn_count(pb)
    qc_w, qc_b = queen_count(pw), queen_count(pb)
    bc_w, bc_b = bishop_count(pw), bishop_count(pb)
    nc_w, nc_b = knight_count(pw), knight_count(pb)
    npm_w, npm_b = non_pawn_material(pw), non_pawn_material(pb)
    bishop_mg, rook_mg = 825, 1276
    if pc_w == 0 and npm_w - npm_b <= bishop_mg:
        sf = 0 if npm_w < rook_mg else 4 if npm_b <= bishop_mg else 14
    if sf == 64:
        ob = opposite_bishops(p)
        if ob and npm_w == bishop_mg and npm_b == bishop_mg:
            sf = 22 + 4 * int(candidate_passed(pw))
        elif ob:
            sf = 22 + 3 * piece_count(pw)
        else:
            if npm_w == rook_mg and npm_b == rook_mg and pc_w - pc_b <= 1:
                bdw, bdb = pw.bd, pb.bd
                flank = [0, 0]
                pawn_by_king = 0
                for i in SQUARES:
                    if bdw[i] == "P":
                        flank[1 if XS[i] < 4 else 0] = 1
                    if bdb[i] == "K":
                        for o in (-17, -16, -15, -1, 0, 1, 15, 16, 17):
                            if bdb[i + o] == "P":
                                pawn_by_king = 1
                if flank[0] != flank[1] and pawn_by_king:
                    return 36
            if qc_w + qc_b == 1:
                sf = 37 + 3 * (bc_b + nc_b if qc_w == 1 else bc_w + nc_w)
            else:
                sf = min(sf, 36 + 7 * pc_w)
    return sf


def phase(p: Pos) -> Num:
    midgame_limit, endgame_limit = 15258, 3915
    npm = non_pawn_material(p) + non_pawn_material(p.flip)
    npm = max(endgame_limit, min(npm, midgame_limit))
    return _t((npm - endgame_limit) * 128 / (midgame_limit - endgame_limit))


def tempo(p: Pos) -> Num:
    return 28 if p.white_to_move else -28


def rule50(p: Pos) -> Num:
    return p.rule50


def main_evaluation(p: Pos) -> Num:
    mg = middle_game_evaluation(p)
    eg = end_game_evaluation(p)
    ph = phase(p)
    eg = eg * scale_factor(p, eg) / 64
    v = _t((mg * ph + _t(eg * (128 - ph))) / 128)
    v = _t(v / 16) * 16
    v += int(tempo(p))
    return _t(v * (100 - int(rule50(p))) / 100)


# ---------------------------------------------------------------- Registry

TermFn = Callable[[Pos], Num]

# (guide name, guide group, whole-board function), in the guide's order.
TERMS: tuple[tuple[str, str, TermFn], ...] = (
    ("Main evaluation", "", main_evaluation),
    ("Isolated", "Pawns", isolated),
    ("Opposed", "Pawns", opposed),
    ("Rank", "Helpers", rank),
    ("File", "Helpers", file),
    ("Phalanx", "Pawns", phalanx),
    ("Supported", "Pawns", supported),
    ("Backward", "Pawns", backward),
    ("Doubled", "Pawns", doubled),
    ("Connected", "Pawns", connected),
    ("Middle game evaluation", "", middle_game_evaluation),
    ("End game evaluation", "", end_game_evaluation),
    ("Scale factor", "", scale_factor),
    ("Phase", "", phase),
    ("Imbalance", "Imbalance", imbalance),
    ("Bishop count", "Helpers", bishop_count),
    ("Bishop pair", "Imbalance", bishop_pair),
    ("Pinned direction", "Attack", pinned_direction),
    ("Mobility", "Mobility", mobility),
    ("Mobility area", "Mobility", mobility_area),
    ("Mobility bonus", "Mobility", mobility_bonus),
    ("Knight attack", "Attack", knight_attack),
    ("Bishop xray attack", "Attack", bishop_xray_attack),
    ("Rook xray attack", "Attack", rook_xray_attack),
    ("Queen attack", "Attack", queen_attack),
    ("Outpost", "Pieces", outpost),
    ("Outpost square", "Pieces", outpost_square),
    ("Reachable outpost", "Pieces", reachable_outpost),
    ("Minor behind pawn", "Pieces", minor_behind_pawn),
    ("Bishop pawns", "Pieces", bishop_pawns),
    ("Rook on file", "Pieces", rook_on_file),
    ("Trapped rook", "Pieces", trapped_rook),
    ("Weak queen", "Pieces", weak_queen),
    ("Space area", "Space", space_area),
    ("Pawn attack", "Attack", pawn_attack),
    ("King attack", "Attack", king_attack),
    ("Attack", "Attack", attack),
    ("Non pawn material", "Material", non_pawn_material),
    ("Safe pawn", "Threats", safe_pawn),
    ("Threat safe pawn", "Threats", threat_safe_pawn),
    ("Weak enemies", "Threats", weak_enemies),
    ("Minor threat", "Threats", minor_threat),
    ("Rook threat", "Threats", rook_threat),
    ("Hanging", "Threats", hanging),
    ("King threat", "Threats", king_threat),
    ("Pawn push threat", "Threats", pawn_push_threat),
    ("Candidate passed", "Passed pawns", candidate_passed),
    ("King proximity", "Passed pawns", king_proximity),
    ("Passed block", "Passed pawns", passed_block),
    ("Passed file", "Passed pawns", passed_file),
    ("Passed rank", "Passed pawns", passed_rank),
    ("Pawnless flank", "King", pawnless_flank),
    ("Strength square", "King", strength_square),
    ("Storm square", "King", storm_square),
    ("Shelter strength", "King", shelter_strength),
    ("Shelter storm", "King", shelter_storm),
    ("King pawn distance", "King", king_pawn_distance),
    ("Check", "King", check),
    ("Safe check", "King", safe_check),
    ("Queen count", "Helpers", queen_count),
    ("King attackers count", "King", king_attackers_count),
    ("King attackers weight", "King", king_attackers_weight),
    ("King attacks", "King", king_attacks),
    ("Weak bonus", "King", weak_bonus),
    ("Weak squares", "King", weak_squares),
    ("Winnable", "Winnable", winnable),
    ("Unsafe checks", "King", unsafe_checks),
    ("Tempo", "", tempo),
    ("Pawn count", "Helpers", pawn_count),
    ("Connected bonus", "Pawns", connected_bonus),
    ("Mobility mg", "Mobility", mobility_mg),
    ("Mobility eg", "Mobility", mobility_eg),
    ("Piece value bonus", "Material", piece_value_bonus),
    ("Psqt bonus", "Material", psqt_bonus),
    ("Piece value mg", "Material", piece_value_mg),
    ("Piece value eg", "Material", piece_value_eg),
    ("Psqt mg", "Material", psqt_mg),
    ("Psqt eg", "Material", psqt_eg),
    ("King protector", "Pieces", king_protector),
    ("Knight count", "Helpers", knight_count),
    ("Imbalance total", "Imbalance", imbalance_total),
    ("Weak unopposed pawn", "Pawns", weak_unopposed_pawn),
    ("Rook count", "Helpers", rook_count),
    ("Opposite bishops", "Helpers", opposite_bishops),
    ("King distance", "Helpers", king_distance),
    ("Long diagonal bishop", "Pieces", long_diagonal_bishop),
    ("Queen attack diagonal", "Attack", queen_attack_diagonal),
    ("Pinned", "Attack", pinned),
    ("King ring", "Helpers", king_ring),
    ("Slider on queen", "Threats", slider_on_queen),
    ("Knight on queen", "Threats", knight_on_queen),
    ("Outpost total", "Pieces", outpost_total),
    ("Restricted", "Threats", restricted),
    ("Knight defender", "King", knight_defender),
    ("Endgame shelter", "King", endgame_shelter),
    ("Space", "Space", space),
    ("Weak lever", "Pawns", weak_lever),
    ("Blockers for king", "King", blockers_for_king),
    ("Rook on queen file", "Pieces", rook_on_queen_file),
    ("Winnable total mg", "Winnable", winnable_total_mg),
    ("Winnable total eg", "Winnable", winnable_total_eg),
    ("Flank attack", "King", flank_attack),
    ("Flank defense", "King", flank_defense),
    ("King danger", "King", king_danger),
    ("King mg", "King", king_mg),
    ("King eg", "King", king_eg),
    ("Weak queen protection", "Threats", weak_queen_protection),
    ("Threats mg", "Threats", threats_mg),
    ("Threats eg", "Threats", threats_eg),
    ("Passed leverable", "Passed pawns", passed_leverable),
    ("Passed mg", "Passed pawns", passed_mg),
    ("Passed eg", "Passed pawns", passed_eg),
    ("Piece count", "Helpers", piece_count),
    ("Bishop xray pawns", "Pieces", bishop_xray_pawns),
    ("Rook on king ring", "Pieces", rook_on_king_ring),
    ("Bishop on king ring", "Pieces", bishop_on_king_ring),
    ("Queen infiltration", "Pieces", queen_infiltration),
    ("Pieces mg", "Pieces", pieces_mg),
    ("Pieces eg", "Pieces", pieces_eg),
    ("Blocked", "Pawns", blocked),
    ("Pawn attacks span", "Helpers", pawn_attacks_span),
    ("Doubled isolated", "Pawns", doubled_isolated),
    ("Pawns mg", "Pawns", pawns_mg),
    ("Pawns eg", "Pawns", pawns_eg),
    ("Rule50", "", rule50),
)

"""Factor report: every guide term, the evaluation groups and their parts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import chess

from . import terms as t
from .board import GuidePosition, positions_from_board

# Stockfish 12 PawnValueEg: one pawn in the guide's internal units.
PAWN_VALUE_EG = 206

Num = int | float


@dataclass(frozen=True, slots=True)
class TermValue:
    """The whole-board value of one guide term, for each side.

    ``white`` is the term on the position; ``black`` is the term on the
    colour-flipped position. Most terms are counts or flags; the ``mg``/``eg``
    terms and the evaluation terms are in internal units.
    """

    name: str
    group: str
    white: Num
    black: Num


@dataclass(frozen=True, slots=True)
class Contribution:
    """One named part of the evaluation, in internal units.

    A side value belongs to the side that earns it, as in the guide. For
    example, White's ``King danger`` measures White's attack on the black king.
    Net-only parts (``Imbalance total``, ``Winnable``) have no side values.
    """

    name: str
    group: str
    white_mg: int | None
    white_eg: int | None
    black_mg: int | None
    black_eg: int | None
    net_mg: int
    net_eg: int
    tapered: float


@dataclass(frozen=True, slots=True)
class GroupValue:
    """An evaluation group: the sum of its contributions."""

    name: str
    white_mg: int | None
    white_eg: int | None
    black_mg: int | None
    black_eg: int | None
    net_mg: int
    net_eg: int
    tapered: float


@dataclass(frozen=True, slots=True)
class FactorReport:
    """The guide's evaluation of one position, from White's point of view.

    ``evaluation`` is the guide's ``Main evaluation`` (rounded to 16 units,
    with tempo and the fifty-move scaling). ``tapered`` values mix middle game
    and end game by ``phase`` (0..128) and scale the end game by
    ``scale_factor`` (0..64), without rounding.
    """

    fen: str
    phase: int
    scale_factor: int
    middle_game: int
    end_game: int
    tempo: int
    rule50: int
    evaluation: int
    terms: Mapping[str, TermValue]
    groups: Mapping[str, GroupValue]
    contributions: tuple[Contribution, ...]

    @staticmethod
    def to_pawns(value: float) -> float:
        """Convert internal units to pawns (Stockfish 12 PawnValueEg)."""
        return value / PAWN_VALUE_EG

    def to_dict(self) -> dict[str, Any]:
        return {
            "fen": self.fen,
            "phase": self.phase,
            "scale_factor": self.scale_factor,
            "middle_game": self.middle_game,
            "end_game": self.end_game,
            "tempo": self.tempo,
            "rule50": self.rule50,
            "evaluation": self.evaluation,
            "terms": {
                name: {"group": v.group, "white": v.white, "black": v.black}
                for name, v in self.terms.items()
            },
            "groups": {name: _row(g) for name, g in self.groups.items()},
            "contributions": [
                {"name": c.name, "group": c.group, **_row(c)} for c in self.contributions
            ],
        }


def _row(v: Contribution | GroupValue) -> dict[str, Any]:
    return {
        "white_mg": v.white_mg,
        "white_eg": v.white_eg,
        "black_mg": v.black_mg,
        "black_eg": v.black_eg,
        "net_mg": v.net_mg,
        "net_eg": v.net_eg,
        "tapered": v.tapered,
    }


# Evaluation groups in the order of the guide's Middle game evaluation.
GROUPS: tuple[str, ...] = (
    "Material",
    "Imbalance",
    "Pawns",
    "Pieces",
    "Mobility",
    "Threats",
    "Passed pawns",
    "Space",
    "King",
    "Winnable",
)


# Every contribution, in a fixed order. Each guide name is a term in TERMS.
PARTS: tuple[tuple[str, str], ...] = (
    ("Piece value bonus", "Material"),
    ("Psqt bonus", "Material"),
    ("Imbalance total", "Imbalance"),
    ("Doubled isolated", "Pawns"),
    ("Isolated", "Pawns"),
    ("Backward", "Pawns"),
    ("Doubled", "Pawns"),
    ("Connected bonus", "Pawns"),
    ("Weak unopposed pawn", "Pawns"),
    ("Weak lever", "Pawns"),
    ("Blocked", "Pawns"),
    ("Outpost total", "Pieces"),
    ("Minor behind pawn", "Pieces"),
    ("Bishop pawns", "Pieces"),
    ("Bishop xray pawns", "Pieces"),
    ("Rook on queen file", "Pieces"),
    ("Rook on king ring", "Pieces"),
    ("Bishop on king ring", "Pieces"),
    ("Rook on file", "Pieces"),
    ("Trapped rook", "Pieces"),
    ("Weak queen", "Pieces"),
    ("Queen infiltration", "Pieces"),
    ("King protector", "Pieces"),
    ("Long diagonal bishop", "Pieces"),
    ("Mobility bonus", "Mobility"),
    ("Hanging", "Threats"),
    ("King threat", "Threats"),
    ("Pawn push threat", "Threats"),
    ("Threat safe pawn", "Threats"),
    ("Slider on queen", "Threats"),
    ("Knight on queen", "Threats"),
    ("Restricted", "Threats"),
    ("Weak queen protection", "Threats"),
    ("Minor threat", "Threats"),
    ("Rook threat", "Threats"),
    ("Passed rank", "Passed pawns"),
    ("Passed block", "Passed pawns"),
    ("Passed file", "Passed pawns"),
    ("King proximity", "Passed pawns"),
    ("Space", "Space"),
    ("Shelter strength", "King"),
    ("Shelter storm", "King"),
    ("King danger", "King"),
    ("Flank attack", "King"),
    ("Pawnless flank", "King"),
    ("King pawn distance", "King"),
    ("Endgame shelter", "King"),
    ("Winnable", "Winnable"),
)
_NET_ONLY = frozenset({"Imbalance total", "Winnable"})


def _side_parts(p: GuidePosition) -> dict[str, tuple[int, int]]:
    """Middle-game and end-game value of each side part, earned by ``p``'s White."""
    parts: dict[str, tuple[int, int]] = {
        "Piece value bonus": (int(t.piece_value_mg(p)), int(t.piece_value_eg(p))),
        "Psqt bonus": (int(t.psqt_mg(p)), int(t.psqt_eg(p))),
        "Mobility bonus": (int(t.mobility_mg(p)), int(t.mobility_eg(p))),
        "Space": (int(t.space(p)), 0),
    }
    parts.update(t._pawn_contributions(p))
    parts.update(t._piece_contributions(p))
    parts.update(t._threat_contributions(p))
    parts.update(t._passed_contributions(p))
    parts.update(t._king_contributions(p))
    return parts


def evaluate_factors(board: chess.Board | str) -> FactorReport:
    """Evaluate ``board`` (a python-chess board or a FEN) with the guide's terms.

    Pure Python: no engine binary and no network access.
    """
    if isinstance(board, str):
        board = chess.Board(board)
    white, black = positions_from_board(board)
    phase = int(t.phase(white))
    mg = int(t.middle_game_evaluation(white))
    eg = int(t.end_game_evaluation(white))
    scale = int(t.scale_factor(white, eg))

    def taper(net_mg: int, net_eg: int) -> float:
        return (net_mg * phase + net_eg * scale / 64 * (128 - phase)) / 128

    white_parts = _side_parts(white)
    black_parts = _side_parts(black)
    imb = int(t.imbalance_total(white))
    net_only = {
        "Imbalance total": (imb, imb),
        "Winnable": (
            int(mg - t.middle_game_evaluation(white, True)),
            int(eg - t.end_game_evaluation(white, True)),
        ),
    }
    contributions: list[Contribution] = []
    for name, group in PARTS:
        if name in _NET_ONLY:
            nmg, neg = net_only[name]
            contributions.append(
                Contribution(name, group, None, None, None, None, nmg, neg, taper(nmg, neg))
            )
            continue
        wmg, weg = white_parts.get(name, (0, 0))
        bmg, beg = black_parts.get(name, (0, 0))
        contributions.append(
            Contribution(
                name, group, wmg, weg, bmg, beg, wmg - bmg, weg - beg, taper(wmg - bmg, weg - beg)
            )
        )

    groups: dict[str, GroupValue] = {}
    for group in GROUPS:
        members = [c for c in contributions if c.group == group]
        net_mg = sum(c.net_mg for c in members)
        net_eg = sum(c.net_eg for c in members)
        sides = members[0].white_mg is not None

        def side(
            attr: str, members: list[Contribution] = members, sides: bool = sides
        ) -> int | None:
            return sum(int(getattr(c, attr)) for c in members) if sides else None

        groups[group] = GroupValue(
            group,
            side("white_mg"),
            side("white_eg"),
            side("black_mg"),
            side("black_eg"),
            net_mg,
            net_eg,
            taper(net_mg, net_eg),
        )

    values = {name: TermValue(name, group, fn(white), fn(black)) for name, group, fn in t.TERMS}
    return FactorReport(
        fen=board.fen(),
        phase=phase,
        scale_factor=scale,
        middle_game=mg,
        end_game=eg,
        tempo=int(t.tempo(white)),
        rule50=int(t.rule50(white)),
        evaluation=int(t.main_evaluation(white)),
        terms=MappingProxyType(values),
        groups=MappingProxyType(groups),
        contributions=tuple(contributions),
    )

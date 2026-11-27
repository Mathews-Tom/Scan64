"""Chess variants Scan64 supports, and the PGN ``Variant`` tag that names them.

Rules come from python-chess variant boards. A game's variant is read from its
stored PGN headers, so the ``Variant`` tag is the single source of truth and no
schema column duplicates it. Standard chess is ``"chess"`` (python-chess's
``uci_variant``); every other name is the ``UCI_Variant`` value that
Fairy-Stockfish declares for that variant.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, get_args

import chess
import chess.variant

VariantName = Literal[
    "chess",
    "crazyhouse",
    "3check",
    "atomic",
    "antichess",
    "kingofthehill",
    "horde",
    "racingkings",
]
STANDARD: VariantName = "chess"
SUPPORTED_VARIANTS: tuple[VariantName, ...] = get_args(VariantName)

_BOARDS: dict[VariantName, type[chess.Board]] = {
    name: board
    for board in chess.variant.VARIANTS
    if (name := board.uci_variant) in SUPPORTED_VARIANTS
}
assert set(_BOARDS) == set(SUPPORTED_VARIANTS), "python-chess lacks a supported variant"

# Shown to the player wherever a feature is standard-chess only.
NOT_COVERED = "{name} games are not covered by {feature} yet; Scan64 covers standard chess there."


class UnsupportedVariantError(ValueError):
    """The PGN or FEN names a variant Scan64 does not support."""


def variant_from_tag(tag: str | None) -> VariantName:
    """The supported variant a PGN ``Variant`` tag names; absent means standard."""
    if tag is None:
        return STANDARD
    try:
        board = chess.variant.find_variant(tag)
    except ValueError:
        raise UnsupportedVariantError(_unsupported(tag)) from None
    name = board.uci_variant
    if name not in SUPPORTED_VARIANTS:
        raise UnsupportedVariantError(_unsupported(tag))
    return name


def variant_of(headers: Mapping[str, str]) -> VariantName:
    """The variant of a game from its PGN headers."""
    return variant_from_tag(headers.get("Variant"))


def board_class(variant: VariantName) -> type[chess.Board]:
    return _BOARDS[variant]


def board_variant(board: chess.Board) -> VariantName:
    """The supported variant a python-chess board plays; Chess960 is refused."""
    if board.chess960:
        raise UnsupportedVariantError(_unsupported("Chess960"))
    name = board.uci_variant
    if name not in SUPPORTED_VARIANTS or type(board) is not _BOARDS[name]:
        raise UnsupportedVariantError(_unsupported(type(board).__name__))
    return name


def display_name(variant: VariantName) -> str:
    """The PGN tag spelling, for messages (``Three-check``, ``King of the Hill``)."""
    return "Standard" if variant == STANDARD else _BOARDS[variant].aliases[0]


def not_covered(variant: VariantName, feature: str) -> str:
    return NOT_COVERED.format(name=display_name(variant), feature=feature)


def _unsupported(name: str) -> str:
    names = ", ".join(display_name(variant) for variant in SUPPORTED_VARIANTS)
    return f"Variant '{name}' is not supported; Scan64 supports {names}"


def stored_variant(headers: Mapping[str, str]) -> str:
    """A stored game's variant for display: the supported name, else its tag as written.

    Games imported before variant support kept any ``Variant`` tag; listing them
    must show that tag rather than fail.
    """
    tag = headers.get("Variant")
    try:
        return variant_from_tag(tag)
    except UnsupportedVariantError:
        assert tag is not None
        return tag

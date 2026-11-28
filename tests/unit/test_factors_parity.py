"""Parity of the factor module with the Stockfish Evaluation Guide's JavaScript.

The reference values come from the guide's own code, run under Node by
tests/helpers/factors/seg_harness.cjs (regenerate with
tests/helpers/factors/regenerate_reference.py). Every term is compared for
White (the position) and Black (the colour-flipped position).
"""

from __future__ import annotations

import gzip
import json
from functools import cache
from pathlib import Path
from typing import Any

import chess
import pytest

from scan64.explanations.factors import GUIDE_COMMIT, TERM_NAMES
from scan64.explanations.factors.board import positions_from_board
from scan64.explanations.factors.terms import TERMS

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "factors"


@cache
def _reference() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        gzip.decompress((FIXTURES / "seg_reference.json.gz").read_bytes())
    )
    return data


def _ids() -> list[str]:
    return [r["id"] for r in _reference()["results"]]


def test_reference_covers_the_position_set() -> None:
    positions = json.loads((FIXTURES / "positions.json").read_text())["positions"]
    assert [p["id"] for p in positions] == _ids()
    assert len(positions) >= 200
    assert _reference()["guide_commit"] == GUIDE_COMMIT


def test_every_guide_term_is_ported_with_its_name_and_group() -> None:
    reference = [(t["name"], t["group"]) for t in _reference()["terms"]]
    assert [(name, group) for name, group, _ in TERMS] == reference
    assert len(TERM_NAMES) == 125
    groups = {group for _, group in reference}
    assert groups == {
        "",
        "Attack",
        "Helpers",
        "Imbalance",
        "King",
        "Material",
        "Mobility",
        "Passed pawns",
        "Pawns",
        "Pieces",
        "Space",
        "Threats",
        "Winnable",
    }


def test_every_term_is_exercised_by_the_position_set() -> None:
    nonzero = {
        name
        for r in _reference()["results"]
        for side in ("white", "black")
        for name, value in r[side].items()
        if value
    }
    assert nonzero == set(TERM_NAMES)


@pytest.mark.parametrize("position_id", _ids())
def test_terms_match_the_guide(position_id: str) -> None:
    result = next(r for r in _reference()["results"] if r["id"] == position_id)
    white, black = positions_from_board(chess.Board(result["fen"]))
    mismatches = []
    for name, _, fn in TERMS:
        for side, pos in (("white", white), ("black", black)):
            expected = result[side][name]
            actual = fn(pos)
            if actual != pytest.approx(expected, abs=1e-9):
                mismatches.append(f"{name} ({side}): {actual} != {expected}")
    assert not mismatches, "\n".join(mismatches)

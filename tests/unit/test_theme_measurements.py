"""The turning point and variant threshold figures recompute from raw records (#513, #506)."""

from __future__ import annotations

import json

from scan64.explanations.themes import _VARIANT_THRESHOLDS
from tests.helpers.themes import (
    record_turning_points,
    record_variant_games,
    summarize_turning_points,
    summarize_variant_games,
)
from tests.helpers.themes.provenance import file_sha256


def test_the_turning_point_table_recomputes_without_an_engine() -> None:
    recorded = json.loads(record_turning_points.OUTPUT.read_text(encoding="utf-8"))
    provenance = recorded["provenance"]
    assert provenance["uncommitted"] == []
    assert provenance["games_sha256"] == {
        name: file_sha256(path) for name, path in record_turning_points.GAMES.items()
    }
    summary = summarize_turning_points.summarize()
    rule = summarize_turning_points.RULE
    found = {
        name: (row["same_as_deep"]["fast pass"], row["same_as_deep"][rule])
        + (row["sides_with_a_point"], row["searches_per_lost_side"])
        for name, row in summary.items()
        if name in ("slow", "selection", "unselected")
    }
    assert found == {
        "slow": (11, 15, 15, 6.4),
        "selection": (60, 70, 72, 1.9),
        "unselected": (114, 135, 141, 1.9),
    }


def test_the_checked_variant_thresholds_are_the_shipped_ones() -> None:
    recorded = json.loads(record_variant_games.OUTPUT.read_text(encoding="utf-8"))
    assert recorded["provenance"]["uncommitted"] == []
    assert all(len(g) == record_variant_games.GAMES for g in recorded["games"].values())
    assert summarize_variant_games.STATED == _VARIANT_THRESHOLDS

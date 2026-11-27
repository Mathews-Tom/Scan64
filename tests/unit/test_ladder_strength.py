"""The ladder-strength harness: config validation, scoring and the Elo estimate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.engines.ladder_strength import (
    elo_from,
    game_seed,
    ladder_score,
    load_config,
    summarise,
)

STRENGTH_TOML = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "engines" / "ladder_strength.toml"
)


def test_committed_config_loads_and_names_both_profiles() -> None:
    config = load_config(STRENGTH_TOML)
    assert config.ladder_profile.is_file() and config.reference_profile.is_file()
    assert len(config.anchors) == 8 and config.openings


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("anchors = [", "anchors = [1, "), "8 integers"),
        (('"e2e4 e7e5 g1f3 b8c6 f1b5 a7a6"', '"e2e4 e2e4"'), "illegal"),
        (("rounds = 2", "rounds = 0"), "run.rounds"),
    ],
)
def test_invalid_config_fails_fast(tmp_path: Path, change: tuple[str, str], message: str) -> None:
    path = tmp_path / "strength.toml"
    path.write_text(STRENGTH_TOML.read_text(encoding="utf-8").replace(*change, 1), "utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(path)


def test_score_is_from_the_ladder_side_and_skips_ply_limit() -> None:
    assert ladder_score({"result": "1-0", "ladder_color": "white"}) == 1.0
    assert ladder_score({"result": "1-0", "ladder_color": "black"}) == 0.0
    assert ladder_score({"result": "1/2-1/2", "ladder_color": "black"}) == 0.5
    assert ladder_score({"result": "*", "ladder_color": "white"}) is None
    assert elo_from(0.5, 1600) == 1600
    assert round(elo_from(0.75, 1600)) == 1791


def test_seed_is_stable_across_runs() -> None:
    assert game_seed((1, 0, "white", 0)) == game_seed((1, 0, "white", 0))
    assert game_seed((1, 0, "white", 0)) != game_seed((1, 0, "black", 0))


def test_summary_reports_estimate_and_unmeasurable_levels(tmp_path: Path) -> None:
    def game(level: int, result: str, color: str) -> dict[str, object]:
        return {
            "level": level,
            "sent_elo": 1000 + level,
            "anchor": 1600,
            "result": result,
            "ladder_color": color,
        }

    results = tmp_path / "games.jsonl"
    records = [
        game(3, "1-0", "white"),
        game(3, "1/2-1/2", "black"),
        game(3, "1-0", "black"),
        game(3, "*", "white"),
        game(4, "0-1", "white"),
    ]
    results.write_text("".join(json.dumps(record) + "\n" for record in records), "utf-8")
    text = summarise(results, load_config(STRENGTH_TOML), {"Reference": "test"})
    assert "| 3 | 1003 | 1600 | 4 | 1-1-1 | 1 | 0.500 | 1600 (" in text
    assert "| 4 | 1004 | 1600 | 1 | 0-0-1 | 0 | 0.000 | below the measurable range" in text

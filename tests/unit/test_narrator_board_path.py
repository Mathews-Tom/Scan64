"""The Analysis Board path benchmark of #462 rebuilds its blind set from the stored records."""

from __future__ import annotations

import json
from pathlib import Path

import chess
import pytest

from benchmarks.narrator import board_path

RESULTS = Path(__file__).parents[2] / "benchmarks/narrator/results/board-path"


def _stored() -> dict[str, object]:
    return json.loads((RESULTS / "lines.json").read_text(encoding="utf-8"))


def test_every_position_is_legal() -> None:
    for fen in board_path.POSITIONS.values():
        assert chess.Board(fen).is_valid()


def test_the_stored_blind_set_rebuilds_from_the_stored_records() -> None:
    rows = board_path.read_jsonl(RESULTS / "run-gemma4-e4b-q4ks.jsonl")
    items, key = board_path.blind(rows, _stored(), seed=462, prefix="b")
    assert items == json.loads((RESULTS / "judge/board-items.json").read_text(encoding="utf-8"))
    assert key == json.loads((RESULTS / "board-key.json").read_text(encoding="utf-8"))


def test_the_judge_folder_holds_only_the_items_and_judgements() -> None:
    names = {path.name for path in (RESULTS / "judge").iterdir()}
    assert names <= {"board-items.json", "board-judgements.json"}
    for item in json.loads((RESULTS / "judge/board-items.json").read_text(encoding="utf-8")):
        assert set(item) == {"id", "position", "facts", "comment"}


def test_the_judge_sees_the_facts_the_model_words() -> None:
    facts = board_path.judge_facts(board_path.board_facts(_stored(), "H3-stalemate"))
    assert facts[0] == "Opening: White to move. The engine's first choice is 1.Qf8#."
    assert "Line 1, The move: 1.Qf8#: the queen on f1 goes forward from f1 to f8." in facts


def test_summarise_rejects_judgements_that_do_not_match_the_key() -> None:
    key = {"b001": {"test": "H1-missed-mate", "n": 0, "model": "m"}}
    with pytest.raises(SystemExit):
        board_path.summarise(key, [], [])

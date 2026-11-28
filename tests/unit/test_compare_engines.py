"""The engine comparison summary uses all of an engine's records, in any order."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from benchmarks.engines.compare_engines import EngineEntry, RunConfig, summarise

POSITIONS = (
    ("start", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
    ("king_pawn", "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"),
)
CAPABILITIES = {
    "id_name": "Second 1.0",
    "multipv_option": True,
    "wdl_option": False,
    "strength_options": ["UCI_Elo"],
}


def _config(tmp_path: Path) -> RunConfig:
    return RunConfig(
        nodes=1000,
        multipv=2,
        threads=1,
        hash_mb=16,
        results_dir=tmp_path,
        positions=POSITIONS,
        engines=(
            EngineEntry("second", tmp_path / "second", "GPL-3.0", "unit"),
            EngineEntry("absent", tmp_path / "absent", "MIT", "unit"),
        ),
    )


def _searched(position: str, pv_length: int, **capabilities: Any) -> dict[str, Any]:
    return {
        "engine": "second",
        "position": position,
        "status": "ok",
        "capabilities": CAPABILITIES | capabilities,
        "lines": [{"pv": ["e2e4"] * pv_length, "depth": 10, "seldepth": 14}] * 2,
    }


def _write(path: Path, records: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def _row(summary: str, cell: str) -> str:
    return next(line for line in summary.splitlines() if f"| {cell} |" in line)


def test_a_resumed_engine_is_summarised_after_its_not_installed_record(tmp_path: Path) -> None:
    # The first run found no binary; the resumed run searched both positions.
    records = [
        {"engine": "second", "position": "*", "status": "not installed"},
        _searched("start", 3),
        _searched("king_pawn", 5),
        {"engine": "absent", "position": "*", "status": "not installed"},
    ]
    summary = summarise(_write(tmp_path / "results.jsonl", records), _config(tmp_path))

    row = _row(summary, "Second 1.0")
    assert row.startswith("| Second 1.0 | GPL-3.0 | yes | no | UCI_Elo | 3/3 | 5/5 | 4.0 |")
    assert "not installed" not in row
    assert "| absent | MIT | not installed |" in summary


def test_the_summary_does_not_depend_on_record_order(tmp_path: Path) -> None:
    records = [
        {"engine": "second", "position": "*", "status": "not installed"},
        _searched("start", 3),
        _searched("king_pawn", 5),
    ]
    expected = summarise(_write(tmp_path / "ordered.jsonl", records), _config(tmp_path))
    for seed in range(5):
        shuffled = records[:]
        random.Random(seed).shuffle(shuffled)
        path = _write(tmp_path / f"shuffled_{seed}.jsonl", shuffled)
        assert summarise(path, _config(tmp_path)) == expected


def test_an_engine_with_no_records_is_not_run(tmp_path: Path) -> None:
    summary = summarise(_write(tmp_path / "results.jsonl", []), _config(tmp_path))
    assert "| second | GPL-3.0 | not run |" in summary


def test_records_from_engines_with_different_capabilities_are_refused(tmp_path: Path) -> None:
    records = [_searched("start", 3), _searched("king_pawn", 5, id_name="Second 2.0")]
    with pytest.raises(ValueError, match="2 different engine capabilities"):
        summarise(_write(tmp_path / "results.jsonl", records), _config(tmp_path))


def test_a_position_recorded_twice_is_refused(tmp_path: Path) -> None:
    records = [_searched("start", 3), _searched("start", 4)]
    with pytest.raises(ValueError, match="position start is recorded twice"):
        summarise(_write(tmp_path / "results.jsonl", records), _config(tmp_path))

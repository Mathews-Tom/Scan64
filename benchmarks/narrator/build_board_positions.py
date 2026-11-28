"""Build the fixed Analysis Board positions of the narrator benchmark (#449).

Usage:
    uv run python -m benchmarks.narrator.build_board_positions benchmarks/narrator/narrator.toml

The Analysis Board narrator (``POST /v1/analysis/position/explanation``) words
the engine lines of a position. Its benchmark positions are the recorded
positions of the Analysis Board tests: the Stockfish 18 MultiPV searches of
``tests/fixtures/analysis_board_lines.json`` and the per-move searches along
their lines in ``tests/fixtures/analysis_board_line_evaluations.json``. For each
position the file keeps the MultiPV search and the per-move searches that the
endpoint makes for ``multipv`` explained lines (``board_line_positions``), so
the runner rebuilds the facts as the endpoint does, with no engine:
``scored_legal_lines`` -> first ``multipv`` lines -> ``line_scores`` ->
``build_board_lines_facts``.

No engine runs and no random value is written, so the output is byte-for-byte
reproducible, and ``positions_sha256`` in a runs header is the same after every
rebuild. Positions are in the recorded order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

import chess

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.board_lines import board_line_positions, scored_legal_lines

REPO_ROOT = Path(__file__).resolve().parents[2]
BOARD_KIND = "board-lines"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_board_positions(lines_file: Path, searches_file: Path, multipv: int) -> dict[str, Any]:
    """The board positions document: per position, the searches the endpoint uses."""
    recorded = json.loads(lines_file.read_text(encoding="utf-8"))
    searches = json.loads(searches_file.read_text(encoding="utf-8"))
    positions = []
    for name, entry in recorded["positions"].items():
        board = chess.Board(entry["fen"])
        analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
        lines = scored_legal_lines(board, analysis)[:multipv]
        if len(lines) < multipv:
            raise ValueError(f"{name}: {len(lines)} usable lines, fewer than MultiPV {multipv}")
        by_moves = {
            tuple(item["moves"]): item["raw_result"] for item in searches["positions"][name]
        }
        line_searches = []
        for position in board_line_positions(board, lines):
            moves = tuple(move.uci() for move in position.move_stack)
            if moves not in by_moves:
                raise ValueError(f"{name}: no recorded search after {' '.join(moves)}")
            line_searches.append({"moves": list(moves), "raw_result": by_moves[moves]})
        positions.append(
            {
                "position_id": name,
                "fen": entry["fen"],
                "config": entry["config"],
                "raw_result": entry["raw_result"],
                "line_searches": line_searches,
            }
        )
    return {
        "kind": BOARD_KIND,
        "multipv": multipv,
        "sources": {
            "lines": {
                "file": lines_file.relative_to(REPO_ROOT).as_posix(),
                "sha256": _digest(lines_file),
            },
            "line_evaluations": {
                "file": searches_file.relative_to(REPO_ROOT).as_posix(),
                "sha256": _digest(searches_file),
            },
        },
        "line_search_config": searches["config"],
        "positions": positions,
    }


def build(config_path: Path) -> Path:
    with config_path.open("rb") as stream:
        board = tomllib.load(stream)["positions"]["board"]
    document = build_board_positions(
        REPO_ROOT / board["lines"], REPO_ROOT / board["line_evaluations"], int(board["multipv"])
    )
    output = REPO_ROOT / str(board["file"])
    output.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    print(f"wrote {build(args.config)}")


if __name__ == "__main__":
    main()

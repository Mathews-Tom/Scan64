"""Write the Playwright fixture of an Analysis Board explanation response (#368).

Run from the repository root with:
    uv run python tests/fixtures/write_analysis_explanation_e2e.py

Builds the template response of ``POST /v1/analysis/position/explanation`` for
the recorded Stockfish 18 lines of the starting position
(``tests/fixtures/analysis_board_lines.json``) with the endpoint's own code.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import chess

from scan64.api.analysis_explanation import explanation_read
from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.board_lines import (
    build_board_lines_facts,
    resolve_board_lines_explanation,
    scored_legal_lines,
)

ROOT = Path(__file__).resolve().parents[2]
RECORDING = ROOT / "tests/fixtures/analysis_board_lines.json"
OUTPUT = ROOT / "apps/scan64-web/tests/e2e/fixtures/analysis-explanation-start.json"


async def main() -> None:
    entry = json.loads(RECORDING.read_text(encoding="utf-8"))["positions"]["start"]
    board = chess.Board(entry["fen"])
    analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
    lines = scored_legal_lines(board, analysis)[:3]
    facts = build_board_lines_facts(board, lines)
    resolved = await resolve_board_lines_explanation(facts, environment={})
    read = explanation_read(board, lines, facts, resolved)
    OUTPUT.write_text(read.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())

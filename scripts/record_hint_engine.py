"""Explicitly regenerate M55's version-sensitive reference engine fixture.

Run from the repository root with:
    uv run python scripts/record_hint_engine.py --engine stockfish

This replaces recorded engine output; unit tests never invoke the engine.
The original recording used only a node limit, not the live endpoint's time cap.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import chess
import chess.engine

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/hint_engine_recording.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default="stockfish", help="Stockfish UCI executable")
    args = parser.parse_args()
    previous = json.loads(FIXTURE.read_text())
    with chess.engine.SimpleEngine.popen_uci(args.engine) as engine:
        engine.configure({"Threads": previous["threads"], "Hash": previous["hash_mb"]})
        record: dict[str, Any] = {
            "engine": engine.id,
            "nodes": previous["nodes"],
            "multipv": previous["multipv"],
            "threads": previous["threads"],
            "hash_mb": previous["hash_mb"],
            "time_ms": previous["time_ms"],
            "recorded_on": datetime.now(UTC).date().isoformat(),
            "regenerate_command": previous["regenerate_command"],
            "positions": {},
        }
        for name, entry in previous["positions"].items():
            board = chess.Board(entry["fen"])
            time_ms = record["time_ms"]
            infos = engine.analyse(
                board,
                chess.engine.Limit(nodes=record["nodes"], time=time_ms / 1000 if time_ms else None),
                multipv=record["multipv"],
                game=object(),
            )
            lines = []
            for info in infos:
                score = info["score"].pov(board.turn)
                lines.append(
                    {
                        "pv": [move.uci() for move in info["pv"]],
                        "score_mate" if score.is_mate() else "score_cp": (
                            score.mate() if score.is_mate() else score.score()
                        ),
                    }
                )
            record["positions"][name] = {"fen": entry["fen"], "raw_result": lines}
    FIXTURE.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()

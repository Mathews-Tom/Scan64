"""Measure narrator prompt sizes on the benchmark positions and the Analysis Board fixture.

Usage: ``uv run python tests/helpers/narrator/prompt_tokens.py [--ollama URL MODEL]``.
Prints, per prompt set, the largest request in characters and in the offline
token estimate (``assembly.estimated_prompt_tokens``); with ``--ollama``,
also the ``prompt_eval_count`` the model reports (think=false, one output token).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import chess
import httpx
from chess_lesson_spec import Diagnosis

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.assembly import _explanation_request, estimated_prompt_tokens
from scan64.explanations.board_lines import (
    board_lines_request,
    build_board_lines_facts,
    scored_legal_lines,
)
from scan64.explanations.facts import build_narrator_facts
from scan64.explanations.voice import NarratorVoice
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import ExplanationRequest

ROOT = Path(__file__).resolve().parents[3]


def lesson_requests(voice: NarratorVoice = "coach") -> dict[str, ExplanationRequest]:
    raw = json.loads((ROOT / "benchmarks/narrator/positions.json").read_text(encoding="utf-8"))
    out = {}
    for item in raw["positions"]:
        facts = build_narrator_facts(
            Diagnosis(**item["diagnosis"]),
            [Evidence(**e) for e in item["evidence"]],
            item["fen"],
        )
        out[item["position_id"]] = _explanation_request(facts, voice)
    return out


def board_requests(count: int = 3, voice: NarratorVoice = "coach") -> dict[str, ExplanationRequest]:
    raw = json.loads(
        (ROOT / "tests/fixtures/analysis_board_lines.json").read_text(encoding="utf-8")
    )
    out = {}
    for name, entry in raw["positions"].items():
        board = chess.Board(entry["fen"])
        analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
        lines = scored_legal_lines(board, analysis)[:count]
        out[name] = board_lines_request(build_board_lines_facts(board, lines), voice)
    return out


def characters(request: ExplanationRequest) -> int:
    return sum(len(message.content) for message in request.messages)


def ollama_tokens(url: str, model: str, request: ExplanationRequest) -> int:
    body = {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        "stream": False,
        "think": False,
        "options": {"num_predict": 1, "num_ctx": 16384},
    }
    response = httpx.post(f"{url}/api/chat", json=body, timeout=300)
    response.raise_for_status()
    return int(response.json()["prompt_eval_count"])


def main() -> None:
    ollama = sys.argv[2:4] if len(sys.argv) >= 4 and sys.argv[1] == "--ollama" else None
    sets = [("lessons", lesson_requests())]
    sets += [(f"board MultiPV {count}", board_requests(count)) for count in (3, 4)]
    for label, requests in sets:
        rows = []
        for name, request in requests.items():
            real = ollama_tokens(*ollama, request) if ollama else None
            rows.append((characters(request), estimated_prompt_tokens(request), real, name))
        rows.sort()
        chars, est, real, name = rows[-1]
        print(f"{label}: {len(rows)} prompts; largest {name}: {chars} chars, estimate {est}")
        if ollama:
            reals = [r[2] for r in rows]
            ratio = min(r[0] / r[2] for r in rows)
            print(f"  ollama prompt_eval_count max {max(reals)}, min chars/token {ratio:.2f}")
            print("  " + json.dumps({r[3]: [r[0], r[1], r[2]] for r in rows}))


if __name__ == "__main__":
    main()

"""The default Ollama context fits the longest narrator prompt plus an answer (#388, #429).

Ollama silently drops the start of a prompt that fills ``num_ctx``, so the
default window must hold the largest prompt of either kind plus the output
budget: the lesson prompt for every fixture diagnosis, and the Analysis Board
prompt for every recorded position at the largest MultiPV the endpoint allows
(the hint MultiPV). The test needs no tokenizer: it bounds tokens from
characters with ``assembly.estimated_prompt_tokens`` (2.4 characters per
token). Since #408 the prompt is the system prompt and slot list, not the facts
JSON; the lowest ratio the #428 benchmark measured is 2.63 characters per token
(gemma4-12b; 2.64 for the earlier default model gemma4-e4b-q3km, 534 calls each), so
the estimate over-counts tokens. The 1.75 used before #429 was set for the
facts JSON prompt.
"""

from __future__ import annotations

import json
from pathlib import Path

import chess
import pytest

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations import assembly
from scan64.explanations.board_lines import (
    board_lines_request,
    build_board_lines_facts,
    scored_legal_lines,
)
from scan64.explanations.facts import build_narrator_facts
from scan64.providers.llm import local_ollama
from scan64.providers.llm.adapters import DEFAULT_OLLAMA_CONTEXT_TOKENS
from scan64.providers.llm.config import LLMProviderConfig
from scan64.providers.llm.contracts import ExplanationRequest, LLMMessage
from scan64.providers.stockfish.pool import HINT_MULTIPV
from tests.unit.test_narrator_facts import _CASES, _diagnoses

OUTPUT_TOKEN_BUDGET = 2048
BOARD_RECORDING = json.loads(
    (Path(__file__).parents[1] / "fixtures/analysis_board_lines.json").read_text(encoding="utf-8")
)


def _characters(request: ExplanationRequest) -> int:
    return sum(len(message.content) for message in request.messages)


def _estimated_tokens(characters: int) -> int:
    message = LLMMessage(role="user", content="x" * characters)
    return assembly.estimated_prompt_tokens(ExplanationRequest(messages=(message,)))


async def _longest_lesson_prompt() -> tuple[int, str]:
    longest = (0, "")
    for case_id, board, evidence, played, swing in _CASES:
        for diagnosis in await _diagnoses(evidence, played, swing):
            facts = build_narrator_facts(diagnosis, evidence, board.fen())
            request = assembly._explanation_request(facts)
            longest = max(longest, (_characters(request), case_id))
    return longest


def _longest_board_prompt() -> tuple[int, str]:
    # The endpoint explains at most the hint MultiPV lines; the recording holds that many.
    assert BOARD_RECORDING["multipv"] >= HINT_MULTIPV
    longest = (0, "")
    for name, entry in BOARD_RECORDING["positions"].items():
        board = chess.Board(entry["fen"])
        analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
        lines = scored_legal_lines(board, analysis)[:HINT_MULTIPV]
        request = board_lines_request(build_board_lines_facts(board, lines))
        longest = max(longest, (_characters(request), f"board {name} MultiPV {len(lines)}"))
    return longest


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["lesson", "board"])
async def test_default_context_fits_the_longest_prompt_and_the_output_budget(kind: str) -> None:
    if kind == "lesson":
        characters, case_id = await _longest_lesson_prompt()
    else:
        characters, case_id = _longest_board_prompt()
    assert characters > len(assembly.NARRATOR_SYSTEM_PROMPT), "no fixture prompt was built"
    needed = _estimated_tokens(characters) + OUTPUT_TOKEN_BUDGET
    for window in (
        DEFAULT_OLLAMA_CONTEXT_TOKENS,
        local_ollama.LocalOllamaLimits.from_environment({}).context_tokens,
        LLMProviderConfig(provider="ollama").context_tokens,
    ):
        assert needed <= window, (
            f"{case_id}: {characters} chars is about {_estimated_tokens(characters)} tokens; "
            f"plus {OUTPUT_TOKEN_BUDGET} output tokens exceeds num_ctx {window}"
        )


def test_the_prompt_size_target_plus_the_answer_fits_the_default_context() -> None:
    """test_narrator_prompt_size keeps every prompt, in every voice, under the target."""
    assert assembly.PROMPT_TOKEN_TARGET + OUTPUT_TOKEN_BUDGET <= DEFAULT_OLLAMA_CONTEXT_TOKENS

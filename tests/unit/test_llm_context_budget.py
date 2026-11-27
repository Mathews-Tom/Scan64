"""The default Ollama context fits the longest narrator prompt plus an answer (#388).

Ollama silently drops the start of a prompt that fills ``num_ctx``, so the
default window must hold the largest NarratorFacts prompt in the test corpus
plus the output budget. The test needs no tokenizer: it bounds tokens from
characters. Narrator prompts are JSON with many short keys, SAN and UCI
tokens; the measured runs in docs/narrator-pipeline.md and the #367
benchmark give about 1.8-2.1 characters per token. 1.75 characters per token
over-counts tokens, so the estimate is conservative.
"""

from __future__ import annotations

import math

import pytest

from scan64.explanations import assembly
from scan64.explanations.facts import build_narrator_facts
from scan64.providers.llm import local_ollama
from scan64.providers.llm.adapters import DEFAULT_OLLAMA_CONTEXT_TOKENS
from scan64.providers.llm.config import LLMProviderConfig
from tests.unit.test_narrator_facts import _CASES, _diagnoses

CHARS_PER_TOKEN = 1.75
OUTPUT_TOKEN_BUDGET = 2048


def _estimated_tokens(characters: int) -> int:
    return math.ceil(characters / CHARS_PER_TOKEN)


async def _longest_prompt_characters() -> tuple[int, str]:
    longest = (0, "")
    for case_id, board, evidence, played, swing in _CASES:
        for diagnosis in await _diagnoses(evidence, played, swing):
            facts = build_narrator_facts(diagnosis, evidence, board.fen())
            request = assembly._explanation_request(facts)
            characters = sum(len(message.content) for message in request.messages)
            longest = max(longest, (characters, case_id))
    return longest


@pytest.mark.asyncio
async def test_default_context_fits_the_longest_prompt_and_the_output_budget() -> None:
    characters, case_id = await _longest_prompt_characters()
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

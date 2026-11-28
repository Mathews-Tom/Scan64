"""Narrator prompt size (#408): every benchmark and Analysis Board prompt under the target.

The model gets the system prompt (rules, claims format, voice) and the slot
list only; the facts JSON stays in the pipeline for validation and storage.
Measured with tests/helpers/narrator/prompt_tokens.py --ollama (docs section 13).
"""

from __future__ import annotations

import pytest

from scan64.explanations.assembly import PROMPT_TOKEN_TARGET, estimated_prompt_tokens
from scan64.explanations.voice import NARRATOR_VOICES, NarratorVoice
from scan64.providers.llm import ExplanationRequest, LLMMessage
from tests.helpers.narrator.prompt_tokens import board_requests, lesson_requests


def test_the_estimate_rounds_up() -> None:
    request = ExplanationRequest(messages=(LLMMessage(role="user", content="x" * 25),))
    assert estimated_prompt_tokens(request) == 11


@pytest.mark.parametrize("voice", NARRATOR_VOICES)
def test_every_lesson_prompt_is_under_the_target(voice: NarratorVoice) -> None:
    for position_id, request in lesson_requests(voice).items():
        assert [m.role for m in request.messages] == ["system", "user"], position_id
        assert estimated_prompt_tokens(request) < PROMPT_TOKEN_TARGET, position_id


@pytest.mark.parametrize("voice", NARRATOR_VOICES)
@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_every_board_prompt_is_under_the_target(count: int, voice: NarratorVoice) -> None:
    for name, request in board_requests(count, voice).items():
        assert [m.role for m in request.messages] == ["system", "user"], name
        assert estimated_prompt_tokens(request) < PROMPT_TOKEN_TARGET, name


def test_no_facts_json_in_the_prompt() -> None:
    for request in (*lesson_requests().values(), *board_requests().values()):
        assert "schema_version" not in "".join(m.content for m in request.messages)

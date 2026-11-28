"""Narrator voices change only the prose style; every slot check applies to each voice (#387)."""

from __future__ import annotations

from pathlib import Path

import pytest
from chess_lesson_spec import Diagnosis, ExplanationClaim

from scan64.explanations import assembly
from scan64.explanations.assembly import (
    NARRATOR_SYSTEM_PROMPT,
    narrator_system_prompt,
    resolve_explanation,
    template_fallback_counts,
)
from scan64.explanations.board_lines import (
    BOARD_NARRATOR_SYSTEM_PROMPT,
    BoardLinesFacts,
    board_lines_request,
    board_narrator_system_prompt,
    board_slot_catalogue,
    resolve_board_lines_explanation,
    template_board_explanation,
)
from scan64.explanations.facts import NarratorFacts, build_narrator_facts
from scan64.explanations.settings import OPERATOR_LLM_CONFIG, LocalNarratorProvider
from scan64.explanations.slots import CHESS_PROSE, lesson_slot_catalogue
from scan64.explanations.templates.provider import TemplateExplanationProvider
from scan64.explanations.voice import (
    DEFAULT_NARRATOR_VOICE,
    NARRATOR_VOICES,
    VOICE_FRAGMENTS,
    NarratorVoice,
    NarratorVoiceError,
    parse_narrator_voice,
    voice_rules,
)
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import ExplanationRequest, GeneratedExplanation
from scan64.providers.llm.config import LLMConfigurationError, LLMProviderConfig
from tests.unit.test_board_lines import _facts as _board_facts
from tests.unit.test_narrator_facts import _FEN, _missed_capture

# A raw chess claim written as prose, not through a slot.
_RAW_LESSON_CLAIM = "You played Nf3 and lost the queen on g5."
_SLOT_LESSON_CLAIM = "You played {played}, but {capture:best}."
_RAW_BOARD_CLAIM = "Line 1 wins a queen."


def _claim(text: str, evidence_ref: str, line: tuple[str, ...] = ()) -> ExplanationClaim:
    return ExplanationClaim(text=text, evidence_ref=evidence_ref, line=line, disclosure_level=1)


class _Provider:
    def __init__(self, *claims: ExplanationClaim) -> None:
        self.claims = claims
        self.requests: list[ExplanationRequest] = []

    async def generate(self, request: ExplanationRequest) -> GeneratedExplanation:
        self.requests.append(request)
        return GeneratedExplanation(claims=self.claims)


def _lesson(text: str) -> tuple[Diagnosis, list[Evidence], _Provider]:
    """A missed-capture lesson and a provider that answers ``text`` for its evidence."""
    diagnosis, evidence = _missed_capture()
    facts = build_narrator_facts(diagnosis, evidence, _FEN)
    return diagnosis, evidence, _Provider(_claim(text, facts.evidence[0].evidence_id))


def test_there_are_three_voices_and_coach_is_the_default() -> None:
    assert NARRATOR_VOICES == ("coach", "terse", "playful")
    assert DEFAULT_NARRATOR_VOICE == "coach"
    assert parse_narrator_voice(None) == "coach"
    with pytest.raises(NarratorVoiceError):
        parse_narrator_voice("pirate")
    with pytest.raises(NarratorVoiceError):
        parse_narrator_voice(1)


@pytest.mark.parametrize("voice", NARRATOR_VOICES)
def test_a_voice_fragment_adds_no_chess_fact_and_asks_for_no_reasoning(
    voice: NarratorVoice,
) -> None:
    fragment = VOICE_FRAGMENTS[voice]
    assert CHESS_PROSE.search(fragment) is None
    for word in ("think", "reason", "step by step", "analy", "calculat", "explain why"):
        assert word not in fragment.lower()
    assert len(voice_rules(voice)) < 400


@pytest.mark.parametrize("voice", NARRATOR_VOICES)
def test_each_voice_builds_a_valid_lesson_and_board_prompt(voice: NarratorVoice) -> None:
    diagnosis, evidence = _missed_capture()
    facts = build_narrator_facts(diagnosis, evidence, _FEN)
    system, user, slots = assembly._explanation_request(facts, voice).messages
    assert system.role == "system"
    assert system.content == f"{NARRATOR_SYSTEM_PROMPT}\n{voice_rules(voice)}"
    assert system.content == narrator_system_prompt(voice)
    # The facts and the slot list do not depend on the voice.
    assert NarratorFacts.model_validate_json(user.content, strict=True) == facts
    assert slots.content == lesson_slot_catalogue(facts).prompt()

    board = _board_facts("start", 3)
    system, user, slots = board_lines_request(board, voice).messages
    assert system.content == f"{BOARD_NARRATOR_SYSTEM_PROMPT}\n{voice_rules(voice)}"
    assert system.content == board_narrator_system_prompt(voice)
    assert BoardLinesFacts.model_validate_json(user.content) == board.for_prompt()
    assert slots.content == board_slot_catalogue(board).prompt()


def test_voices_differ_only_in_the_last_line() -> None:
    prompts = {narrator_system_prompt(voice) for voice in NARRATOR_VOICES}
    assert len(prompts) == len(NARRATOR_VOICES)
    assert {prompt.rsplit("\n", 1)[0] for prompt in prompts} == {NARRATOR_SYSTEM_PROMPT}


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", NARRATOR_VOICES)
async def test_each_voice_rejects_a_raw_chess_claim_in_a_lesson(voice: NarratorVoice) -> None:
    diagnosis, evidence, provider = _lesson(_RAW_LESSON_CLAIM)
    template = await TemplateExplanationProvider().explain(diagnosis, evidence)
    before = template_fallback_counts().get("chess_token_outside_slot", 0)
    explanation = await resolve_explanation(
        diagnosis, evidence, _FEN, local_provider=provider, environment={}, voice=voice
    )
    assert explanation == template
    assert template_fallback_counts()["chess_token_outside_slot"] == before + 1
    [request] = provider.requests
    assert request.messages[0].content == narrator_system_prompt(voice)


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", NARRATOR_VOICES)
async def test_each_voice_accepts_the_same_slot_claim_in_a_lesson(voice: NarratorVoice) -> None:
    diagnosis, evidence, provider = _lesson(_SLOT_LESSON_CLAIM)
    explanation = await resolve_explanation(
        diagnosis,
        evidence,
        _FEN,
        local_provider=provider,
        environment={},
        voice=voice,
    )
    assert explanation.text == "You played Nf3, but Bxg5 captures the black queen on g5."


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", NARRATOR_VOICES)
async def test_each_voice_rejects_a_raw_chess_claim_on_the_board(voice: NarratorVoice) -> None:
    facts = _board_facts("start", 3)
    provider = _Provider(_claim(_RAW_BOARD_CLAIM, "line-1", ("e2e4",)))
    resolved = await resolve_board_lines_explanation(
        facts, local_provider=provider, environment={}, voice=voice
    )
    assert resolved.source == "template"
    assert resolved.fallback_reason == "chess_token_outside_slot"
    assert resolved.explanation == template_board_explanation(facts)
    [request] = provider.requests
    assert request.messages[0].content == board_narrator_system_prompt(voice)


@pytest.mark.asyncio
async def test_without_a_voice_setting_the_default_voice_is_used() -> None:
    diagnosis, evidence, provider = _lesson(_SLOT_LESSON_CLAIM)
    await resolve_explanation(diagnosis, evidence, _FEN, local_provider=provider, environment={})
    board = _Provider(_claim(_RAW_BOARD_CLAIM, "line-1", ("e2e4",)))
    await resolve_board_lines_explanation(
        _board_facts("start", 3), local_provider=board, environment={}
    )
    assert provider.requests[0].messages[0].content == narrator_system_prompt("coach")
    assert board.requests[0].messages[0].content == board_narrator_system_prompt("coach")
    assert narrator_system_prompt() == narrator_system_prompt("coach")


@pytest.mark.asyncio
async def test_the_local_grant_carries_its_voice_and_an_explicit_voice_wins() -> None:
    diagnosis, evidence, inner = _lesson(_SLOT_LESSON_CLAIM)
    local = LocalNarratorProvider(inner, "terse")
    await resolve_explanation(diagnosis, evidence, _FEN, local_provider=local, environment={})
    await resolve_explanation(
        diagnosis, evidence, _FEN, local_provider=local, environment={}, voice="playful"
    )
    assert [request.messages[0].content for request in inner.requests] == [
        narrator_system_prompt("terse"),
        narrator_system_prompt("playful"),
    ]


def test_the_operator_config_reads_an_optional_voice() -> None:
    base: dict[object, object] = {
        "provider": "ollama",
        "model": "m",
        "base_url": "http://127.0.0.1:11434",
    }
    assert LLMProviderConfig._from_mapping(base).voice == "coach"
    assert LLMProviderConfig._from_mapping({**base, "voice": "terse"}).voice == "terse"
    assert LLMProviderConfig._from_mapping({"provider": "template"}).voice == "coach"
    with pytest.raises(LLMConfigurationError, match="llm.voice"):
        LLMProviderConfig._from_mapping({**base, "voice": "pirate"})
    with pytest.raises(LLMConfigurationError, match="llm.voice"):
        LLMProviderConfig._from_mapping({**base, "voice": 3})


@pytest.mark.asyncio
@pytest.mark.parametrize(("line", "voice"), [("", "coach"), ('voice = "playful"\n', "playful")])
async def test_the_operator_voice_reaches_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str, voice: NarratorVoice
) -> None:
    config = tmp_path / "llm.toml"
    config.write_text(
        f'[llm]\nprovider = "ollama"\nmodel = "m"\nbase_url = "http://127.0.0.1:11434"\n{line}'
    )
    diagnosis, evidence, provider = _lesson(_SLOT_LESSON_CLAIM)
    monkeypatch.setattr(assembly, "create_llm_provider", lambda *_args, **_kwargs: provider)
    await resolve_explanation(
        diagnosis, evidence, _FEN, environment={OPERATOR_LLM_CONFIG: str(config)}
    )
    assert provider.requests[0].messages[0].content == narrator_system_prompt(voice)

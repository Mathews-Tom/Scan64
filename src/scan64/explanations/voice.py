"""Narrator voices: tone and sentence style for the prose outside the fact slots (#387).

A voice is one short prompt fragment appended to the narrator system prompt. It
changes only how the plain words around the slots read. It adds no chess fact,
asks for no reasoning, and leaves the slot rules and the validator unchanged:
every voice is checked by the same slot path and the same ``CHESS_PROSE`` rules.

Configuration (additive; a missing value means ``DEFAULT_NARRATOR_VOICE``):

- operator: ``voice`` in the ``[llm]`` table of ``SCAN64_LLM_CONFIG``;
- local model: ``voice`` in the owner's local explanation settings.
"""

from __future__ import annotations

from typing import Final, Literal, TypeGuard, get_args

NarratorVoice = Literal["coach", "terse", "playful"]

NARRATOR_VOICES: Final[tuple[NarratorVoice, ...]] = get_args(NarratorVoice)
DEFAULT_NARRATOR_VOICE: Final[NarratorVoice] = "coach"

_SHARED = (
    " The voice changes only the plain words around the slots: every slot rule above "
    "applies unchanged, and the voice never adds a chess fact."
)

VOICE_FRAGMENTS: Final[dict[NarratorVoice, str]] = {
    "coach": (
        "Voice: a calm, friendly coach. Speak to the learner directly, in short, "
        "encouraging sentences."
    ),
    "terse": (
        "Voice: terse. Use as few plain words as possible: short sentences, no "
        "greetings, no praise, no filler."
    ),
    "playful": (
        "Voice: playful and light. Use lively, everyday words and a warm, cheerful "
        "tone; keep each sentence short and clear."
    ),
}


class NarratorVoiceError(ValueError):
    """Raised for a voice name that is not one of ``NARRATOR_VOICES``."""


def is_narrator_voice(value: object) -> TypeGuard[NarratorVoice]:
    return isinstance(value, str) and value in NARRATOR_VOICES


def parse_narrator_voice(value: object) -> NarratorVoice:
    """Return ``value`` as a voice; ``None`` gives the default voice."""
    if value is None:
        return DEFAULT_NARRATOR_VOICE
    if is_narrator_voice(value):
        return value
    raise NarratorVoiceError(f"Narrator voice must be one of {', '.join(NARRATOR_VOICES)}")


def voice_rules(voice: NarratorVoice) -> str:
    """The system prompt fragment for ``voice``."""
    return VOICE_FRAGMENTS[voice] + _SHARED


def with_voice(system_prompt: str, voice: NarratorVoice) -> str:
    """``system_prompt`` with the voice fragment as its last line."""
    return f"{system_prompt}\n{voice_rules(voice)}"


__all__ = [
    "DEFAULT_NARRATOR_VOICE",
    "NARRATOR_VOICES",
    "VOICE_FRAGMENTS",
    "NarratorVoice",
    "NarratorVoiceError",
    "is_narrator_voice",
    "parse_narrator_voice",
    "voice_rules",
    "with_voice",
]

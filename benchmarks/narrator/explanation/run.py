"""Run the production voice pipeline on the fixed positions, one record per explanation (#403).

The pipeline is the product's own ``voice_facts``; only the voice card and the model change.
"""

import asyncio
import dataclasses
import time
import urllib.error
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from unittest import mock

from benchmarks.narrator.explanation.positions import Position, load
from benchmarks.narrator.voice import data, pipeline
from scan64.explanations import voice_pipeline
from scan64.explanations.sentence_check import finish
from scan64.explanations.voice_pipeline import StoryFact, cause_facts, voice_facts

PLAIN = "plain"  # the fact texts, no model: what every sentence falls back to
NO_CAUSE = "no-cause"  # the Tal card without the cause and the turning point (before #401)
VOICES = ("Tal", "Chernev", NO_CAUSE, PLAIN)

# The benchmark's Chernev card (voice runs 6-9) gives no example for these two facts.
CHERNEV_CAUSE = (
    "Notice that from move 12 to move 15 White's king on g1 had little cover: the f-file next to "
    "it was open, with none of White's pawns on it.",
    "The game began to turn against White with 14.Qd2: the position was level before it, and "
    "the engine never again rated it level after it.",
)


@dataclasses.dataclass
class Writer:
    client: pipeline.Client

    async def rewrite(self, system: str, user: str) -> str:
        return self.client.chat(system, user)[0]


def cards(voice: str) -> dict[str, Any]:
    """The card constants of ``voice_pipeline`` that ``voice`` runs with."""
    names = ("VOICE_NAME", "VOICE_TRAITS", "VOICE_EXAMPLES", "CAUSE_EXAMPLES")
    card = {name: getattr(voice_pipeline, name) for name in names}
    if voice == "Chernev":
        sentences = [s for group in data.SENTENCES_RUN6["Chernev"] for s in group]
        card["VOICE_NAME"] = "Chernev"
        card["VOICE_TRAITS"] = data.CARDS_RUN6["Chernev"]
        for name, texts in (("VOICE_EXAMPLES", sentences), ("CAUSE_EXAMPLES", CHERNEV_CAUSE)):
            card[name] = tuple(
                (role, fact, text) for (role, fact, _), text in zip(card[name], texts, strict=True)
            )
    return card


def _patched(voice: str) -> AbstractContextManager[Any]:
    return mock.patch.multiple(voice_pipeline, **cards(voice))


def label(fact: StoryFact, position: Position) -> str:
    if isinstance(fact, voice_pipeline.CauseFact):
        return voice_pipeline.CAUSE_LABELS[fact.role]
    return voice_pipeline.role_label(fact.role, position.mover)


def explain(client: pipeline.Client | None, voice: str, position: Position) -> dict[str, Any]:
    """One explanation. A provider error is a record with no sentences, not a crash."""
    game = None if voice == NO_CAUSE else position.game
    theme = position.game.themes[0] if position.game.themes else None
    turn = voice_pipeline.lesson_turning_point(position.game)
    record: dict[str, Any] = {
        "test": position.id,
        "style": voice,
        "theme": theme.model_dump(mode="json") if theme else None,
        "turn": turn.model_dump(mode="json") if turn else None,
        "key_moves": position.key_moves(),
        "facts": [[label(f, position), f.text] for f in position.story()],
        "fen": position.fen,
        "error": None,
    }
    start = time.monotonic()
    if client is None:
        sentences = [
            {"role": f.role, "text": finish(f.text), "source": "fact", "calls": 0}
            for f in position.story()
        ]
    else:
        try:
            with _patched(voice):
                voiced = asyncio.run(
                    voice_facts(
                        Writer(client),
                        position.line_facts(),
                        mover=position.mover,
                        played=position.played_san,
                        game=game,
                    )
                )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return record | {"error": str(error), "sentences": [], "text": ""}
        sentences = [
            {"role": s.fact.role, "text": s.text, "source": s.source, "calls": s.calls}
            for s in voiced.sentences
        ]
    return record | {
        "sentences": sentences,
        "text": " ".join(s["text"] for s in sentences),
        "seconds": round(time.monotonic() - start, 2),
    }


def prompt_data(voice: str, positions: list[Position]) -> dict[str, Any]:
    facts = {
        p.id: [
            [label(f, p), f.text]
            for f in p.story()
            if voice != NO_CAUSE or f not in cause_facts(p.game, p.mover)
        ]
        for p in positions
    }
    return {"system": voice_pipeline.VOICE_SYSTEM_PROMPT, "card": cards(voice), "facts": facts}


def run(
    voice: str, client: pipeline.Client | None, samples: int, out: Path | None = None
) -> Iterator[dict[str, Any]]:
    """Records of one arm. A dirty tree is refused, so ``commit`` names the code that ran."""
    if voice not in VOICES:
        raise SystemExit(f"there is no voice {voice}; voices are {', '.join(VOICES)}")
    if (client is None) != (voice == PLAIN):
        raise SystemExit("the plain voice runs without a model; every other voice needs one")
    if pipeline.tree_state(out)[0]:
        raise SystemExit("the tree has uncommitted changes: commit them before a measurement")
    positions = load()
    sha256 = pipeline.sha256_json(prompt_data(voice, positions))
    config = {"run": "explanation", "voice": voice, "samples": samples, "own_words": False}
    if client is None:
        meta = {
            "commit": pipeline._git("rev-parse", "HEAD"),
            "dirty": False,
            "model": PLAIN,
            "prompt_sha256": sha256,
            "config": config,
        }
    else:
        meta = client.meta(config | {"url": client.url}, sha256, exclude=out)
    return (
        explain(client, voice, position) | {"n": n, "model": meta["model"], "meta": meta}
        for position in positions
        for n in range(samples)
    )

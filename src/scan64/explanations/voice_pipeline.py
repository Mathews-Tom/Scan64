"""The narrator voice pipeline: one code-written fact per call, every sentence checked (#454).

Code decides the chess (``role_facts``). A small local model only rewrites one
fact at a time as one lively sentence, in the voice of a card (traits plus
example sentences). Each call sees one fact and its role, and nothing else: no
earlier sentence, no position, no other fact, so it cannot move a piece or a
role from one fact to another.

Each sentence passes ``sentence_check.sentence_problems`` or is retried, at most
twice. After that, the plain fact text is used. A sentence that reuses a picture
word of an earlier sentence is written once more with a "Do not use:" list.
Code repairs a "!" after the played move, ends each sentence with a full stop
and joins the sentences.

This is the method the narrator voice benchmark measured (run 8 in #457: 91%
faithful on its positions, and 96% on held-out positions with gemma4-e4b
Q4_K_S in #462).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol, runtime_checkable

import chess

from scan64.explanations.role_facts import ROLES, RoleFact, colour_name, role_label
from scan64.explanations.sentence_check import (
    colour_words,
    finish,
    framing_problems,
    limit_exclamations,
    repair,
    sentence_problems,
)

# A failed sentence is written again at most this many times.
MAX_RETRIES: Final = 2
# One comment has at most one "!".
MAX_EXCLAMATIONS: Final = 1

VOICE_SYSTEM_PROMPT: Final = (
    "You rewrite one chess fact as one lively sentence for a club player. Keep every move, "
    "square and piece of the fact, and add none. Do not add any judgement, plan or danger the "
    "fact does not state. Never put '!' after the move {mover} played. Write plain text: one "
    "sentence, or two for a fact with several parts."
)

# The Tal-like card measured in #457 (runs 6-9): traits plus one example
# sentence for each example fact.
VOICE_NAME: Final = "Tal"
VOICE_TRAITS: Final = (
    "Lively and humorous. Turns the fact into a picture: pieces go hunting, kings dance, queens "
    "accept invitations. Short, with an exclamation now and then. The picture only retells the "
    "fact."
)
# (role, fact, sentence) for each example.
VOICE_EXAMPLES: Final[tuple[tuple[str, str, str], ...]] = (
    ("The move", "White played 1.Qd2.", "1.Qd2, a quiet queen step."),
    (
        "What it misses",
        "After 1.Qd2 the knight on f3 is undefended.",
        "But look: the knight on f3 is left an orphan, undefended!",
    ),
    (
        "What follows",
        "Black answers 1...Bxf3: the bishop takes the knight on f3 and wins it.",
        "Black's bishop swoops down with 1...Bxf3 and carries the knight off!",
    ),
    (
        "What White could have played",
        "White could have played 1.Be2 instead of 1.Qd2. The bishop on e2 defends the knight "
        "on f3.",
        "1.Be2 instead would have kept the knight on f3 under a roof, with the bishop on e2 "
        "beside it.",
    ),
    (
        "The move",
        "White played 1.Rb1, moving a rook away from the e-file.",
        "White's rook drives off to b1, away from the e-file.",
    ),
    ("What it misses", "The e-file is open.", "Yet the e-file is an open road!"),
    (
        "What follows",
        "Black answers 1...Re8, and Black's rook on e8 takes the open e-file.",
        "Black's rook happily rolls in with 1...Re8, and the rook on e8 takes the open e-file.",
    ),
    (
        "What White could have played",
        "White could have played 1.Re1 instead of 1.Rb1. The rook on e1 takes the open e-file "
        "first.",
        "1.Re1 instead would have put White's rook on e1, on that road first!",
    ),
    ("The move", "White played 1.a3.", "White plays a quiet 1.a3."),
    (
        "What it misses",
        "White's king is still in the centre.",
        "But White's king still sits in the middle of the house, in the centre!",
    ),
    (
        "What follows",
        "Black answers 1...d5 and opens the centre.",
        "Black kicks the door open with 1...d5 and opens the centre!",
    ),
    (
        "What White could have played",
        "White could have castled with 1.O-O instead of 1.a3. The king is then in safety first.",
        "Castling first with 1.O-O instead of 1.a3 would have put the king in safety.",
    ),
)


@runtime_checkable
class SentenceWriter(Protocol):
    """A provider that returns plain text for one system and one user message."""

    async def rewrite(self, system: str, user: str) -> str: ...


def voice_system_prompt(mover: str) -> str:
    return VOICE_SYSTEM_PROMPT.format(mover=mover)


def voice_prompt(role: str, fact: str, avoid: Sequence[str] = ()) -> str:
    """The user message for one fact: the card, its examples, then the fact."""
    parts = [f"Style: {VOICE_NAME}", f"How this style writes: {VOICE_TRAITS}", "Examples:"]
    parts.extend(
        f"Fact ({example_role}): {example_fact}\nSentence: {sentence}"
        for example_role, example_fact, sentence in VOICE_EXAMPLES
    )
    ask = "Now rewrite this fact."
    if _parts(fact) > 1:
        ask = "Now rewrite this fact in one or two sentences."
    if avoid:
        ask += f" Do not use: {', '.join(sorted(avoid))}."
    parts.append(f"{ask}\nFact ({role}): {fact}\nSentence:")
    return "\n\n".join(parts)


SentenceSource = Literal["model", "fact"]


@dataclass(frozen=True)
class VoicedSentence:
    fact: RoleFact
    text: str
    source: SentenceSource
    calls: int


@dataclass(frozen=True)
class VoicedExplanation:
    sentences: tuple[VoicedSentence, ...]
    # Facts left out by ``select_facts``: each role merges at most three.
    dropped_facts: int = 0

    @property
    def text(self) -> str:
        return " ".join(sentence.text for sentence in self.sentences)

    @property
    def fallback_sentences(self) -> int:
        """Sentences that use the plain fact text because every try failed the check."""
        return sum(sentence.source == "fact" for sentence in self.sentences)

    @property
    def calls(self) -> int:
        return sum(sentence.calls for sentence in self.sentences)


# Relation facts merged into a role's one fact, after its framing fact.
MAX_RELATION_FACTS: Final = 2


def _weight(fact: RoleFact) -> int:
    """How much a fact carries: a mate, then a check, then a capture, then the rest."""
    kinds = {relation.kind for relation in fact.relations}
    if kinds & {"mates", "stalemates"}:
        return 4
    if "gives_check" in kinds:
        return 3
    if "takes" in kinds:
        return 2
    return 1 if kinds - {"moves"} else 0


def _is_framing(fact: RoleFact) -> bool:
    """A fact that only names a move: "White played 1.Kd2.", "White could have played X ..."."""
    return all(relation.kind == "moves" for relation in fact.relations)


def _parts(text: str) -> int:
    return max(1, len(re.findall(r"[.!?](\s|$)", text.strip())))


def select_facts(facts: Sequence[RoleFact]) -> tuple[tuple[RoleFact, ...], int]:
    """One merged fact per role, in story order, and the number of facts left out.

    A role's fact starts with its framing fact when it has one ("White could
    have played 7.Qe2 instead of 7.Be2."), then adds up to two of its relation
    facts, chosen by ``_weight`` (a mate, a check, a capture, the rest). The
    chosen facts keep their own order, which is the order of the moves. The
    story order is the move, what it misses, what follows, and what the mover
    could have played.
    """
    chosen: list[RoleFact] = []
    used = 0
    for role in ROLES:
        of_role = [fact for fact in facts if fact.role == role]
        if not of_role:
            continue
        framing = next((fact for fact in of_role if _is_framing(fact)), None)
        others = [fact for fact in of_role if fact is not framing]
        picked = sorted(others, key=_weight, reverse=True)[:MAX_RELATION_FACTS]
        parts = [fact for fact in of_role if fact is framing or fact in picked]
        used += len(parts)
        chosen.append(
            RoleFact(
                role,
                " ".join(fact.text for fact in parts),
                tuple(relation for fact in parts for relation in fact.relations),
            )
        )
    return tuple(chosen), len(facts) - used


def _sides(fact: RoleFact) -> dict[str, str]:
    """The side of each piece the fact's relations name, by square."""
    sides: dict[str, str] = {}
    for relation in fact.relations:
        for piece in (relation.subject, relation.target, relation.through):
            if piece is not None:
                sides[chess.square_name(piece.square)] = colour_name(piece.colour).lower()
    return sides


def _first_line(raw: str) -> str:
    stripped = raw.strip()
    return stripped.splitlines()[0].strip() if stripped else ""


async def voice_facts(
    writer: SentenceWriter,
    facts: Sequence[RoleFact],
    *,
    mover: bool,
    played: str,
) -> VoicedExplanation:
    """Rewrite each fact as one checked sentence and join them.

    ``mover`` is the side that played ``played`` (``chess.WHITE`` is ``True``);
    ``played`` is its numbered SAN, such as ``5.Nxf7``. A provider error ends
    the pipeline: the caller falls back to its template.
    """
    system = voice_system_prompt(colour_name(mover))
    selected, dropped = select_facts(facts)
    used: set[str] = set()
    sentences: list[VoicedSentence] = []
    for fact in selected:
        role = role_label(fact.role, mover)
        kinds = [relation.kind for relation in fact.relations]
        sides = _sides(fact)
        allowed = min(2, _parts(fact.text))
        avoid: set[str] = set()
        chosen: str | None = None
        calls = 0
        for _ in range(1 + MAX_RETRIES):
            calls += 1
            raw = await writer.rewrite(system, voice_prompt(role, fact.text, sorted(avoid)))
            text = repair(_first_line(raw), played)
            if (
                not text
                or sentence_problems(text, fact.text, kinds, sides, allowed)
                or framing_problems(text, fact.role)
            ):
                continue
            repeated = colour_words(text, fact.text) & used
            if repeated and not avoid:
                # Keep this sentence in hand and ask once more without the repeated words.
                avoid = repeated
                chosen = chosen or text
                continue
            chosen = text
            break
        if chosen is None:
            sentences.append(VoicedSentence(fact, finish(fact.text), "fact", calls))
            continue
        sentences.append(VoicedSentence(fact, finish(chosen), "model", calls))
        used |= colour_words(chosen, fact.text)
    limited = limit_exclamations([sentence.text for sentence in sentences], MAX_EXCLAMATIONS)
    sentences = [
        dataclasses.replace(sentence, text=finish(text))
        for sentence, text in zip(sentences, limited, strict=True)
    ]
    return VoicedExplanation(tuple(sentences), dropped)


__all__ = [
    "MAX_EXCLAMATIONS",
    "MAX_RETRIES",
    "VOICE_EXAMPLES",
    "VOICE_NAME",
    "VOICE_SYSTEM_PROMPT",
    "VOICE_TRAITS",
    "SentenceWriter",
    "VoicedExplanation",
    "VoicedSentence",
    "select_facts",
    "voice_facts",
    "voice_prompt",
    "voice_system_prompt",
]

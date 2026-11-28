"""The phrase-bank narrator: lively explanations written by code, with no model (#461).

Code decides the chess (``role_facts``, #460). This module only words it: for
each kind of fact sentence it holds several hand-written variants in the
approved tone (#457, section 3). A variant fills its pieces, squares and moves
from the fact's own relations and adds no chess claim.

Each explanation picks its variants with a seed taken from its facts, so the
same facts always read the same way, and never uses one variant twice. Every
role's text passes the #454 sentence check against its fact; a role that does
not pass uses the plain fact text, so the result is faithful by construction.

This is the narrator when no model is available or granted, in place of the
template, and the baseline a model must beat to earn its GPU memory.
"""

from __future__ import annotations

import random
import re
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

import chess

from scan64.explanations.role_facts import (
    PieceOnSquare,
    Relation,
    RoleFact,
    colour_name,
    numbered_san,
)
from scan64.explanations.sentence_check import (
    finish,
    framing_problems,
    limit_exclamations,
    pin_problems,
    sentence_problems,
)
from scan64.explanations.voice_pipeline import (
    MAX_EXCLAMATIONS,
    VoicedExplanation,
    VoicedSentence,
    fact_sides,
    pinned_pieces,
    select_fact_parts,
    sentence_parts,
)


@dataclass(frozen=True)
class Slots:
    """What a variant may name: everything comes from the fact and its first relation."""

    fact: RoleFact
    relation: Relation
    board: chess.Board
    move: chess.Move
    # Numbered SAN of the relation's move, such as ``5.Nxf7``.
    san: str
    # The side that plays the relation's move.
    side: str
    other: str

    def piece(self, name: str) -> PieceOnSquare:
        value = getattr(self.relation, name)
        if value is None:
            raise ValueError(f"{self.relation.kind} has no {name}")
        return value  # type: ignore[no-any-return]

    @property
    def subject(self) -> str:
        return self.piece("subject").phrase()

    @property
    def target(self) -> str:
        return self.piece("target").phrase()

    @property
    def through(self) -> str:
        return self.piece("through").phrase()

    @property
    def mover(self) -> str:
        """The moving piece as the fact names it, on the square it leaves."""
        found = re.match(r"\S+: (.+?) (?:goes|steps|takes) ", self.fact.text)
        if found is None:
            raise ValueError(f"No moving piece in {self.fact.text!r}")
        return found.group(1)

    @property
    def square(self) -> str:
        if self.relation.square is None:
            raise ValueError(f"{self.relation.kind} has no square")
        return chess.square_name(self.relation.square)

    @property
    def way(self) -> str:
        if self.relation.direction is None:
            raise ValueError(f"{self.relation.kind} has no direction")
        return self.relation.direction

    @property
    def start(self) -> str:
        return chess.square_name(self.move.from_square)

    @property
    def end(self) -> str:
        return chess.square_name(self.move.to_square)

    def group(self, pattern: str, index: int = 1) -> str:
        found = re.search(pattern, self.fact.text)
        if found is None:
            raise ValueError(f"{pattern!r} does not match {self.fact.text!r}")
        return found.group(index)


Variant = Callable[[Slots], str]


@dataclass(frozen=True)
class Shape:
    """One kind of fact sentence and its variants; the first variant is the fact's own wording."""

    name: str
    role: str | None
    kind: str
    # The fact's text must match this, so a shape fits one sentence form only.
    pattern: str
    variants: tuple[Variant, ...]


def _possessive(side: str) -> str:
    return f"{side}'s"


SHAPES: Final[tuple[Shape, ...]] = (
    # --- a move, a capture, castling and promotion ---
    Shape(
        "castles",
        None,
        "moves",
        r"^\S+: (White|Black) castles (kingside|queenside); the king now stands on [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"{s.san}: {s.side} castles {s.group('castles (\\w+)')}, and the king settles "
                f"on {s.end}."
            ),
            lambda s: (
                f"{s.side} castles {s.group('castles (\\w+)')} with {s.san}; the king now "
                f"stands on {s.end}."
            ),
        ),
    ),
    Shape(
        "moves",
        None,
        "moves",
        r"^\S+: .+ (goes|steps) (forward|back|sideways) from [a-h][1-8] to [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.san} sends {s.mover} {s.way} from {s.start} to {s.end}.",
            lambda s: f"With {s.san}, {s.mover} sets off {s.way}, from {s.start} to {s.end}.",
            lambda s: f"{s.san}: off goes {s.mover}, {s.way} from {s.start} to {s.end}.",
        ),
    ),
    Shape(
        "takes",
        None,
        "takes",
        r"^\S+: .+ takes the \w+ on [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.san}: {s.mover} grabs {s.target}!",
            lambda s: f"{s.san}, and {s.mover} snaps up {s.target}.",
            lambda s: f"With {s.san}, {s.mover} helps itself to {s.target}.",
        ),
    ),
    Shape(
        "promotes",
        None,
        "moves",
        r"^The pawn on [a-h][1-8] becomes a \w+\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"The pawn on {s.end} turns into a {s.group('becomes a (\\w+)')}!",
            lambda s: f"And the pawn on {s.end} grows up into a {s.group('becomes a (\\w+)')}.",
        ),
    ),
    # --- what the move does ---
    Shape(
        "gives_check",
        None,
        "gives_check",
        r"^The \w+ on [a-h][1-8] gives check to the king on [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.subject.capitalize()} gives check to {s.target}!",
            lambda s: f"{s.subject.capitalize()} calls check on {s.target}.",
            lambda s: f"And {s.subject} gives check to {s.target}.",
        ),
    ),
    Shape(
        "mates",
        None,
        "mates",
        r"^\S+ gives check to the king on [a-h][1-8], and it is mate: the game ends, and "
        r"(White|Black) wins\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.san} is checkmate for {s.target}: the game is over, and {s.side} wins.",
            lambda s: f"And that is mate! {s.san} ends the game, and {s.side} wins.",
        ),
    ),
    Shape(
        "stalemates",
        None,
        "stalemates",
        r"^\S+ is stalemate: ",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"{s.san}, and it is stalemate: {s.other} has no legal move and is not in "
                "check, so the game ends at once as a draw."
            ),
        ),
    ),
    Shape(
        "blocks",
        None,
        "blocks",
        r"^The \w+ on [a-h][1-8] blocks the check from the \w+ on [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.subject.capitalize()} jumps into the way of the check from {s.through}.",
            lambda s: f"{s.subject.capitalize()} stands in front of the check from {s.through}.",
        ),
    ),
    Shape(
        "other_blocks",
        None,
        "blocks",
        r"^\S+ was not the only way to block the check: (.+) also blocks? it\.$",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"{s.san} blocks the check, but it was not the only way: "
                f"{s.group(': (.+) also blocks? it')} also "
                f"{s.group('also (blocks?) it')} it."
            ),
        ),
    ),
    Shape(
        "attacks",
        None,
        "attacks",
        r"^The \w+ on [a-h][1-8] attacks the \w+ on [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.subject.capitalize()} now eyes {s.target}.",
            lambda s: f"{s.subject.capitalize()} goes hunting for {s.target}.",
            lambda s: f"And {s.subject} has {s.target} in its sights.",
        ),
    ),
    Shape(
        "defends",
        None,
        "defends",
        r"^The \w+ on [a-h][1-8] defends the \w+ on [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.subject.capitalize()} keeps watch over {s.target}.",
            lambda s: f"{s.subject.capitalize()} now guards {s.target}.",
        ),
    ),
    Shape(
        "pins",
        None,
        "pins",
        r"^(The|White's|Black's) \w+ on [a-h][1-8] pins the \w+ on [a-h][1-8] to the king on "
        r"[a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.target.capitalize()} is now pinned to {s.through} by {s.subject}.",
            lambda s: f"{s.subject.capitalize()} pins {s.target} to {s.through}!",
        ),
    ),
    Shape(
        "opens",
        None,
        "opens",
        r"^Now the king on [a-h][1-8] can step to [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.subject.capitalize()} gets a way out to {s.square}.",
            lambda s: f"Now {s.subject} has {s.square} to step to.",
        ),
    ),
    Shape(
        "frees",
        None,
        "frees",
        r"^\S+ frees the \w+ on [a-h][1-8]: it could not move before\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.san} lets {s.target} out: it could not move before.",
            lambda s: f"{s.target.capitalize()}, stuck until now, is free after {s.san}.",
        ),
    ),
    Shape(
        "frees_too",
        None,
        "frees",
        r"^\S+ frees the \w+ on [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"{s.san} would also let {s.target} out.",
            lambda s: f"And {s.san} would set {s.target} free.",
        ),
    ),
    # --- what the move leaves ---
    Shape(
        "already_attacks",
        "what_it_misses",
        "attacks",
        r"^(White|Black)'s \w+ on [a-h][1-8] already attacks (White|Black)'s \w+ on [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"Meanwhile {s.piece('subject').owned_phrase()} already has "
                f"{s.piece('target').owned_phrase()} in its sights."
            ),
            lambda s: (
                f"And {s.piece('subject').owned_phrase()} was already eyeing "
                f"{s.piece('target').owned_phrase()}, and still is."
            ),
        ),
    ),
    Shape(
        "left_attacked",
        "what_it_misses",
        "attacks",
        r"^(White|Black)'s \w+ on [a-h][1-8] attacks (White|Black)'s \w+ on [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"But look: {s.piece('subject').owned_phrase()} eyes "
                f"{s.piece('target').owned_phrase()}."
            ),
            lambda s: (
                f"Now {s.piece('subject').owned_phrase()} has "
                f"{s.piece('target').owned_phrase()} in its sights."
            ),
        ),
    ),
    Shape(
        "undefended",
        "what_it_misses",
        "undefended",
        r"^After \S+ nothing defends the \w+ on [a-h][1-8]\.$",
        (
            lambda s: s.fact.text,
            lambda s: f"After {s.san}, {s.target} is left all alone, with no defender.",
            lambda s: f"After {s.san}, nobody looks after {s.target} any more.",
            lambda s: f"After {s.san}, {s.target} stands undefended.",
        ),
    ),
    Shape(
        "held_by_pin",
        "what_it_misses",
        "pins",
        r"^(White|Black)'s \w+ on [a-h][1-8] (cannot move|is pinned), because the \w+ on "
        r"[a-h][1-8] pins it to the king on [a-h][1-8]\.$",
        (
            lambda s: (
                f"{s.piece('target').owned_phrase()} {s.group('(cannot move|is pinned)')}, "
                f"because {s.subject} pins {s.target} to {s.through}."
            ),
            lambda s: (
                f"{s.piece('target').owned_phrase()} is stuck: {s.subject} pins "
                f"{s.target} to {s.through}"
                + (", so it cannot move." if "cannot move" in s.fact.text else ".")
            ),
        ),
    ),
    Shape(
        "king_shut_in",
        "what_it_misses",
        "shuts_in",
        r"^(White|Black)'s king on [a-h][1-8] is shut in by its own pawns? on (.+)\.$",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"{s.subject.capitalize()} is boxed in by its own "
                f"{s.group('its own (pawns? on .+)[.]$')}."
            ),
            lambda s: f"Its own {s.group('its own (pawns? on .+)[.]$')} wall in {s.subject}.",
        ),
    ),
    Shape(
        "already_shut_in",
        "what_it_misses",
        "shuts_in",
        r"^The \w+ on [a-h][1-8] was already shut in by (.+) before (\S+), and \S+ does not "
        r"free it\.$",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"{s.subject.capitalize()} was already shut in by "
                f"{s.group('shut in by (.+) before')} before {s.san}, and after {s.san} it "
                "still cannot get out."
            ),
            lambda s: (
                f"{s.san} does not free {s.subject}: it was already shut in by "
                f"{s.group('shut in by (.+) before')} before, and still is."
            ),
        ),
    ),
    # --- what follows ---
    Shape(
        "cannot_take",
        "what_follows",
        "pins",
        r"^The \w+ on [a-h][1-8] cannot take the \w+ on [a-h][1-8]: the \w+ on [a-h][1-8] pins "
        r"it to the king on [a-h][1-8]\.$",
        (
            lambda s: (
                f"{s.target.capitalize()} cannot take {s.group('cannot take (the .+?):')}: "
                f"{s.subject} pins {s.target} to {s.through}."
            ),
            lambda s: (
                f"{s.target.capitalize()} cannot touch {s.group('cannot take (the .+?):')}, "
                f"because {s.subject} pins {s.target} to {s.through}."
            ),
        ),
    ),
    Shape(
        "check_then_takes",
        None,
        "takes",
        r"^\S+ gives check; after .+, the \w+ on [a-h][1-8] takes the \w+ on [a-h][1-8] "
        r"\(\S+\)\.$",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"First {s.group('^(\\S+) gives check')} gives check; then, "
                f"{s.group('; (after .+?), the')}, "
                f"{s.group(', (the \\w+ on [a-h][1-8]) takes')} takes {s.target} with {s.san}."
            ),
        ),
    ),
    Shape(
        "line_mates",
        None,
        "mates",
        r"^The line goes on to \S+, and it is mate: the game ends, and (White|Black) wins\.$",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"And the line ends in mate with {s.san}: the game ends, and "
                f"{s.group('and (White|Black) wins')} wins."
            ),
            lambda s: (
                f"Then comes {s.san}, and it is mate: {s.group('and (White|Black) wins')} wins."
            ),
        ),
    ),
    Shape(
        "line_stalemates",
        None,
        "stalemates",
        r"^The line goes on to \S+, and it is stalemate: ",
        (
            lambda s: s.fact.text,
            lambda s: f"Then comes {s.san}, and it is stalemate: {s.group('stalemate: (.+)$')}",
        ),
    ),
    # --- what the mover could have played ---
    Shape(
        "could_have_played",
        "could_have_played",
        "moves",
        r"^(White|Black) could have played \S+ instead of \S+\.$",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"Instead of {s.group('instead of (\\S+)[.]$')}, {s.side} could have played "
                f"{s.san}."
            ),
            lambda s: (
                f"{s.san} was there for {s.side} instead of {s.group('instead of (\\S+)[.]$')}."
            ),
            lambda s: (
                f"What about {s.san}? {s.side} could have played it instead of "
                f"{s.group('instead of (\\S+)[.]$')}."
            ),
        ),
    ),
    Shape(
        "prevents",
        "could_have_played",
        "prevents",
        r"^After \S+, (White|Black) must answer the check, so the \w+ on [a-h][1-8] cannot "
        r"play \S+ at once\.$",
        (
            lambda s: s.fact.text,
            lambda s: (
                f"After {s.san}, {s.other} is busy with the check, so {s.target} cannot play "
                f"{s.group('cannot play (\\S+) at once')} at once."
            ),
        ),
    ),
)


def shape_of(fact: RoleFact) -> Shape | None:
    """The shape a fact's sentence has, or ``None`` when no variant fits it."""
    if not fact.relations:
        return None
    kind = fact.relations[0].kind
    for shape in SHAPES:
        if shape.kind != kind or (shape.role is not None and shape.role != fact.role):
            continue
        if re.search(shape.pattern, fact.text):
            return shape
    return None


def slots(fact: RoleFact) -> Slots:
    relation = fact.relations[0]
    board = chess.Board(relation.fen)
    move = chess.Move.from_uci(relation.move)
    side = colour_name(board.turn)
    return Slots(
        fact, relation, board, move, numbered_san(board, move), side, colour_name(not board.turn)
    )


def variants(fact: RoleFact) -> tuple[str, ...]:
    """Every variant of a fact, the plain fact first; just the fact when no shape fits."""
    shape = shape_of(fact)
    if shape is None:
        return (fact.text,)
    filled = slots(fact)
    return tuple(variant(filled) for variant in shape.variants)


def explanation_seed(facts: Sequence[RoleFact]) -> int:
    """A seed fixed by the facts: the same facts always read the same way."""
    return zlib.crc32("\n".join(fact.text for fact in facts).encode("utf-8"))


def role_problems(text: str, merged: RoleFact) -> list[str]:
    """The #454 checks on one role's text against its merged fact."""
    kinds = [relation.kind for relation in merged.relations]
    return [
        *sentence_problems(
            text, merged.text, kinds, fact_sides(merged), max(1, sentence_parts(merged.text))
        ),
        *framing_problems(text, merged.role),
        *pin_problems(text, pinned_pieces(merged)),
    ]


def phrase_facts(facts: Sequence[RoleFact], *, seed: int | None = None) -> VoicedExplanation:
    """Word the facts with the phrase bank: one text per role, in story order."""
    rng = random.Random(explanation_seed(facts) if seed is None else seed)
    used: set[tuple[str, int]] = set()
    sentences: list[VoicedSentence] = []
    parts_by_role, dropped = select_fact_parts(facts)
    for merged, parts in parts_by_role:
        texts = []
        for part in parts:
            shape = shape_of(part)
            options = variants(part)
            fresh = [
                index
                for index in range(len(options))
                if shape is None or (shape.name, index) not in used
            ]
            index = rng.choice(fresh or list(range(len(options))))
            if shape is not None:
                used.add((shape.name, index))
            texts.append(finish(options[index]))
        text = " ".join(texts)
        if role_problems(text, merged):
            sentences.append(VoicedSentence(merged, finish(merged.text), "fact", 0))
        else:
            sentences.append(VoicedSentence(merged, text, "phrase", 0))
    limited = limit_exclamations([sentence.text for sentence in sentences], MAX_EXCLAMATIONS)
    return VoicedExplanation(
        tuple(
            VoicedSentence(sentence.fact, finish(text), sentence.source, 0)
            for sentence, text in zip(sentences, limited, strict=True)
        ),
        dropped,
    )


__all__ = [
    "SHAPES",
    "Shape",
    "Slots",
    "explanation_seed",
    "phrase_facts",
    "role_problems",
    "shape_of",
    "variants",
]

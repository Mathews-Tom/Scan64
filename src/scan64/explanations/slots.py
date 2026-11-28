"""Named fact slots: the narrator writes prose, code writes every chess token (#386).

The narrator LLM writes claim text that names facts by slot, for example
``"You played {played}, but {capture:best}."``. Code builds the slot catalogue
from the engine and board facts (``facts.py``, ``board_lines.py``) and replaces
each slot with its value. Slot values are the only chess tokens the rendered
text holds: SAN copied from the facts, piece-on-square phrases, signed pawn
units and the other fact clauses below.

Grounding a slot claim (``validator.py``) has two fixed checks:

1. every slot names a fact listed for the claim's evidence item, and
2. the prose outside the slots holds no chess content (``CHESS_PROSE``) and
   no relational claim the item's facts do not support (``claims.py``, #431).

``CHESS_PROSE`` is one closed rule set. It must not grow with each phrasing: a
chess fact that a model wants to state and no slot covers is a missing slot,
not a missing prose rule.

Slot names: ``{kind}`` for a position fact, ``{kind:LINE}`` for a fact about a
whole line, ``{kind:LINE:N}`` for ply N of a line, ``{kind:N}`` for the Nth
piece or square of an evidence item. ``played`` and ``best`` stand in for a
line ID in move slots: ``{capture:best}``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

import chess

from scan64.explanations.claims import ClaimFacts, factor_group
from scan64.explanations.factor_facts import FactorChange, FactorFacts
from scan64.explanations.facts import (
    CentipawnEvaluation,
    EvidenceFacts,
    MateEvaluation,
    MoveFacts,
    NarratorFacts,
    PieceOnSquare,
    UnknownEvaluation,
)

# One slot occurrence: ``{name}``, ``{kind:arg}`` or ``{kind:arg:arg}``.
SLOT_TOKEN = re.compile(r"\{\s*(?P<name>[a-z_]+(?::[A-Za-z0-9_-]+){0,2})\s*\}")

# The fixed free-prose rule set (see the module docstring), in two parts.
#
# NOTATION is always rejected outside a slot: squares, SAN, UCI, move numbers,
# evaluations and mate counts all contain a digit ("8.5 pawns", "+150 cp"); the
# others are mate and check suffixes, figurines and castling notation.
NOTATION = re.compile(r"\d|[#♔-♟]|\b[O0]-[O0]")
# CHESS_WORDS outside a slot are rejected unless the position's facts contain
# the word (the vocabulary rule, ``chess_words_outside_slots``).
CHESS_WORDS = re.compile(
    "|".join(
        (
            r"\b(?:king|queen|rook|bishop|knight|pawn)s?\b",
            r"\b(?:white|black)\b",
            r"\b(?:check|checks|checked|checking|checkmat\w*|mate|mates|mated|mating|stalemat\w*)\b",
            r"\b(?:captur\w*|recaptur\w*|take|takes|took|taking|exchang\w*|trad(?:e|es|ed|ing))\b",
            r"\b(?:fork\w*|pin|pins|pinned|pinning|skewer\w*|discover\w*|back[-\s]rank"
            r"|overload\w*|deflect\w*|decoy\w*|sacrific\w*|zwischenzug|en\s+passant"
            r"|promot\w*|castl\w*)\b",
            r"\b(?:threat\w*|attack\w*|hang\w*|hung|undefended|defend\w*|en\s+prise)\b",
            r"\b(?:material|centipawns?|win|wins|winning|won|lose|loses|losing|lost"
            r"|gain|gains|gained|gaining|ahead|behind|advantage|equal)\b",
            r"\b[a-h]-(?:file|pawn)\b|\b(?:kingside|queenside|files?|ranks?|diagonals?)\b",
        )
    ),
    re.IGNORECASE,
)
# Both parts: any match is chess content (the #386 blanket rule).
CHESS_PROSE = re.compile(f"{NOTATION.pattern}|{CHESS_WORDS.pattern}", re.IGNORECASE)

_WORD = re.compile(r"[a-z]+")


def _word_form(word: str) -> str:
    """Lower case, a plural ``s`` removed: ``Threats`` -> ``threat``."""
    word = word.lower()
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def vocabulary(phrases: Iterable[str]) -> frozenset[str]:
    """The word forms of ``phrases``: fact names and slot names."""
    return frozenset(_word_form(w) for phrase in phrases for w in _WORD.findall(phrase.lower()))


def blank_phrases(prose: str, phrases: Iterable[str]) -> str:
    """``prose`` with every occurrence of a phrase blanked (word forms compared).

    A threats factor name admits its words only with its head noun (#431): a
    name ending in "threats" admits its endings (``safe pawn threats``: "safe
    pawn threats", "pawn threats", "threats", never a bare "pawn"); any other
    name only as a whole ("slider on the queen", never a bare "queen").
    """
    tokens = list(_WORD.finditer(prose.lower()))
    forms = [_word_form(token[0]) for token in tokens]
    chars = list(prose)
    for phrase in phrases:
        words = [_word_form(w) for w in _WORD.findall(phrase.lower())]
        cuts = range(len(words)) if words[-1:] == ["threat"] else range(1 if words else 0)
        for target in (words[cut:] for cut in cuts):
            for start in range(len(forms) - len(target) + 1):
                if forms[start : start + len(target)] == target:
                    begin, end = tokens[start].start(), tokens[start + len(target) - 1].end()
                    chars[begin:end] = " " * (end - begin)
    return "".join(chars)


def factor_names(changes: Iterable[FactorChange]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Passed factor names as (vocabulary terms, phrases), first occurrence order.

    A Threats-group name ("rook threats", "minor piece threats") names the
    attacking piece type, not a piece of the position, so it is a phrase
    (``blank_phrases``). Every other name ("king safety", "weak queen",
    "connected pawns") names the side's own king, pieces or pawns, and its
    words are vocabulary words; claims about them are read by the claim rule.
    """
    names = list(
        dict.fromkeys((change.name, factor_group(change) == "Threats") for change in changes)
    )
    return (
        tuple(name for name, threats in names if not threats),
        tuple(name for name, threats in names if threats),
    )


def chess_words_outside_slots(
    prose: str, words: frozenset[str], phrases: Iterable[str] = ()
) -> str | None:
    """The first chess token in ``prose`` that the facts do not contain, else ``None``.

    Notation (digits, ``#``, ``O-O``, figurines) is never allowed. A chess word
    is allowed when each of its words is in ``words`` (``vocabulary``) or it
    lies inside one of ``phrases`` (the passed factor names).
    """
    if (notation := NOTATION.search(prose)) is not None:
        return notation[0]
    for match in CHESS_WORDS.finditer(blank_phrases(prose, phrases)):
        if not {_word_form(w) for w in _WORD.findall(match[0].lower())} <= words:
            return match[0]
    return None


# Prompt layout (#408): plies of a line shown in the prompt, and plies whose
# clause slots ({capture:L:N} ...) the prompt lists. Validation is not limited.
PROMPT_LINE_PLIES = 6
PROMPT_CLAUSE_PLIES = 2


@dataclass(frozen=True)
class Slot:
    """One fact the narrator may name. ``value`` is code-rendered.

    ``moves`` are the UCI moves the value states, in line order; ``phrase``
    marks a value that starts with a word (capitalised at a sentence start),
    as opposed to SAN, which keeps its case. ``source`` labels a value that
    comes from a module other than the engine and the board, such as the
    factor module version of a factor slot.

    ``group`` (a line ID), ``listed`` and ``preview`` shape the prompt only:
    the prompt lists a line's slots once under the line, leaves out unlisted
    slots (every ``{move:L:N}``, clauses of later plies) and shows ``preview``
    in place of a long value. ``cite`` is the line prefix a claim using this
    slot is linked to when ``moves`` is empty.
    """

    name: str
    value: str
    meaning: str
    moves: tuple[str, ...] = ()
    phrase: bool = False
    source: str | None = None
    # Prompt layout only; validation accepts every slot of the catalogue.
    group: str | None = None
    listed: bool = True
    preview: str | None = None
    cite: tuple[str, ...] | None = None

    @property
    def citation(self) -> tuple[str, ...]:
        """The line prefix a claim using this slot cites when it names none."""
        return self.cite if self.cite is not None else self.moves


@dataclass(frozen=True)
class SlotCatalogue:
    """Slots per evidence item: a claim may use only its own item's slots.

    ``combined`` maps an evidence item whose slots are exactly those of other
    items (such as the Analysis Board ``comparison``) to those items; the
    prompt names them as the item's lines instead of listing every slot again.
    """

    by_evidence: Mapping[str, Mapping[str, Slot]]
    shared: frozenset[str] = field(default_factory=frozenset)
    # Fact names per evidence item beyond its slot names: the mistake class
    # and the evidence tactic (vocabulary rule).
    terms: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # Names of the passed Threats-group factor changes per evidence item,
    # admitted only with their head noun (#431, ``factor_names``).
    phrases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # Relational facts per evidence item (``claims.py``); an item without an
    # entry supports no claim.
    claims: Mapping[str, ClaimFacts] = field(default_factory=dict)
    # An example claim text built from this catalogue's own slots (prompt only).
    example: str | None = None
    combined: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def slots_for(self, evidence_id: str) -> Mapping[str, Slot]:
        return self.by_evidence.get(evidence_id, {})

    def vocabulary(self, evidence_id: str) -> frozenset[str]:
        """Words a claim citing ``evidence_id`` may use outside slots.

        Factor slot names add no word: the passed factor names come in
        ``terms`` or, for threats, ``phrases`` (``factor_names``).
        """
        names = (name for name in self.slots_for(evidence_id) if not name.startswith("factor:"))
        return vocabulary((*names, *self.terms.get(evidence_id, ())))

    def claim_facts(self, evidence_id: str) -> ClaimFacts:
        return self.claims.get(evidence_id, ClaimFacts())

    def names(self) -> frozenset[str]:
        return frozenset(name for slots in self.by_evidence.values() for name in slots)

    def prompt(self) -> str:
        """The slot list, the narrator's only user message (#408: no facts JSON).

        Each listed slot appears once: position slots, then each line's slots
        under the line, then each evidence item with its lines, its own slots
        and the chess words it allows outside slots.
        """
        out = ["Slot list ({name} = the value code writes in its place):"]
        items = list(self.by_evidence.items())
        if not items:
            return out[0]
        shown: set[str] = set()

        def emit(slot: Slot) -> None:
            if slot.listed and slot.value not in shown:
                shown.add(slot.value)
                out.append(_prompt_line(slot))

        first = items[0][1]
        shared = [first[name] for name in first if name in self.shared]
        if shared:
            out.append("For every evidence item:")
            for slot in shared:
                emit(slot)
        groups: dict[str, list[Slot]] = {}
        for _, slots in items:
            for slot in slots.values():
                if slot.group is not None and slot.name not in self.shared:
                    members = groups.setdefault(slot.group, [])
                    if slot not in members:
                        members.append(slot)
        for group, members in groups.items():
            plies = sum(1 for slot in members if slot.name.startswith(f"move:{group}:"))
            note = f" {{move:{group}:N}} = move N, N from 1 to {plies}." if plies else ""
            out.append(f"Line {group}:{note}")
            for slot in members:
                emit(slot)
        sources = sorted({s.source for _, slots in items for s in slots.values() if s.source})
        for evidence_id, slots in items:
            if evidence_id in self.combined:
                used = list(self.combined[evidence_id])
            else:
                used = [g for g in groups if any(s.group == g for s in slots.values())]
            lines = f"; lines {', '.join(used)}" if used else ""
            out.append(f'evidence_ref "{evidence_id}"{lines}:')
            for name, slot in slots.items():
                if slot.group is None and name not in self.shared:
                    emit(slot)
            allowed = sorted(w for w in self.vocabulary(evidence_id) if CHESS_WORDS.fullmatch(w))
            allowed += sorted(p for p in self.phrases.get(evidence_id, ()) if CHESS_WORDS.search(p))
            if allowed:
                out.append(f"Allowed plain words: {', '.join(allowed)}.")
        out += [f"Factor slots ({{factor:...}}) come from {source}." for source in sources]
        if not sources:
            out.append("No factor slot: no cause was measured, so name no cause.")
        if self.example:
            out.append(f'Example claim text with these slots: "{self.example}"')
        return "\n".join(out)


def _prompt_line(slot: Slot) -> str:
    meaning = f" ({slot.meaning})" if slot.meaning else ""
    return f"{{{slot.name}}} = {slot.preview or slot.value}{meaning}"


def render(text: str, slots: Mapping[str, Slot]) -> str:
    """Replace every slot in ``text``; the caller has checked that each exists."""

    def value(match: re.Match[str]) -> str:
        slot = slots[match.group("name")]
        start = text[: match.start()].rstrip()
        if slot.phrase and (not start or start[-1] in ".!?"):
            return slot.value[:1].upper() + slot.value[1:]
        return slot.value

    return SLOT_TOKEN.sub(value, text)


def slot_names(text: str) -> list[str]:
    """Slot names in ``text``, in order, repeats kept."""
    return [match.group("name") for match in SLOT_TOKEN.finditer(text)]


def prose_outside_slots(text: str) -> str:
    """``text`` with each slot replaced by a neutral placeholder."""
    return SLOT_TOKEN.sub(" ~ ", text)


# --- Value rendering ---------------------------------------------------------------


def piece_phrase(piece: PieceOnSquare) -> str:
    """``the black queen on g5``."""
    return f"the {piece.colour} {piece.piece} on {piece.square}"


def pawn_units(centipawns: int, *, signed: bool) -> str:
    """``+8.50 pawns`` (signed) or ``8.50 pawns``."""
    number = f"{centipawns / 100:+.2f}" if signed else f"{abs(centipawns) / 100:.2f}"
    return f"{number} pawns"


def evaluation_value(
    evaluation: CentipawnEvaluation | MateEvaluation | UnknownEvaluation,
    *,
    side: str,
    other: str,
) -> str | None:
    """A known evaluation from ``side``'s view, or ``None`` for an unknown one."""
    if isinstance(evaluation, CentipawnEvaluation):
        return pawn_units(evaluation.centipawns, signed=True)
    if isinstance(evaluation, MateEvaluation) and evaluation.mate_in != 0:
        winner = side if evaluation.mate_in > 0 else other
        return f"mate in {abs(evaluation.mate_in)} for {winner}"
    return None


def numbered_san(board: chess.Board, uci: Sequence[str]) -> str:
    """``12.Nf3 Nc6 13.O-O`` or ``12...Nc6 13.O-O`` from ``board``."""
    replay = board.copy(stack=False)
    parts = []
    for index, token in enumerate(uci):
        move = chess.Move.from_uci(token)
        san = replay.san(move)
        if replay.turn == chess.WHITE:
            parts.append(f"{replay.fullmove_number}.{san}")
        elif index == 0:
            parts.append(f"{replay.fullmove_number}...{san}")
        else:
            parts.append(san)
        replay.push(move)
    return " ".join(parts)


def _joined(phrases: Iterable[str]) -> str:
    items = list(phrases)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def move_event_slots(key: str, move: MoveFacts, moves: tuple[str, ...]) -> list[Slot]:
    """Clause slots for one move: capture, check or mate, threats, promotion, castling.

    ``key`` is ``played``, ``best`` or ``LINE:N``; ``moves`` are the UCI moves
    from the line start up to and including this move.
    """
    san = move.san
    slots = []
    if move.captures is not None:
        slots.append(
            Slot(
                f"capture:{key}",
                f"{san} captures {piece_phrase(move.captures)}",
                "",
                moves,
            )
        )
    if move.checkmate:
        slots.append(Slot(f"mate:{key}", f"{san} is checkmate", "", moves))
    elif move.check:
        slots.append(Slot(f"check:{key}", f"{san} gives check", "", moves))
    if move.new_threats:
        targets = _joined(
            f"{threat.colour} {threat.piece} on {threat.square}" for threat in move.new_threats
        )
        slots.append(
            Slot(
                f"threat:{key}",
                f"{san} attacks the undefended {targets}",
                "",
                moves,
            )
        )
    if move.promotion is not None:
        slots.append(
            Slot(
                f"promotion:{key}",
                f"{san} promotes to a {move.promotion}",
                "",
                moves,
            )
        )
    if move.castling is not None:
        slots.append(Slot(f"castle:{key}", f"{san} castles {move.castling}", "", moves))
    return slots


def line_slots(
    line_id: str,
    board: chess.Board,
    uci: Sequence[str],
    moves: Sequence[MoveFacts],
    *,
    mover_words: Mapping[str, str] | None = None,
) -> list[Slot]:
    """``{line:L}``, ``{move:L:N}`` and the clause slots of every ply of one line.

    The prompt shows the first ``PROMPT_LINE_PLIES`` plies of the line and the
    clause slots of the first ``PROMPT_CLAUSE_PLIES`` plies; every slot is valid.
    """
    full = tuple(uci)
    value = numbered_san(board, full)
    preview = None
    if len(full) > PROMPT_LINE_PLIES:
        preview = f"{numbered_san(board, full[:PROMPT_LINE_PLIES])} ... ({len(full)} moves)"
    slots = [Slot(f"line:{line_id}", value, "", full, group=line_id, preview=preview)]
    for move in moves:
        prefix = full[: move.ply]
        slots.append(
            Slot(f"move:{line_id}:{move.ply}", move.san, "", prefix, group=line_id, listed=False)
        )
        who = (mover_words or {}).get(move.mover, "")
        slots += [
            replace(slot, meaning=who, group=line_id, listed=move.ply <= PROMPT_CLAUSE_PLIES)
            for slot in move_event_slots(f"{line_id}:{move.ply}", move, prefix)
        ]
    return slots


def factor_value(change: FactorChange, side: str, reference: str, lead: str = "") -> str:
    """A clause stating one change, for example (``reference`` in brackets):

    - ``""``: ``after your move, king safety fell by 0.65 pawns for you``;
    - ``"after the best move"``: ``after your move, king safety is 0.60 pawns worse for
      you than after the best move``.

    ``lead`` (``after your move, ``; Analysis Board ``in line 2, ``) names the
    position the change belongs to, so the value is true wherever it is placed.
    """
    amount = f"{abs(change.change_pawns):.2f} pawns"
    if not reference:
        direction = "rose" if change.change_pawns > 0 else "fell"
        return f"{lead}{change.name} {direction} by {amount} for {side}"
    relation = "better" if change.change_pawns > 0 else "worse"
    return f"{lead}{change.name} is {amount} {relation} for {side} than {reference}"


# The reference point of a lesson factor change, as code words it, and the
# slot suffix: {factor:KEY:played} is what your move changed (from the
# position before it), {factor:KEY:best} the gap to the best move.
FACTOR_REFERENCES = {"before": "", "best": "after the best move"}
FACTOR_SLOT_SUFFIX = {"before": "played", "best": "best"}


def factor_slots(
    factors: FactorFacts | None,
    *,
    side: str,
    references: Mapping[str, str],
    suffix: str | None = None,
    lead: str = "",
) -> list[Slot]:
    """``{factor:KEY:REF}`` per passed change; ``suffix`` replaces ``REF`` (Analysis Board)."""
    if factors is None:
        return []
    return [
        Slot(
            f"factor:{change.key}:{suffix or FACTOR_SLOT_SUFFIX[change.compared_with]}",
            factor_value(change, side, references[change.compared_with], lead),
            "",
            phrase=True,
            source=factors.source,
        )
        for change in factors.changes
    ]


# --- Lesson catalogue (NarratorFacts) ----------------------------------------------

LEARNER = "the learner"
OPPONENT = "the opponent"
_LESSON_MOVERS = {"learner": "your move", "opponent": "your opponent's move"}

TACTIC_NAMES = {
    "knight_fork": "knight fork",
    "fork": "fork",
    "pin": "pin",
    "skewer": "skewer",
    "discovered_attack": "discovered attack",
    "back_rank": "back-rank mate",
    "overloaded_defender": "overloaded defender",
}


def _evidence_slots(item: EvidenceFacts) -> list[Slot]:
    slots = []
    if item.tactic is not None and item.tactic in TACTIC_NAMES:
        slots.append(
            Slot("tactic", TACTIC_NAMES[item.tactic], "the tactic in this evidence", phrase=True)
        )
    if item.mate_in is not None and item.mate_in > 0:
        slots.append(
            Slot("mate_in", f"mate in {item.mate_in}", "the mate this evidence proves", phrase=True)
        )
    if item.material_gain is not None and item.material_gain != 0:
        points = abs(item.material_gain)
        slots.append(
            Slot(
                "material_gain",
                f"a material swing of {points} point{'s' if points != 1 else ''}",
                "material the evidence line wins, in points",
                phrase=True,
            )
        )
    for index, piece in enumerate(item.pieces, start=1):
        role = piece.role.replace("_", " ")
        slots.append(Slot(f"piece:{index}", piece_phrase(piece), role, phrase=True))
    for index, square in enumerate(item.squares, start=1):
        slots.append(Slot(f"square:{index}", square, "a square in this evidence"))
    return slots


def lesson_slot_catalogue(facts: NarratorFacts) -> SlotCatalogue:
    """Every slot a lesson narration may use, per evidence item."""
    position = facts.position
    board = chess.Board(position.fen)
    shared: list[Slot] = []
    played = position.played_move
    shared.append(Slot("played", played.san, "your move, the mistake", (played.uci,)))
    shared += move_event_slots("played", played, (played.uci,))
    best = position.best_move
    if best is not None:
        shared.append(Slot("best", best.san, "the move you should have played", (best.uci,)))
        shared += move_event_slots("best", best, (best.uci,))
    shared.append(Slot("mistake", position.mistake_class.name, "the mistake class", phrase=True))
    for name, evaluation, meaning in (
        ("eval_before", position.evaluation_before, "evaluation before your move, your view"),
        ("eval_after", position.evaluation_after, "evaluation after your move, your view"),
    ):
        value = evaluation_value(evaluation, side=LEARNER, other=OPPONENT)
        if value is not None:
            shared.append(Slot(name, value, meaning, phrase=value.startswith("mate")))
    shared += factor_slots(
        facts.factors, side="you", references=FACTOR_REFERENCES, lead="after your move, "
    )
    difference = position.evaluation_difference_cp
    if difference is not None and difference > 0:
        shared.append(
            Slot(
                "eval_diff",
                pawn_units(difference, signed=False),
                "how much better the best move scores than your move",
            )
        )
    by_line = {
        line.line_id: line_slots(
            line.line_id, board, line.uci, line.moves, mover_words=_LESSON_MOVERS
        )
        for line in facts.lines
    }
    by_evidence: dict[str, dict[str, Slot]] = {}
    terms: dict[str, tuple[str, ...]] = {}
    changes = facts.factors.changes if facts.factors else ()
    factor_terms, factor_phrases = factor_names(changes)
    lines = {line.line_id: line.moves for line in facts.lines}
    claims: dict[str, ClaimFacts] = {}
    for item in facts.evidence:
        slots = {slot.name: slot for slot in shared}
        for line_id in item.line_ids:
            slots.update((slot.name, slot) for slot in by_line[line_id])
        slots.update((slot.name, slot) for slot in _evidence_slots(item))
        by_evidence[item.evidence_id] = slots
        tactic = (item.tactic.replace("_", " "),) if item.tactic else ()
        terms[item.evidence_id] = (position.mistake_class.name, *tactic, *factor_terms)
        moves = [(played,), *([(best,)] if best is not None else [])]
        claims[item.evidence_id] = ClaimFacts.collect(
            position.material,
            (position.evaluation_before, position.evaluation_after, position.best_move_evaluation),
            [*moves, *(lines[line_id] for line_id in item.line_ids)],
            changes,
        )
    return SlotCatalogue(
        by_evidence=by_evidence,
        shared=frozenset(slot.name for slot in shared),
        terms=terms,
        phrases={item.evidence_id: factor_phrases for item in facts.evidence},
        claims=claims,
        example=_lesson_example({slot.name for slot in shared}),
    )


def _lesson_example(names: set[str]) -> str:
    """``You played {played}, but {best} was stronger: {capture:best}.`` from existing slots."""
    if "best" not in names:
        return "You played {played}, a {mistake}."
    text = "You played {played}, but {best} was stronger"
    clause = next(
        (
            f"{kind}:best"
            for kind in ("capture", "mate", "check", "threat")
            if f"{kind}:best" in names
        ),
        None,
    )
    text += f": {{{clause}}}." if clause else "."
    factor = next((name for name in sorted(names) if name.startswith("factor:")), None)
    if factor is not None:
        text += f" That was a {{mistake}}: {{{factor}}}."
    return text


__all__ = [
    "CHESS_PROSE",
    "CHESS_WORDS",
    "NOTATION",
    "LEARNER",
    "OPPONENT",
    "SLOT_TOKEN",
    "TACTIC_NAMES",
    "Slot",
    "SlotCatalogue",
    "chess_words_outside_slots",
    "evaluation_value",
    "FACTOR_REFERENCES",
    "FACTOR_SLOT_SUFFIX",
    "factor_names",
    "factor_slots",
    "factor_value",
    "lesson_slot_catalogue",
    "line_slots",
    "move_event_slots",
    "numbered_san",
    "pawn_units",
    "piece_phrase",
    "prose_outside_slots",
    "render",
    "slot_names",
    "vocabulary",
]

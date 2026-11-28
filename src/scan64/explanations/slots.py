"""Named fact slots: the narrator writes prose, code writes every chess token (#386).

The narrator LLM writes claim text that names facts by slot, for example
``"You played {played}, but {capture:best}."``. Code builds the slot catalogue
from the engine and board facts (``facts.py``, ``board_lines.py``) and replaces
each slot with its value. Slot values are the only chess tokens the rendered
text holds: SAN copied from the facts, piece-on-square phrases, signed pawn
units and the other fact clauses below.

Grounding a slot claim (``validator.py``) has two fixed checks:

1. every slot names a fact listed for the claim's evidence item, and
2. the prose outside the slots holds no chess content (``CHESS_PROSE``).

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
from dataclasses import dataclass, field

import chess

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

# The fixed free-prose rule set (see the module docstring). Any match in the
# prose outside the slots rejects the claim as ``chess_token_outside_slot``.
CHESS_PROSE = re.compile(
    "|".join(
        (
            # Squares, SAN, UCI, move numbers, evaluations and mate counts all
            # contain a digit; so do "8.5 pawns" and "+150 cp".
            r"\d",
            # Mate and check suffixes, castling notation, figurine notation.
            r"[#♔-♟]|\b[O0]-[O0]",
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


@dataclass(frozen=True)
class Slot:
    """One fact the narrator may name. ``value`` is code-rendered.

    ``moves`` are the UCI moves the value states, in line order; ``phrase``
    marks a value that starts with a word (capitalised at a sentence start),
    as opposed to SAN, which keeps its case.
    """

    name: str
    value: str
    meaning: str
    moves: tuple[str, ...] = ()
    phrase: bool = False


@dataclass(frozen=True)
class SlotCatalogue:
    """Slots per evidence item: a claim may use only its own item's slots.

    ``combined`` maps an evidence item whose slots are exactly those of other
    items (such as the Analysis Board ``comparison``) to those items; the
    prompt names them instead of listing every slot again.
    """

    by_evidence: Mapping[str, Mapping[str, Slot]]
    shared: frozenset[str] = field(default_factory=frozenset)
    combined: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def slots_for(self, evidence_id: str) -> Mapping[str, Slot]:
        return self.by_evidence.get(evidence_id, {})

    def names(self) -> frozenset[str]:
        return frozenset(name for slots in self.by_evidence.values() for name in slots)

    def prompt(self) -> str:
        """The slot list the narrator receives as its last user message."""
        lines = [
            "Slots. Write each chess fact as one of these slots, in braces. Code puts the "
            "value after = in its place. A claim may use only the slots listed for the "
            "evidence item it cites."
        ]
        items = list(self.by_evidence.items())
        if not items:
            return lines[0]
        first = items[0][1]
        shared = [first[name] for name in first if name in self.shared]
        if shared and len(items) > 1:
            lines.append("For every evidence item:")
            lines += [_prompt_line(slot) for slot in shared]
        for evidence_id, slots in items:
            if evidence_id in self.combined:
                parts = ", ".join(self.combined[evidence_id])
                lines.append(f"For evidence {evidence_id}: every slot listed for {parts}.")
                continue
            lines.append(f"For evidence {evidence_id}:")
            lines += [
                _prompt_line(slot)
                for name, slot in slots.items()
                if len(items) == 1 or name not in self.shared
            ]
        return "\n".join(lines)


def _prompt_line(slot: Slot) -> str:
    meaning = f" ({slot.meaning})" if slot.meaning else ""
    return f"{{{slot.name}}} = {slot.value}{meaning}"


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
    mover_words: Mapping[str, str],
) -> list[Slot]:
    """``{line:L}``, ``{move:L:N}`` and the clause slots of every ply of one line."""
    full = tuple(uci)
    slots = [Slot(f"line:{line_id}", numbered_san(board, full), f"engine line {line_id}", full)]
    for move in moves:
        prefix = full[: move.ply]
        who = mover_words.get(move.mover, move.mover)
        slots.append(
            Slot(
                f"move:{line_id}:{move.ply}",
                move.san,
                f"move {move.ply} of {line_id}, {who}",
                prefix,
            )
        )
        slots += move_event_slots(f"{line_id}:{move.ply}", move, prefix)
    return slots


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
        shared.append(Slot("best", best.san, "the engine's best move", (best.uci,)))
        shared += move_event_slots("best", best, (best.uci,))
    shared.append(Slot("mistake", position.mistake_class.name, "the mistake class", phrase=True))
    for name, evaluation, meaning in (
        ("eval_before", position.evaluation_before, "evaluation before your move, your view"),
        ("eval_after", position.evaluation_after, "evaluation after your move, your view"),
    ):
        value = evaluation_value(evaluation, side=LEARNER, other=OPPONENT)
        if value is not None:
            shared.append(Slot(name, value, meaning, phrase=value.startswith("mate")))
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
    for item in facts.evidence:
        slots = {slot.name: slot for slot in shared}
        for line_id in item.line_ids:
            slots.update((slot.name, slot) for slot in by_line[line_id])
        slots.update((slot.name, slot) for slot in _evidence_slots(item))
        by_evidence[item.evidence_id] = slots
    return SlotCatalogue(by_evidence=by_evidence, shared=frozenset(slot.name for slot in shared))


__all__ = [
    "CHESS_PROSE",
    "LEARNER",
    "OPPONENT",
    "SLOT_TOKEN",
    "TACTIC_NAMES",
    "Slot",
    "SlotCatalogue",
    "evaluation_value",
    "lesson_slot_catalogue",
    "line_slots",
    "move_event_slots",
    "numbered_san",
    "pawn_units",
    "piece_phrase",
    "prose_outside_slots",
    "render",
    "slot_names",
]

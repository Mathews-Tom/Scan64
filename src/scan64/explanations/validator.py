"""Grounding contract for provider-worded explanations.

Every claim must cite known evidence, stay within the requested certainty and
hint level, and cite only a legal prefix of a verified line. Claim text is
checked on one of two paths:

- Slot path (#386, ``context.slots`` set): every slot must name a fact listed
  for the cited evidence item, and the prose outside the slots must hold no
  chess notation and no chess word the position's facts do not contain
  (``slots.chess_words_outside_slots``, #408). The returned claims hold the rendered
  text. The claim grammar below is not used.
- Grammar path (#366, ``context.slots`` unset): claim text is read with a small
  explicit grammar (see ``_validate_claim_text``); any chess token the grammar
  cannot place rejects the generation rather than passing unchecked.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

import chess
from chess_lesson_spec import Explanation, ExplanationClaim, LessonSpec

from scan64.explanations.facts import (
    PIECE_LIST_FIELDS,
    PIECE_SQUARE_FIELDS,
    SQUARE_FIELDS,
    SQUARE_LIST_FIELDS,
    TACTIC_FIELDS,
    NarratorFactsError,
    StoredEvaluations,
    parse_played_move,
)
from scan64.explanations.slots import (
    SLOT_TOKEN,
    SlotCatalogue,
    chess_words_outside_slots,
    prose_outside_slots,
    render,
)
from scan64.learning.evidence.composer import PIECE_VALUES, line_material_gain
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm.adapters import LLMExplanationProvider
from scan64.providers.llm.contracts import ExplanationRequest, GeneratedExplanation

RejectionReason = Literal[
    "invalid_hint_level",
    "invalid_fen",
    "invalid_evidence",
    "duplicate_evidence",
    "empty_evidence_ref",
    "unknown_evidence",
    "certainty_exceeded",
    "disclosure_exceeded",
    "malformed_line_move",
    "illegal_line_move",
    "unverified_line",
    "illegal_move",
    "move_not_verified",
    "piece_not_on_square",
    "piece_not_present",
    "square_not_in_evidence",
    "evaluation_not_supported",
    "mate_not_supported",
    "check_not_supported",
    "material_not_supported",
    "move_type_not_supported",
    "tactic_not_supported",
    "threat_not_supported",
    "wrong_side_to_move",
    "unparsed_chess_token",
    "missing_line",
    # Slot path (#386).
    "unknown_slot",
    "slot_not_in_evidence",
    "chess_token_outside_slot",
]
REJECTION_REASONS: tuple[RejectionReason, ...] = get_args(RejectionReason)

# An evaluation stated in claim text must be within this many centipawns of an
# engine score or swing recorded in the referenced evidence. Magnitudes are
# compared: engine scores are side-to-move relative, prose perspective is not.
EVALUATION_TOLERANCE_CP = 25


class GroundedExplanationValidationError(ValueError):
    """Raised when provider output exceeds verified explanation evidence."""

    def __init__(self, message: str, *, reason: RejectionReason) -> None:
        super().__init__(message)
        self.reason: RejectionReason = reason


@dataclass(frozen=True)
class GroundedExplanationContext:
    """Verified facts and disclosure policy supplied to an explanation provider.

    ``fen`` is the position before the learner's move: its side to move is the
    learner ("your" in claim text). With ``slots`` set, claim text is checked
    and rendered on the slot path (module docstring).
    """

    fen: str
    evidence: tuple[Evidence, ...]
    verified_lines: Mapping[str, tuple[tuple[str, ...], ...]]
    requested_hint_level: int
    maximum_certainty: Literal["observed", "likely", "certain"] = "observed"
    slots: SlotCatalogue | None = None


_CERTAINTY_RANK = {"observed": 0, "likely": 1, "certain": 2}


def validate_generated_explanation(
    generated: GeneratedExplanation,
    context: GroundedExplanationContext,
) -> Explanation:
    """Validate provider claims before constructing a client-visible explanation."""

    if context.requested_hint_level < 1:
        raise GroundedExplanationValidationError(
            "Requested hint level must be at least one", reason="invalid_hint_level"
        )
    try:
        board = chess.Board(context.fen)
    except ValueError as error:
        raise GroundedExplanationValidationError(
            "Grounding context contains an invalid FEN", reason="invalid_fen"
        ) from error

    evidence_by_id = _evidence_by_id(context.evidence)
    claims: list[ExplanationClaim] = []
    for claim in generated.claims:
        _validate_claim_contract(claim, board, context, evidence_by_id)
        if context.slots is None:
            _validate_claim_text(
                claim.text,
                _ClaimEvidence.build(
                    board,
                    evidence_by_id[claim.evidence_ref],
                    context.verified_lines.get(claim.evidence_ref, ()),
                ),
            )
            claims.append(claim)
        else:
            text = render_slot_text(claim.text, context.slots, claim.evidence_ref)
            claims.append(claim.model_copy(update={"text": text}))

    return Explanation(text=" ".join(claim.text for claim in claims), claims=claims)


def render_slot_text(text: str, catalogue: SlotCatalogue, evidence_id: str) -> str:
    """Check one slot claim's text (slot path) and return it with every slot filled."""
    slots = catalogue.slots_for(evidence_id)
    for match in SLOT_TOKEN.finditer(text):
        name = match.group("name")
        if name in slots:
            continue
        if name in catalogue.names():
            raise _reject(
                "slot_not_in_evidence", f"uses a slot of another evidence item: {match[0]!r}"
            )
        raise _reject("unknown_slot", f"uses an unknown slot: {match[0]!r}")
    prose = prose_outside_slots(text.replace("’", "'").replace("‘", "'"))
    if "{" in prose or "}" in prose:
        raise _reject("unknown_slot", f"contains a malformed slot: {text!r}")
    residue = chess_words_outside_slots(prose, catalogue.vocabulary(evidence_id))
    if residue is not None:
        raise _reject(
            "chess_token_outside_slot",
            f"states chess content outside a slot: {residue!r}",
        )
    return render(text, slots)


async def attach_validated_explanation(
    lesson: LessonSpec,
    provider: LLMExplanationProvider,
    request: ExplanationRequest,
    context: GroundedExplanationContext,
) -> None:
    """Attach provider output only after the full grounded-claim contract passes."""

    if lesson.source.fen != context.fen:
        raise GroundedExplanationValidationError(
            "Lesson source FEN and explanation grounding context must match",
            reason="invalid_fen",
        )
    generated = await provider.generate(request)
    lesson.explanation = validate_generated_explanation(generated, context)


def _evidence_by_id(evidence: tuple[Evidence, ...]) -> dict[str, Evidence]:
    by_id = {item.evidence_id: item for item in evidence}
    if len(by_id) != len(evidence):
        raise GroundedExplanationValidationError(
            "Grounding context contains duplicate evidence IDs", reason="duplicate_evidence"
        )
    return by_id


def _validate_claim_contract(
    claim: ExplanationClaim,
    board: chess.Board,
    context: GroundedExplanationContext,
    evidence_by_id: Mapping[str, Evidence],
) -> None:
    if not claim.evidence_ref.strip():
        raise GroundedExplanationValidationError(
            "Explanation claims require a non-empty evidence_ref", reason="empty_evidence_ref"
        )
    evidence = evidence_by_id.get(claim.evidence_ref)
    if evidence is None:
        raise GroundedExplanationValidationError(
            f"Explanation claim references unknown evidence {claim.evidence_ref!r}",
            reason="unknown_evidence",
        )
    if _CERTAINTY_RANK[claim.certainty] > _CERTAINTY_RANK[context.maximum_certainty]:
        raise GroundedExplanationValidationError(
            "Explanation claim exceeds supported certainty", reason="certainty_exceeded"
        )
    if claim.disclosure_level > context.requested_hint_level:
        raise GroundedExplanationValidationError(
            "Explanation claim exceeds the requested hint level", reason="disclosure_exceeded"
        )
    _validate_line(claim.line, board, context.verified_lines.get(claim.evidence_ref, ()))


def _validate_line(
    line: tuple[str, ...],
    board: chess.Board,
    verified_lines: tuple[tuple[str, ...], ...],
) -> None:
    if not line:
        return
    candidate = board.copy(stack=False)
    for move_uci in line:
        try:
            move = chess.Move.from_uci(move_uci)
        except ValueError as error:
            raise GroundedExplanationValidationError(
                f"Explanation claim contains malformed UCI move {move_uci!r}",
                reason="malformed_line_move",
            ) from error
        if move not in candidate.legal_moves:
            raise GroundedExplanationValidationError(
                f"Explanation claim contains illegal move {move_uci!r}",
                reason="illegal_line_move",
            )
        candidate.push(move)
    if not any(_is_prefix(line, verified_line) for verified_line in verified_lines):
        raise GroundedExplanationValidationError(
            "Explanation claim line does not match a verified variation",
            reason="unverified_line",
        )


def _is_prefix(line: tuple[str, ...], verified_line: tuple[str, ...]) -> bool:
    return len(line) <= len(verified_line) and verified_line[: len(line)] == line


# --- Claim-text evidence ---------------------------------------------------

# Payload line fields replayed from the position before the learner's move
# (the context FEN) or after it (payload ``after_fen``); see composer.py.
_BEFORE_LINE_FIELDS = ("fast_line",)
_AFTER_LINE_FIELDS = ("proving_line", "mating_line", "focused_line")
_BEFORE_MOVE_FIELDS = ("best_move", "pinning_move", "played_move")
_AFTER_MOVE_FIELDS = ("threat_move", "incoming_threat")
# ``board_multipv``: Analysis Board engine lines (``explanations/board_lines.py``).
_BEFORE_MULTIPV_FIELDS = ("mate_source_multipv", "board_multipv")
_AFTER_MULTIPV_FIELDS = ("focused_multipv",)
_CENTIPAWN_FIELDS = ("swing_cp", "before_score_cp")
# Best-line score minus each compared line's score (Analysis Board comparison).
_CENTIPAWN_LIST_FIELDS = ("best_differences_cp",)
_MATE_FIELDS = ("mate_in", "before_score_mate", "after_score_mate", "source_score_mate")
_GAIN_FIELDS = ("material_gain", "exchange_gain")
_GAIN_FLAG_FIELDS = ("wins_material", "results_in_material_gain", "opponent_executed_threat")
_VICTIM_FIELDS = ("captured_piece", "hanging_piece", "threatened_piece")


@dataclass(frozen=True)
class _MoveFact:
    board: chess.Board
    move: chess.Move
    piece: chess.Piece
    victim: chess.Piece | None
    gives_check: bool
    gives_mate: bool


@dataclass
class _ClaimEvidence:
    """Every chess fact a claim citing one evidence item may state."""

    learner: chess.Color
    moves: list[_MoveFact] = field(default_factory=list)
    boards: list[chess.Board] = field(default_factory=list)
    pieces: set[tuple[chess.Square, chess.Piece]] = field(default_factory=set)
    squares: set[chess.Square] = field(default_factory=set)
    centipawns: set[int] = field(default_factory=set)
    mates: set[int] = field(default_factory=set)
    gains: set[int] = field(default_factory=set)
    gain_flag: bool = False
    victims: list[chess.Piece] = field(default_factory=list)
    tactics: set[str] = field(default_factory=set)

    @classmethod
    def build(
        cls,
        board: chess.Board,
        evidence: Evidence,
        verified_lines: Iterable[tuple[str, ...]],
    ) -> _ClaimEvidence:
        payload: Mapping[str, Any] = evidence.payload
        facts = cls(learner=board.turn, boards=[board])
        after = _payload_board(payload)
        lines: list[tuple[chess.Board, list[str]]] = [
            (board, list(line)) for line in verified_lines
        ]
        lines += [(board, line) for line in _payload_lines(payload, _BEFORE_LINE_FIELDS)]
        lines += [(board, line) for line in _payload_multipv(payload, _BEFORE_MULTIPV_FIELDS)]
        lines += [(board, [move]) for move in _payload_moves(payload, _BEFORE_MOVE_FIELDS)]
        if after is not None:
            facts.boards.append(after)
            lines += [(after, line) for line in _payload_lines(payload, _AFTER_LINE_FIELDS)]
            lines += [(after, line) for line in _payload_multipv(payload, _AFTER_MULTIPV_FIELDS)]
            lines += [(after, [move]) for move in _payload_moves(payload, _AFTER_MOVE_FIELDS)]
        for start, line in lines:
            facts._add_line(start, line)
        facts._add_payload(payload)
        facts._add_stored_evaluations(board, payload)
        return facts

    def _add_stored_evaluations(self, board: chess.Board, payload: Mapping[str, Any]) -> None:
        """Evaluations the narrator facts state (``explanations/facts.py``) may be quoted."""
        try:
            played: chess.Move | None = parse_played_move(board, payload.get("played_move"))
        except NarratorFactsError:
            played = None
        centipawns, mates = StoredEvaluations.from_payload(board, payload, played).numbers()
        self.centipawns.update(centipawns)
        self.mates.update(mates)

    def _add_line(self, start: chess.Board, line: list[str]) -> None:
        replay = start.copy(stack=False)
        played: list[str] = []
        for token in line:
            move = _parse_move(replay, token)
            if move is None:
                break
            piece = replay.piece_at(move.from_square)
            if piece is None:
                break
            if replay.is_en_passant(move):
                victim: chess.Piece | None = chess.Piece(chess.PAWN, not replay.turn)
            elif replay.is_capture(move):
                victim = replay.piece_at(move.to_square)
            else:
                victim = None
            gives_check = replay.gives_check(move)
            before = replay.copy(stack=False)
            replay.push(move)
            played.append(move.uci())
            self.moves.append(
                _MoveFact(before, move, piece, victim, gives_check, replay.is_checkmate())
            )
            self.boards.append(replay.copy(stack=False))
            self.squares.update((move.from_square, move.to_square))
            if victim is not None:
                self.victims.append(victim)
            if replay.is_checkmate() and replay.turn != start.turn:
                self.mates.add(len(played) // 2 + 1)
        gain = line_material_gain(start, played)
        if gain is not None:
            self.gains.add(abs(gain))

    def _add_payload(self, payload: Mapping[str, Any]) -> None:
        for square_field, piece_field in PIECE_SQUARE_FIELDS:
            self._add_piece(payload.get(square_field), payload.get(piece_field))
        for list_field in PIECE_LIST_FIELDS:
            entries = payload.get(list_field)
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, Mapping):
                        self._add_piece(entry.get("square"), entry.get("piece"))
        for square_field in SQUARE_FIELDS:
            self._add_square(payload.get(square_field))
        for list_field in SQUARE_LIST_FIELDS:
            entries = payload.get(list_field)
            if isinstance(entries, list):
                for entry in entries:
                    self._add_square(entry)
        for cp_field in _CENTIPAWN_FIELDS:
            _add_int(self.centipawns, payload.get(cp_field))
        for cp_list_field in _CENTIPAWN_LIST_FIELDS:
            values = payload.get(cp_list_field)
            if isinstance(values, list):
                for value in values:
                    _add_int(self.centipawns, value)
        for mate_field in _MATE_FIELDS:
            _add_int(self.mates, payload.get(mate_field))
        for multipv_field in (*_BEFORE_MULTIPV_FIELDS, *_AFTER_MULTIPV_FIELDS):
            entries = payload.get(multipv_field)
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, Mapping):
                        _add_int(self.centipawns, entry.get("score_cp"))
                        _add_int(self.mates, entry.get("score_mate"))
        for gain_field in _GAIN_FIELDS:
            _add_int(self.gains, payload.get(gain_field))
        self.gain_flag = any(payload.get(flag) is True for flag in _GAIN_FLAG_FIELDS)
        for victim_field in _VICTIM_FIELDS:
            victim = _piece_from_symbol(payload.get(victim_field))
            if victim is not None:
                self.victims.append(victim)
        for tactic_field in TACTIC_FIELDS:
            value = payload.get(tactic_field)
            if isinstance(value, str):
                self.tactics.add(value)

    def _add_piece(self, square_name: object, symbol: object) -> None:
        square = _square_from_name(square_name)
        piece = _piece_from_symbol(symbol)
        if square is None:
            return
        self.squares.add(square)
        if piece is not None:
            self.pieces.add((square, piece))

    def _add_square(self, square_name: object) -> None:
        square = _square_from_name(square_name)
        if square is not None:
            self.squares.add(square)

    @property
    def has_material_gain(self) -> bool:
        return self.gain_flag or any(gain >= 1 for gain in self.gains)


def _payload_board(payload: Mapping[str, Any]) -> chess.Board | None:
    fen = payload.get("after_fen")
    if fen is None:
        return None
    if not isinstance(fen, str):
        raise GroundedExplanationValidationError(
            "Evidence after_fen is not a FEN string", reason="invalid_evidence"
        )
    try:
        return chess.Board(fen)
    except ValueError as error:
        raise GroundedExplanationValidationError(
            "Evidence contains an invalid after_fen", reason="invalid_evidence"
        ) from error


def _payload_lines(payload: Mapping[str, Any], fields: Iterable[str]) -> list[list[str]]:
    lines = []
    for name in fields:
        line = payload.get(name)
        if isinstance(line, list) and all(isinstance(move, str) for move in line):
            lines.append(list(line))
    return lines


def _payload_multipv(payload: Mapping[str, Any], fields: Iterable[str]) -> list[list[str]]:
    lines = []
    for name in fields:
        entries = payload.get(name)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            pv = entry.get("pv") if isinstance(entry, Mapping) else None
            if isinstance(pv, list) and all(isinstance(move, str) for move in pv):
                lines.append(list(pv))
    return lines


def _payload_moves(payload: Mapping[str, Any], fields: Iterable[str]) -> list[str]:
    return [move for name in fields if isinstance(move := payload.get(name), str) and move]


def _parse_move(board: chess.Board, token: str) -> chess.Move | None:
    """Read one payload move: UCI, or SAN for ``played_move``. Illegal moves prove nothing."""
    try:
        move = chess.Move.from_uci(token)
    except ValueError:
        try:
            return board.parse_san(token)
        except ValueError:
            return None
    return move if move in board.legal_moves else None


def _add_int(target: set[int], value: object) -> None:
    if isinstance(value, int) and not isinstance(value, bool):
        target.add(abs(value))


def _square_from_name(name: object) -> chess.Square | None:
    if not isinstance(name, str) or name not in chess.SQUARE_NAMES:
        return None
    return chess.parse_square(name)


def _piece_from_symbol(symbol: object) -> chess.Piece | None:
    if not isinstance(symbol, str) or len(symbol) != 1 or symbol not in "pnbrqkPNBRQK":
        return None
    return chess.Piece.from_symbol(symbol)


# --- Claim-text grammar ----------------------------------------------------
#
# Rules run in order over the text; each match is checked, then blanked so a
# later rule never re-reads it. Whatever chess-looking token survives every
# rule rejects the claim as unparsed.

_PIECE_TYPES = {
    "king": chess.KING,
    "queen": chess.QUEEN,
    "rook": chess.ROOK,
    "bishop": chess.BISHOP,
    "knight": chess.KNIGHT,
    "pawn": chess.PAWN,
}
_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
}
# Squares stay lowercase inside case-insensitive rules: "G5" must reach the
# unparsed-token rejection, not chess.parse_square (which raises ValueError).
_SQ = r"(?-i:[a-h][1-8])"
_PIECE = r"(?P<piece>king|queen|rook|bishop|knight|pawn)s?"
# "the learner" and "the side to move" are the names NARRATOR_SYSTEM_PROMPT and
# BOARD_NARRATOR_SYSTEM_PROMPT ask for; both mean the side to move in ``fen``.
_COLOUR = (
    r"(?:(?P<colour>your\s+opponent's|the\s+opponent's|opponent's|white's|black's"
    r"|the\s+learner's|learner's|the\s+side\s+to\s+move's"
    r"|white|black|your)\s+)?"
)
_LEARNER_WORDS = frozenset(
    {
        "you",
        "your",
        "the learner",
        "the learner's",
        "learner's",
        "the side to move",
        "the side to move's",
    }
)
_COUNT = r"(?:(?P<count>the|a|an|one|two|three|four|five|six|seven|eight|\d+)\s+)?"
_VERB_OBJECT_WORDS = (
    "the|for|whether|your|each|all|that|every|which|if|its|their|what|how|before|first"
    "|carefully|again|captures|threats|this|these|those|any"
)
_TACTIC_WORDS: dict[str, frozenset[str]] = {
    "fork": frozenset({"knight_fork"}),
    "pin": frozenset({"pin"}),
    "skewer": frozenset({"skewer"}),
    "discovered": frozenset({"discovered_attack"}),
    "back rank": frozenset({"back_rank"}),
    "overload": frozenset({"overloaded_defender"}),
}

_Handler = Callable[[re.Match[str], _ClaimEvidence], None]


def _reject(reason: RejectionReason, message: str) -> GroundedExplanationValidationError:
    return GroundedExplanationValidationError(f"Explanation claim text {message}", reason=reason)


def _colour(match: re.Match[str], facts: _ClaimEvidence) -> chess.Color | None:
    word = match.groupdict().get("colour")
    if word is None:
        return None
    word = " ".join(word.lower().split())
    if word in ("white", "white's"):
        return chess.WHITE
    if word in ("black", "black's"):
        return chess.BLACK
    if word in _LEARNER_WORDS:
        return facts.learner
    return not facts.learner


def _piece_matches(piece: chess.Piece, piece_type: int, colour: chess.Color | None) -> bool:
    return piece.piece_type == piece_type and (colour is None or piece.color == colour)


def _check_piece(
    facts: _ClaimEvidence,
    piece_type: int,
    colour: chess.Color | None,
    square: chess.Square | None,
    text: str,
) -> None:
    if square is None:
        present = any(
            _piece_matches(piece, piece_type, colour)
            for board in facts.boards
            for piece in board.piece_map().values()
        ) or any(_piece_matches(piece, piece_type, colour) for _, piece in facts.pieces)
        if not present:
            raise _reject("piece_not_present", f"names a piece absent from evidence: {text!r}")
        return
    on_square = any(
        (piece := board.piece_at(square)) is not None and _piece_matches(piece, piece_type, colour)
        for board in facts.boards
    ) or any(
        payload_square == square and _piece_matches(piece, piece_type, colour)
        for payload_square, piece in facts.pieces
    )
    if not on_square:
        raise _reject("piece_not_on_square", f"places a piece on the wrong square: {text!r}")


def _side_to_move(match: re.Match[str], facts: _ClaimEvidence) -> None:
    side = match.group("side") or match.group("side2")
    colour = chess.WHITE if side.lower() == "white" else chess.BLACK
    if colour != facts.boards[0].turn:
        raise _reject("wrong_side_to_move", f"states the wrong side to move: {match.group(0)!r}")


def _legal_anywhere(facts: _ClaimEvidence, predicate: Callable[[chess.Board], bool]) -> bool:
    return any(predicate(board) for board in facts.boards)


def _uci_facts(match: re.Match[str], facts: _ClaimEvidence) -> list[_MoveFact]:
    promotion = (match.group("promotion") or "").lower()
    uci = f"{match.group('origin')}{match.group('target')}{promotion}"
    matched = [fact for fact in facts.moves if fact.move.uci() == uci]
    if matched:
        return matched
    try:
        move = chess.Move.from_uci(uci)
    except ValueError as error:  # e.g. "e4e4": python-chess rejects it as a move
        raise _reject("illegal_move", f"names an illegal move: {uci!r}") from error
    if _legal_anywhere(facts, lambda board: move in board.legal_moves):
        raise _reject("move_not_verified", f"names a move outside verified lines: {uci!r}")
    raise _reject("illegal_move", f"names an illegal move: {uci!r}")


def _parse_san(board: chess.Board, san: str) -> chess.Move | None:
    try:
        return board.parse_san(san)
    except ValueError:
        return None


def _san_facts(match: re.Match[str], facts: _ClaimEvidence) -> list[_MoveFact]:
    san = match.group("san")
    suffix = match.group("suffix")
    matched = [fact for fact in facts.moves if _parse_san(fact.board, san) == fact.move]
    if not matched:
        if _legal_anywhere(facts, lambda board: _parse_san(board, san) is not None):
            raise _reject("move_not_verified", f"names a move outside verified lines: {san!r}")
        raise _reject("illegal_move", f"names an illegal move: {san!r}")
    if suffix == "#":
        matched = [fact for fact in matched if fact.gives_mate]
    elif suffix == "+":
        matched = [fact for fact in matched if fact.gives_check]
    if not matched:
        raise _reject("check_not_supported", f"marks a move as check wrongly: {match[0]!r}")
    return matched


def _uci_move(match: re.Match[str], facts: _ClaimEvidence) -> None:
    _uci_facts(match, facts)


def _san_move(match: re.Match[str], facts: _ClaimEvidence) -> None:
    _san_facts(match, facts)


def _side_move(match: re.Match[str], facts: _ClaimEvidence) -> None:
    """``White plays e2e4`` / ``Black replies Qh4#``: the named side must make that move."""
    colour = chess.WHITE if match.group("side").lower() == "white" else chess.BLACK
    matched = _uci_facts(match, facts) if match.group("origin") else _san_facts(match, facts)
    if not any(fact.board.turn == colour for fact in matched):
        raise _reject("wrong_side_to_move", f"gives a move to the wrong side: {match[0]!r}")


def _mate_in(match: re.Match[str], facts: _ClaimEvidence) -> None:
    raw = (match.group("mate") or match.group("mate2") or match.group("mate3")).lower()
    count = _NUMBER_WORDS[raw] if raw in _NUMBER_WORDS else int(raw)
    if count not in facts.mates:
        raise _reject("mate_not_supported", f"states an unproved mate: {match[0]!r}")


def _evaluation(match: re.Match[str], facts: _ClaimEvidence) -> None:
    raw = match.group("value") or match.group("signed")
    value = float(raw.replace("−", "-").replace("–", "-"))
    unit = (match.group("unit") or "pawns").lower()
    centipawns = abs(round(value if unit in ("cp", "centipawn", "centipawns") else value * 100))
    if not any(abs(centipawns - known) <= EVALUATION_TOLERANCE_CP for known in facts.centipawns):
        raise _reject("evaluation_not_supported", f"states an unrecorded evaluation: {match[0]!r}")


def _material(match: re.Match[str], facts: _ClaimEvidence) -> None:
    obj = match.group("object").lower()
    raw_count = (match.group("count") or "the").lower()
    if obj == "material":
        if not facts.has_material_gain:
            raise _reject("material_not_supported", f"states an unproved gain: {match[0]!r}")
        return
    if obj == "exchange":
        if 2 not in facts.gains:
            raise _reject("material_not_supported", f"states an unproved exchange: {match[0]!r}")
        return
    piece_type = _PIECE_TYPES[obj.removesuffix("s")]
    colour = _colour(match, facts)
    square = match.group("square")
    if square is not None:
        _check_piece(facts, piece_type, colour, chess.parse_square(square), match[0])
    if piece_type == chess.PAWN:
        count = 1 if raw_count == "the" else _NUMBER_WORDS.get(raw_count) or int(raw_count)
        if count not in facts.gains:
            raise _reject("material_not_supported", f"states an unproved gain: {match[0]!r}")
        return
    count = 1 if raw_count == "the" else _NUMBER_WORDS.get(raw_count) or int(raw_count)
    won = sum(_piece_matches(victim, piece_type, colour) for victim in facts.victims)
    if won < count or not facts.has_material_gain:
        raise _reject("material_not_supported", f"states an unproved win: {match[0]!r}")


def _undefended(board: chess.Board, square: chess.Square, piece: chess.Piece) -> bool:
    return board.is_attacked_by(not piece.color, square) and not board.is_attacked_by(
        piece.color, square
    )


def _hanging(match: re.Match[str], facts: _ClaimEvidence) -> None:
    """A loss proved by a line, or a non-king piece attacked and undefended on a fact board."""
    if facts.has_material_gain or any(
        piece.piece_type != chess.KING and _undefended(board, square, piece)
        for board in facts.boards
        for square, piece in board.piece_map().items()
    ):
        return
    raise _reject("material_not_supported", f"states an unproved loss: {match[0]!r}")


_MATERIAL_UNITS = {
    "pawn": 1,
    "point": 1,
    "exchange": 2,
    **{chess.piece_name(piece_type): value for piece_type, value in PIECE_VALUES.items()},
}


def _side_colour(word: str, facts: _ClaimEvidence) -> chess.Color:
    word = " ".join(word.lower().split())
    if word == "white":
        return chess.WHITE
    if word == "black":
        return chess.BLACK
    return facts.learner if word in _LEARNER_WORDS else not facts.learner


def _advantage(board: chess.Board, colour: chess.Color) -> int:
    totals = {chess.WHITE: 0, chess.BLACK: 0}
    for piece in board.piece_map().values():
        totals[piece.color] += PIECE_VALUES.get(piece.piece_type, 0)
    return totals[colour] - totals[not colour]


def _material_balance(match: re.Match[str], facts: _ClaimEvidence) -> None:
    """``Black is up a knight`` / ``material is equal``: some fact board has that balance."""
    if match.group("equal") is not None:
        if not any(_advantage(board, chess.WHITE) == 0 for board in facts.boards):
            raise _reject("material_not_supported", f"states an unproved balance: {match[0]!r}")
        return
    colour = _side_colour(match.group("who"), facts)
    sign = 1 if match.group("direction").lower() in ("up", "ahead") else -1
    unit = match.group("unit")
    advantages = [_advantage(board, colour) for board in facts.boards]
    if unit is None:
        supported = any(sign * advantage > 0 for advantage in advantages)
    else:
        raw_count = (match.group("count") or "a").lower()
        count = int(raw_count) if raw_count.isdigit() else _NUMBER_WORDS.get(raw_count, 1)
        expected = sign * count * _MATERIAL_UNITS[unit.lower().removesuffix("s")]
        supported = expected in advantages
    if not supported:
        raise _reject("material_not_supported", f"states an unproved balance: {match[0]!r}")


def _attack(match: re.Match[str], facts: _ClaimEvidence) -> None:
    """``attacks the queen on g5``: a fact board has that piece attacked (and undefended)."""
    piece_type = _PIECE_TYPES[match.group("piece").lower()]
    colour = _colour(match, facts)
    square_name = match.group("square")
    target = chess.parse_square(square_name) if square_name is not None else None
    undefended = match.group("undefended") is not None
    for board in facts.boards:
        for square, piece in board.piece_map().items():
            if not _piece_matches(piece, piece_type, colour) or target not in (None, square):
                continue
            if undefended:
                if _undefended(board, square, piece):
                    return
            elif board.is_attacked_by(not piece.color, square):
                return
    raise _reject("threat_not_supported", f"states an unproved attack: {match[0]!r}")


def _piece_to(match: re.Match[str], facts: _ClaimEvidence) -> None:
    piece_type = _PIECE_TYPES[match.group("piece").lower()]
    colour = _colour(match, facts)
    target = chess.parse_square(match.group("target"))
    origin = match.group("origin")
    origin_square = chess.parse_square(origin) if origin is not None else None
    if not any(
        _piece_matches(fact.piece, piece_type, colour)
        and fact.move.to_square == target
        and (origin_square is None or fact.move.from_square == origin_square)
        for fact in facts.moves
    ):
        raise _reject("move_not_verified", f"describes a move outside evidence: {match[0]!r}")


def _square_piece(match: re.Match[str], facts: _ClaimEvidence) -> None:
    square = chess.parse_square(match.group("square"))
    _check_piece(facts, _PIECE_TYPES[match.group("piece").lower()], None, square, match[0])


def _piece(match: re.Match[str], facts: _ClaimEvidence) -> None:
    square = match.group("square")
    _check_piece(
        facts,
        _PIECE_TYPES[match.group("piece").lower()],
        _colour(match, facts),
        chess.parse_square(square) if square is not None else None,
        match[0],
    )


def _mate_word(match: re.Match[str], facts: _ClaimEvidence) -> None:
    if not facts.mates and not any(fact.gives_mate for fact in facts.moves):
        raise _reject("mate_not_supported", f"states an unproved mate: {match[0]!r}")


def _stalemate(match: re.Match[str], facts: _ClaimEvidence) -> None:
    if not any(board.is_stalemate() for board in facts.boards):
        raise _reject("move_type_not_supported", f"states an unproved stalemate: {match[0]!r}")


def _check_word(match: re.Match[str], facts: _ClaimEvidence) -> None:
    if not any(fact.gives_check for fact in facts.moves) and not any(
        board.is_check() for board in facts.boards
    ):
        raise _reject("check_not_supported", f"states an unproved check: {match[0]!r}")


def _move_kind(predicate: Callable[[_MoveFact], bool], label: str) -> _Handler:
    def handler(match: re.Match[str], facts: _ClaimEvidence) -> None:
        if not any(predicate(fact) for fact in facts.moves):
            raise _reject("move_type_not_supported", f"states an unproved {label}: {match[0]!r}")

    return handler


def _tactic(match: re.Match[str], facts: _ClaimEvidence) -> None:
    word = " ".join(match.group("tactic").lower().replace("-", " ").split())
    stem = next(key for key in _TACTIC_WORDS if word.startswith(key))
    if not _TACTIC_WORDS[stem] & facts.tactics:
        raise _reject("tactic_not_supported", f"names a tactic absent from evidence: {match[0]!r}")


def _square(match: re.Match[str], facts: _ClaimEvidence) -> None:
    if chess.parse_square(match[0]) not in facts.squares:
        raise _reject("square_not_in_evidence", f"names a square outside evidence: {match[0]!r}")


_I = re.IGNORECASE
# Move notation stays case-sensitive inside case-insensitive rules.
_UCI_TOKEN = (
    rf"(?-i:(?<![\w-])(?P<origin>{_SQ})[-x]?(?P<target>{_SQ})(?:=?(?P<promotion>[qrbnQRBN]))?"
    r"(?![\w-]))"
)
_SAN_TOKEN = (
    r"(?-i:(?<![\w-])(?P<san>O-O-O|O-O|0-0-0|0-0|[KQRBN][a-h]?[1-8]?x?[a-h][1-8]"
    r"|[a-h]x[a-h][1-8](?:=?[QRBN])?|[a-h][1-8]=?[QRBN]|[a-h][1-8](?=[+#]))"
    r"(?P<suffix>[+#])?(?![\w-]))"
)
_RULES: tuple[tuple[re.Pattern[str], _Handler], ...] = (
    (
        re.compile(
            r"\b(?:(?P<side>white|black)\s+(?:is\s+)?to\s+(?:move|play)"
            r"|it\s+is\s+(?P<side2>white|black)'s\s+(?:move|turn))\b",
            _I,
        ),
        _side_to_move,
    ),
    (
        re.compile(
            r"\b(?P<side>white|black)\s+(?:plays|played|replies\s+with|replies|answers\s+with"
            r"|answers|continues\s+with|responds\s+with|can\s+play)\s+"
            rf"(?:{_UCI_TOKEN}|{_SAN_TOKEN})",
            _I,
        ),
        _side_move,
    ),
    (re.compile(_UCI_TOKEN), _uci_move),
    (re.compile(_SAN_TOKEN), _san_move),
    (
        re.compile(
            r"(?:\bmates?[-\s]+in[-\s]+(?P<mate>\d+|one|two|three|four|five)\b"
            r"|(?<![\w#])#(?P<mate2>\d+)\b|\bM(?P<mate3>\d+)\b)",
            _I,
        ),
        _mate_in,
    ),
    (
        re.compile(
            r"\b(?:material\s+(?:balance\s+)?(?:is|was|stays|remains)\s+(?:now\s+|still\s+)?"
            r"(?P<equal>equal|even|level|balanced)"
            r"|(?P<who>white|black|you|your\s+opponent|the\s+opponent|the\s+learner"
            r"|the\s+side\s+to\s+move)\s+"
            r"(?:is|are|was|were|will\s+be|would\s+be|stays|remains)\s+(?:now\s+|still\s+)?"
            r"(?P<direction>up|down|ahead|behind)\s+(?:by\s+)?"
            r"(?:(?:(?P<count>a|an|the|one|two|three|four|five|six|seven|eight|\d+)\s+)?"
            r"(?P<unit>pawns?|points?|knight|bishop|rook|queen|exchange)"
            r"(?:\s+(?:of|in)\s+material)?|in\s+material))\b",
            _I,
        ),
        _material_balance,
    ),
    (
        re.compile(
            r"\b(?:attack(?:s|ed|ing)?|threaten(?:s|ed|ing)?)\s+(?:the\s+|a\s+|an\s+)?"
            r"(?P<undefended>undefended\s+)?"
            + _COLOUR
            + _PIECE
            + rf"(?:\s+on\s+(?P<square>{_SQ}))?\b",
            _I,
        ),
        _attack,
    ),
    (
        re.compile(
            r"\b(?:win|wins|winning|won|gain|gains|gaining|gained|lose|loses|losing|lost"
            r"|drop|drops|dropping|dropped|cost|costs|costing|hang|hangs|hanging|hung"
            r"|sacrifice|sacrifices|sacrificing|sacrificed)\s+"
            + _COUNT
            + _COLOUR
            + r"(?P<object>(?:king|queen|rook|bishop|knight|pawn)s?|exchange|material)"
            + rf"(?:\s+on\s+(?P<square>{_SQ}))?\b",
            _I,
        ),
        _material,
    ),
    (
        re.compile(
            r"(?<![\w.])(?P<value>[+\-−–]?\d+(?:\.\d+)?)\s*-?\s*"
            r"(?P<unit>pawns?|points?|centipawns?|cp)\b"
            # Any decimal is an evaluation, signed or not ("drops to 7.4"). A
            # sentence-ending full stop after the number is still an evaluation.
            r"|(?<![\w.])(?P<signed>[+\-−–]?\d+\.\d+)(?!\w|\.\d)",
            _I,
        ),
        _evaluation,
    ),
    (re.compile(r"\b(?:hangs|hanging|hung|en\s+prise|undefended)\b", _I), _hanging),
    (
        re.compile(
            _COLOUR
            + _PIECE
            + rf"\s+(?:from\s+(?P<origin>{_SQ})\s+)?(?:to|onto|into)\s+(?P<target>{_SQ})\b",
            _I,
        ),
        _piece_to,
    ),
    (re.compile(rf"\b(?P<square>{_SQ})[-\s]+{_PIECE}\b", _I), _square_piece),
    (
        re.compile(r"\b" + _COLOUR + _PIECE + rf"(?:\s+(?:on|from)\s+(?P<square>{_SQ}))?\b", _I),
        _piece,
    ),
    (re.compile(r"\bstalemate[sd]?\b", _I), _stalemate),
    (re.compile(r"\b(?:checkmat(?:e|es|ed|ing)|mat(?:e|es|ed|ing))\b", _I), _mate_word),
    (
        re.compile(rf"\b(?:check|checks|checking)\b(?!\s+(?:{_VERB_OBJECT_WORDS})\b)", _I),
        _check_word,
    ),
    (
        re.compile(r"\bcastl(?:e|es|ed|ing)\b", _I),
        _move_kind(lambda fact: fact.board.is_castling(fact.move), "castling"),
    ),
    (
        re.compile(r"\ben\s+passant\b", _I),
        _move_kind(lambda fact: fact.board.is_en_passant(fact.move), "en passant capture"),
    ),
    (
        re.compile(r"\bpromot(?:e|es|ed|ing|ion|ions)\b", _I),
        _move_kind(lambda fact: fact.move.promotion is not None, "promotion"),
    ),
    (
        re.compile(r"\b(?:captur(?:e|es|ed|ing)|takes|took|taking)\b", _I),
        _move_kind(lambda fact: fact.board.is_capture(fact.move), "capture"),
    ),
    (
        re.compile(
            r"\b(?P<tactic>fork(?:s|ed|ing)?|pin(?:s|ned|ning)?|skewer(?:s|ed|ing)?"
            r"|discovered|back[-\s]rank|overload(?:s|ed|ing)?)\b",
            _I,
        ),
        _tactic,
    ),
    (re.compile(rf"\b{_SQ}\b"), _square),
)

# Chess-looking residue no rule placed: a bare colour word, a square fused into
# a longer token, a malformed piece move, or castling notation.
_UNPARSED = re.compile(
    r"\b(?:white|black)\b|[a-hA-H][1-8]|\b[KQRBN]x?[a-z]\d+\b|O-O|0-0|\bcastl", _I
)


def _validate_claim_text(text: str, facts: _ClaimEvidence) -> None:
    """Check every chess fact in ``text`` against one evidence item and its boards."""
    remaining = text.replace("’", "'").replace("‘", "'")
    for pattern, handler in _RULES:
        for match in pattern.finditer(remaining):
            handler(match, facts)
        remaining = pattern.sub(lambda match: " " * len(match[0]), remaining)
    residue = _UNPARSED.search(remaining)
    if residue is not None:
        raise _reject(
            "unparsed_chess_token",
            f"contains a chess token outside the claim grammar: {residue[0]!r}",
        )

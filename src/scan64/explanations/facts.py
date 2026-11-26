"""Precomputed chess facts the narrator words (issue #367).

python-chess and persisted engine searches decide every fact here; the LLM
only words them. ``build_narrator_facts`` turns a diagnosis and its referenced
evidence into a :class:`NarratorFacts` document whose JSON Schema is stored at
``schemas/narrator-facts.schema.json`` (regenerate with
``uv run python -m scan64.explanations.facts``).

Coordinates: every verified line starts at the position before the learner's
move (the grounding FEN). A continuation found by a search of the position
after the learner's move is prefixed with the learner's move, so one replay
origin serves the prompt, ``ExplanationClaim.line`` and the validator.

Evaluations come only from engine searches already stored in the evidence
payload; no search runs here. A position no stored search scored is
``unknown``. Scores are from the learner's point of view.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import chess
from chess_lesson_spec import Diagnosis
from pydantic import BaseModel, ConfigDict, Field

from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.evidence.composer import PIECE_VALUES
from scan64.learning.evidence.models import Evidence

SCHEMA_VERSION: Literal["narrator-facts/1"] = "narrator-facts/1"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "narrator-facts.schema.json"

# Each verified line is cut deterministically after this many plies; the cut
# is marked with ``full_plies`` and the validator only accepts the cut line.
MAX_LINE_PLIES = 8

PieceName = Literal["pawn", "knight", "bishop", "rook", "queen", "king"]
ColourName = Literal["white", "black"]
EvaluationSource = Literal[
    "fast_pass", "reply_pass", "focused_pass", "mate_source_pass", "analysis_board_pass"
]
_SQUARE_PATTERN = r"^[a-h][1-8]$"
_UCI_PATTERN = r"^[a-h][1-8][a-h][1-8][qrbn]?$"


class NarratorFactsError(ValueError):
    """Evidence cannot be turned into board-checked facts (illegal or inconsistent)."""


class _Facts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CentipawnEvaluation(_Facts):
    status: Literal["centipawns"]
    centipawns: int = Field(description="Learner's point of view: positive favours the learner.")
    source: EvaluationSource


class MateEvaluation(_Facts):
    status: Literal["mate"]
    mate_in: int = Field(
        description="Full moves to mate. Positive: the learner mates. Negative: the learner is "
        "mated."
    )
    source: EvaluationSource


class UnknownEvaluation(_Facts):
    status: Literal["unknown"] = Field(
        description="No stored engine search scored this position. State no evaluation."
    )


Evaluation = Annotated[
    CentipawnEvaluation | MateEvaluation | UnknownEvaluation, Field(discriminator="status")
]
UNKNOWN = UnknownEvaluation(status="unknown")


class PieceOnSquare(_Facts):
    square: str = Field(pattern=_SQUARE_PATTERN)
    piece: PieceName
    colour: ColourName


class Material(_Facts):
    """Piece values pawn 1, knight 3, bishop 3, rook 5, queen 9; kings excluded."""

    white: int = Field(ge=0)
    black: int = Field(ge=0)
    learner_advantage: int = Field(description="Learner's material minus the opponent's.")


class MoveFacts(_Facts):
    ply: int = Field(ge=1, description="1 is the first move from the position before the mistake.")
    side: ColourName
    mover: Literal["learner", "opponent"]
    san: str
    uci: str = Field(pattern=_UCI_PATTERN)
    piece: PieceName
    captures: PieceOnSquare | None = Field(default=None, description="Absent: no capture.")
    check: bool = False
    checkmate: bool = False
    promotion: PieceName | None = None
    castling: Literal["kingside", "queenside"] | None = None
    material_after: Material
    new_threats: tuple[PieceOnSquare, ...] = Field(
        default=(),
        description="Opponent-of-mover pieces (not kings) attacked by the mover and not "
        "defended after this move that were not attacked-and-undefended before it.",
    )
    evaluation_after: Evaluation


class VerifiedLineFacts(_Facts):
    line_id: str
    origins: tuple[str, ...] = Field(min_length=1, description="Evidence payload fields.")
    begins_with_played_move: bool = False
    uci: tuple[str, ...] = Field(
        min_length=1, description="Cite this sequence, or a prefix of it, as a claim line."
    )
    full_plies: int | None = Field(
        default=None,
        description="Plies in the engine line when it was cut to the plies shown; absent if uncut.",
    )
    moves: tuple[MoveFacts, ...] = Field(min_length=1)


class EvidencePiece(PieceOnSquare):
    role: str = Field(description="Evidence payload field naming this piece.")


class EvidenceFacts(_Facts):
    evidence_id: str
    kind: str
    claim: str
    line_ids: tuple[str, ...] = ()
    pieces: tuple[EvidencePiece, ...] = ()
    squares: tuple[str, ...] = ()
    tactic: str | None = None
    mate_in: int | None = None
    material_gain: int | None = Field(default=None, description="Pawn units won by the line.")


class MistakeClass(_Facts):
    code: str
    name: str


class PositionFacts(_Facts):
    fen: str
    side_to_move: ColourName = Field(description="The learner, who made the mistake.")
    material: Material
    played_move: MoveFacts
    best_move: MoveFacts | None = None
    evaluation_before: Evaluation = Field(description="Position before the played move.")
    evaluation_after: Evaluation = Field(description="Position after the played move.")
    best_move_evaluation: Evaluation
    evaluation_difference_cp: int | None = Field(
        default=None,
        description="Best-move evaluation minus played-move evaluation, learner's view; absent "
        "unless both are centipawn scores.",
    )
    swing_cp: int | None = None
    mistake_class: MistakeClass


class NarratorFacts(_Facts):
    """Every chess fact an explanation may state, computed before the LLM call."""

    schema_version: Literal["narrator-facts/1"]
    position: PositionFacts
    evidence: tuple[EvidenceFacts, ...] = Field(min_length=1)
    lines: tuple[VerifiedLineFacts, ...] = ()

    def to_prompt_json(self) -> str:
        """Compact JSON; fields equal to their schema default are omitted."""
        return self.model_dump_json(exclude_defaults=True)


def narrator_facts_schema() -> dict[str, Any]:
    schema = NarratorFacts.model_json_schema()
    schema["$id"] = "https://github.com/Mathews-Tom/Scan64/schemas/narrator-facts.schema.json"
    return schema


def write_schema(path: Path = SCHEMA_PATH) -> None:
    path.write_text(
        json.dumps(narrator_facts_schema(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )


# --- Payload fields --------------------------------------------------------

# Lines searched from the position before the learner's move.
BEFORE_LINE_FIELDS = ("fast_line",)
BEFORE_MOVE_FIELDS = ("best_move", "pinning_move")
BEFORE_MULTIPV_FIELDS = ("mate_source_multipv",)
# Lines searched from the position after it. ``mating_line`` is the reply
# line for an allowed mate (composer.py) but a before-position line for a
# back-rank motif (motifs.py); ``_mating_line_is_after`` picks its origin.
AFTER_LINE_FIELDS = ("focused_line", "proving_line")
MATING_LINE_FIELD = "mating_line"
AFTER_MOVE_FIELDS = ("threat_move", "incoming_threat")
AFTER_MULTIPV_FIELDS = ("focused_multipv",)

PIECE_SQUARE_FIELDS = (
    ("captured_square", "captured_piece"),
    ("hanging_square", "hanging_piece"),
    ("threatened_square", "threatened_piece"),
    ("pinned_square", "pinned_piece"),
    ("defender_square", "defender_piece"),
    ("front_square", "front_piece"),
    ("target_square", "target_piece"),
    ("slider_square", "slider_piece"),
)
PIECE_LIST_FIELDS = ("targets", "defended_targets")
SQUARE_FIELDS = ("fork_square", "king_square", "blocker_square")
SQUARE_LIST_FIELDS = ("blocker_squares",)
TACTIC_FIELDS = ("tactic_type", "missed_type", "blunder_type")


@dataclass(frozen=True)
class _RawLine:
    origin: str
    moves: tuple[str, ...]
    entry: Mapping[str, Any] | None = None


def _string_list(value: object) -> tuple[str, ...] | None:
    if isinstance(value, list) and value and all(isinstance(move, str) for move in value):
        return tuple(value)
    return None


def _multipv(payload: Mapping[str, Any], name: str) -> list[_RawLine]:
    entries = payload.get(name)
    if not isinstance(entries, list):
        return []
    lines = []
    for index, entry in enumerate(entries, start=1):
        pv = _string_list(entry.get("pv")) if isinstance(entry, Mapping) else None
        if pv is not None:
            lines.append(_RawLine(f"{name}[{index}]", pv, entry))
    return lines


def mating_line_is_after(payload: Mapping[str, Any]) -> bool:
    return payload.get("blunder_type") == "allowed_mate"


def _raw_lines(payload: Mapping[str, Any], played_uci: str) -> list[_RawLine]:
    lines: list[_RawLine] = []
    for name in BEFORE_LINE_FIELDS:
        if (line := _string_list(payload.get(name))) is not None:
            lines.append(_RawLine(name, line))
    for name in BEFORE_MOVE_FIELDS:
        if isinstance(move := payload.get(name), str) and move:
            lines.append(_RawLine(name, (move,)))
    for name in BEFORE_MULTIPV_FIELDS:
        lines += _multipv(payload, name)
    after: list[_RawLine] = []
    for name in AFTER_LINE_FIELDS:
        if (line := _string_list(payload.get(name))) is not None:
            after.append(_RawLine(name, line))
    if (mating := _string_list(payload.get(MATING_LINE_FIELD))) is not None:
        target = after if mating_line_is_after(payload) else lines
        target.append(_RawLine(MATING_LINE_FIELD, mating))
    for name in AFTER_MOVE_FIELDS:
        if isinstance(move := payload.get(name), str) and move:
            after.append(_RawLine(name, (move,)))
    for name in AFTER_MULTIPV_FIELDS:
        after += _multipv(payload, name)
    lines += [_RawLine(line.origin, (played_uci, *line.moves)) for line in after]
    return lines


def _position_key(board: chess.Board) -> str:
    return " ".join(board.fen().split()[:4])


def _colour_name(colour: chess.Color) -> ColourName:
    return "white" if colour == chess.WHITE else "black"


def _piece_name(piece_type: int) -> PieceName:
    name = chess.piece_name(piece_type)
    if name not in ("pawn", "knight", "bishop", "rook", "queen", "king"):
        raise NarratorFactsError(f"Unknown piece type {piece_type}")
    return name  # type: ignore[return-value]


def material(board: chess.Board, learner: chess.Color) -> Material:
    totals = {chess.WHITE: 0, chess.BLACK: 0}
    for piece in board.piece_map().values():
        totals[piece.color] += PIECE_VALUES.get(piece.piece_type, 0)
    return Material(
        white=totals[chess.WHITE],
        black=totals[chess.BLACK],
        learner_advantage=totals[learner] - totals[not learner],
    )


def _undefended_attacked(board: chess.Board, attacker: chess.Color) -> set[tuple[int, chess.Piece]]:
    return {
        (square, piece)
        for square, piece in board.piece_map().items()
        if piece.color != attacker
        and piece.piece_type != chess.KING
        and board.is_attacked_by(attacker, square)
        and not board.is_attacked_by(piece.color, square)
    }


def _on_square(square: int, piece: chess.Piece) -> PieceOnSquare:
    return PieceOnSquare(
        square=chess.square_name(square),
        piece=_piece_name(piece.piece_type),
        colour=_colour_name(piece.color),
    )


# --- Evaluations from stored searches --------------------------------------


def _score(entry: Mapping[str, Any], cp_key: str, mate_key: str) -> tuple[str, int] | None:
    mate = entry.get(mate_key)
    if isinstance(mate, int) and not isinstance(mate, bool):
        return ("mate", mate)
    cp = entry.get(cp_key)
    if isinstance(cp, int) and not isinstance(cp, bool):
        return ("cp", cp)
    return None


def _learner_evaluation(
    score: tuple[str, int] | None, root_is_learner: bool, source: EvaluationSource
) -> CentipawnEvaluation | MateEvaluation | UnknownEvaluation:
    if score is None:
        return UNKNOWN
    kind, value = score
    signed = value if root_is_learner else -value
    if kind == "mate":
        return MateEvaluation(status="mate", mate_in=signed, source=source)
    return CentipawnEvaluation(status="centipawns", centipawns=signed, source=source)


def _legal_push(board: chess.Board, uci: str) -> chess.Board | None:
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return None
    if move not in board.legal_moves:
        return None
    after = board.copy(stack=False)
    after.push(move)
    return after


def parse_played_move(board: chess.Board, played: object) -> chess.Move:
    """The payload stores the learner's move as SAN (composer) or UCI (older fixtures)."""
    if not isinstance(played, str) or not played:
        raise NarratorFactsError("Evidence lacks the played move")
    try:
        move = chess.Move.from_uci(played)
    except ValueError:
        try:
            return board.parse_san(played)
        except ValueError as error:
            raise NarratorFactsError(f"Played move {played!r} is illegal") from error
    if move not in board.legal_moves:
        raise NarratorFactsError(f"Played move {played!r} is illegal")
    return move


@dataclass(frozen=True)
class StoredEvaluations:
    """Learner-view evaluations keyed by position, from the payload's stored searches.

    A MultiPV entry's score is the evaluation after that entry's first move; a
    search's best score is the evaluation of its root. Deeper searches
    (focused, mate source; 1 000 000 nodes) overwrite shallower ones (fast,
    reply; 10 000 nodes) for the same position.
    """

    before: CentipawnEvaluation | MateEvaluation | UnknownEvaluation
    after_played: CentipawnEvaluation | MateEvaluation | UnknownEvaluation
    by_position: Mapping[str, CentipawnEvaluation | MateEvaluation]

    @classmethod
    def from_payload(
        cls, before: chess.Board, payload: Mapping[str, Any], played: chess.Move | None
    ) -> StoredEvaluations:
        fast = _learner_evaluation(
            _score(payload, "fast_score_cp", "fast_score_mate"), True, "fast_pass"
        )
        reply = _learner_evaluation(
            _score(payload, "reply_score_cp", "reply_score_mate"), False, "reply_pass"
        )
        by_position: dict[str, CentipawnEvaluation | MateEvaluation] = {}

        def record(board: chess.Board | None, evaluation: Any) -> None:
            if board is not None and not isinstance(evaluation, UnknownEvaluation):
                by_position[_position_key(board)] = evaluation

        fast_line = _string_list(payload.get("fast_line"))
        if fast_line is not None:
            record(_legal_push(before, fast_line[0]), fast)
        after = None
        if played is not None and played in before.legal_moves:
            after = before.copy(stack=False)
            after.push(played)
            record(after, reply)
            for line in _multipv(payload, "focused_multipv"):
                record(
                    _legal_push(after, line.moves[0]),
                    _entry_evaluation(line, root_is_learner=False, source="focused_pass"),
                )
        for line in _multipv(payload, "mate_source_multipv"):
            record(
                _legal_push(before, line.moves[0]),
                _entry_evaluation(line, root_is_learner=True, source="mate_source_pass"),
            )
        return cls(before=fast, after_played=reply, by_position=by_position)

    def after(self, board: chess.Board) -> CentipawnEvaluation | MateEvaluation | UnknownEvaluation:
        return self.by_position.get(_position_key(board), UNKNOWN)

    def numbers(self) -> tuple[set[int], set[int]]:
        """Absolute centipawn and mate values, plus the best-minus-played difference."""
        evaluations = [self.before, self.after_played, *self.by_position.values()]
        centipawns = {abs(e.centipawns) for e in evaluations if isinstance(e, CentipawnEvaluation)}
        mates = {abs(e.mate_in) for e in evaluations if isinstance(e, MateEvaluation)}
        if (difference := self.difference_cp()) is not None:
            centipawns.add(abs(difference))
        return centipawns, mates

    def difference_cp(self) -> int | None:
        if isinstance(self.before, CentipawnEvaluation) and isinstance(
            self.after_played, CentipawnEvaluation
        ):
            return self.before.centipawns - self.after_played.centipawns
        return None


def _entry_evaluation(
    line: _RawLine, *, root_is_learner: bool, source: EvaluationSource
) -> CentipawnEvaluation | MateEvaluation | UnknownEvaluation:
    entry = line.entry if line.entry is not None else {}
    return _learner_evaluation(_score(entry, "score_cp", "score_mate"), root_is_learner, source)


# --- Lines -------------------------------------------------------------------


def _move_facts(
    board: chess.Board,
    move: chess.Move,
    ply: int,
    learner: chess.Color,
    evaluations: StoredEvaluations,
) -> tuple[MoveFacts, chess.Board]:
    piece = board.piece_at(move.from_square)
    if piece is None:
        raise NarratorFactsError(f"No piece on {chess.square_name(move.from_square)}")
    mover = board.turn
    if board.is_en_passant(move):
        captured_square = move.to_square - 8 if mover == chess.WHITE else move.to_square + 8
    else:
        captured_square = move.to_square
    victim = board.piece_at(captured_square) if board.is_capture(move) else None
    castling: Literal["kingside", "queenside"] | None = None
    if board.is_kingside_castling(move):
        castling = "kingside"
    elif board.is_queenside_castling(move):
        castling = "queenside"
    threats_before = _undefended_attacked(board, mover)
    san = board.san(move)
    after = board.copy(stack=False)
    after.push(move)
    threats = sorted(_undefended_attacked(after, mover) - threats_before)
    facts = MoveFacts(
        ply=ply,
        side=_colour_name(mover),
        mover="learner" if mover == learner else "opponent",
        san=san,
        uci=move.uci(),
        piece=_piece_name(piece.piece_type),
        captures=_on_square(captured_square, victim) if victim is not None else None,
        check=after.is_check(),
        checkmate=after.is_checkmate(),
        promotion=_piece_name(move.promotion) if move.promotion is not None else None,
        castling=castling,
        material_after=material(after, learner),
        new_threats=tuple(_on_square(square, threatened) for square, threatened in threats),
        evaluation_after=evaluations.after(after),
    )
    return facts, after


def verified_lines(
    before: chess.Board, payload: Mapping[str, Any]
) -> list[tuple[tuple[str, ...], tuple[str, ...], int | None]]:
    """Distinct legal lines as ``(origins, uci, full_plies)``, cut at ``MAX_LINE_PLIES``.

    A line that is a prefix of another is merged into it. Raises
    :class:`NarratorFactsError` when a stored line does not replay legally.
    """
    played = parse_played_move(before, payload.get("played_move"))
    merged: dict[tuple[str, ...], list[str]] = {}
    for raw in _raw_lines(payload, played.uci()):
        replay = before.copy(stack=False)
        for uci in raw.moves:
            if _legal_push(replay, uci) is None:
                raise NarratorFactsError(f"Evidence line {raw.origin} has illegal move {uci!r}")
            replay.push(chess.Move.from_uci(uci))
        merged.setdefault(raw.moves, []).append(raw.origin)
    distinct: list[tuple[tuple[str, ...], tuple[str, ...], int | None]] = []
    for moves in merged:
        if any(len(other) > len(moves) and other[: len(moves)] == moves for other in merged):
            continue
        origins = [
            origin
            for other, other_origins in merged.items()
            if moves[: len(other)] == other
            for origin in other_origins
        ]
        cut = moves[:MAX_LINE_PLIES]
        distinct.append((tuple(dict.fromkeys(origins)), cut, len(moves) if cut != moves else None))
    return distinct


def _line_facts(
    line_id: str,
    before: chess.Board,
    origins: tuple[str, ...],
    moves: tuple[str, ...],
    full_plies: int | None,
    played: chess.Move,
    evaluations: StoredEvaluations,
) -> VerifiedLineFacts:
    replay = before
    facts = []
    for ply, uci in enumerate(moves, start=1):
        fact, replay = _move_facts(replay, chess.Move.from_uci(uci), ply, before.turn, evaluations)
        facts.append(fact)
    return VerifiedLineFacts(
        line_id=line_id,
        origins=origins,
        begins_with_played_move=moves[0] == played.uci(),
        uci=moves,
        full_plies=full_plies,
        moves=tuple(facts),
    )


# --- Evidence and position ---------------------------------------------------


def _symbol_piece(symbol: object) -> chess.Piece | None:
    if isinstance(symbol, str) and len(symbol) == 1 and symbol in "pnbrqkPNBRQK":
        return chess.Piece.from_symbol(symbol)
    return None


def _square(name: object) -> str | None:
    return name if isinstance(name, str) and name in chess.SQUARE_NAMES else None


def _evidence_pieces(payload: Mapping[str, Any]) -> tuple[list[EvidencePiece], list[str]]:
    pieces: list[EvidencePiece] = []
    squares: list[str] = []

    def add(role: str, square_name: object, symbol: object) -> None:
        square = _square(square_name)
        if square is None:
            return
        piece = _symbol_piece(symbol)
        if piece is None:
            squares.append(square)
            return
        pieces.append(
            EvidencePiece(
                role=role,
                square=square,
                piece=_piece_name(piece.piece_type),
                colour=_colour_name(piece.color),
            )
        )

    for square_field, piece_field in PIECE_SQUARE_FIELDS:
        add(piece_field, payload.get(square_field), payload.get(piece_field))
    for list_field in PIECE_LIST_FIELDS:
        entries = payload.get(list_field)
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, Mapping):
                    add(list_field, entry.get("square"), entry.get("piece"))
    for square_field in SQUARE_FIELDS:
        if (square := _square(payload.get(square_field))) is not None:
            squares.append(square)
    for list_field in SQUARE_LIST_FIELDS:
        entries = payload.get(list_field)
        if isinstance(entries, list):
            squares += [square for entry in entries if (square := _square(entry)) is not None]
    return pieces, list(dict.fromkeys(squares))


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _evidence_facts(evidence: Evidence, line_ids: Iterable[str]) -> EvidenceFacts:
    payload = evidence.payload
    pieces, squares = _evidence_pieces(payload)
    tactic = next(
        (value for name in TACTIC_FIELDS if isinstance(value := payload.get(name), str)), None
    )
    return EvidenceFacts(
        evidence_id=evidence.evidence_id,
        kind=evidence.kind,
        claim=evidence.claim,
        line_ids=tuple(line_ids),
        pieces=tuple(pieces),
        squares=tuple(squares),
        tactic=tactic,
        mate_in=_optional_int(payload.get("mate_in")),
        material_gain=_optional_int(payload.get("material_gain")),
    )


def _mistake_class(code: str) -> MistakeClass:
    definition = SEED_CODES.get(code)
    if definition is None:
        raise NarratorFactsError(f"Diagnosis code {code!r} is not in the taxonomy")
    return MistakeClass(code=code, name=definition.name)


def _shared(referenced: Sequence[Evidence], field: str) -> object:
    values = {json.dumps(item.payload.get(field), sort_keys=True) for item in referenced}
    if len(values) != 1:
        raise NarratorFactsError(f"Referenced evidence disagrees on {field!r}")
    return referenced[0].payload.get(field)


def build_narrator_facts(
    diagnosis: Diagnosis, evidence: Sequence[Evidence], fen: str
) -> NarratorFacts:
    """Compute the narrator's facts for ``diagnosis`` from ``fen`` (before the mistake)."""
    referenced = [item for item in evidence if item.evidence_id in diagnosis.evidence_refs]
    if not referenced:
        raise NarratorFactsError("Diagnosis references no evidence")
    try:
        before = chess.Board(fen)
    except ValueError as error:
        raise NarratorFactsError("Grounding FEN does not parse") from error
    payload_fen = _shared(referenced, "before_fen")
    if payload_fen is not None and _position_key(chess.Board(str(payload_fen))) != _position_key(
        before
    ):
        raise NarratorFactsError("Evidence before_fen differs from the grounding FEN")
    played = parse_played_move(before, _shared(referenced, "played_move"))
    first = referenced[0].payload
    evaluations = StoredEvaluations.from_payload(before, first, played)
    learner = before.turn

    lines: list[VerifiedLineFacts] = []
    line_ids_by_uci: dict[tuple[str, ...], str] = {}
    evidence_facts = []
    for item in referenced:
        item_evaluations = StoredEvaluations.from_payload(before, item.payload, played)
        ids = []
        for origins, moves, full_plies in verified_lines(before, item.payload):
            line_id = line_ids_by_uci.get(moves)
            if line_id is None:
                line_id = f"L{len(lines) + 1}"
                line_ids_by_uci[moves] = line_id
                lines.append(
                    _line_facts(
                        line_id, before, origins, moves, full_plies, played, item_evaluations
                    )
                )
            ids.append(line_id)
        evidence_facts.append(_evidence_facts(item, ids))

    played_facts, _ = _move_facts(before, played, 1, learner, evaluations)
    fast_line = _string_list(first.get("fast_line"))
    best_facts = None
    if fast_line is not None:
        best_move = chess.Move.from_uci(fast_line[0])
        if best_move not in before.legal_moves:
            raise NarratorFactsError(f"Best move {fast_line[0]!r} is illegal")
        best_facts, _ = _move_facts(before, best_move, 1, learner, evaluations)
    return NarratorFacts(
        schema_version=SCHEMA_VERSION,
        position=PositionFacts(
            fen=before.fen(),
            side_to_move=_colour_name(learner),
            material=material(before, learner),
            played_move=played_facts,
            best_move=best_facts,
            evaluation_before=evaluations.before,
            evaluation_after=evaluations.after_played,
            best_move_evaluation=evaluations.before if best_facts is not None else UNKNOWN,
            evaluation_difference_cp=evaluations.difference_cp(),
            swing_cp=_optional_int(first.get("swing_cp")),
            mistake_class=_mistake_class(diagnosis.primary),
        ),
        evidence=tuple(evidence_facts),
        lines=tuple(lines),
    )


def verified_lines_by_evidence(facts: NarratorFacts) -> dict[str, tuple[tuple[str, ...], ...]]:
    """The lines a claim citing each evidence item may cite (prefixes allowed)."""
    by_id = {line.line_id: line.uci for line in facts.lines}
    return {
        item.evidence_id: tuple(by_id[line_id] for line_id in item.line_ids)
        for item in facts.evidence
        if item.line_ids
    }


if __name__ == "__main__":
    write_schema()
    print(f"Wrote {SCHEMA_PATH}")

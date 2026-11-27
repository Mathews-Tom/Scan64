"""Explain the Analysis Board's engine lines for any position (issue #368).

The engine and python-chess decide every fact; this module only arranges them.
``scored_legal_lines`` keeps the MultiPV lines whose whole principal variation
replays legally; ``build_board_lines_facts`` turns them into a
:class:`BoardLinesFacts` document (``schemas/board-lines-facts.schema.json``,
regenerate with ``uv run python -m scan64.explanations.board_lines``) holding,
for every line, the per-move facts of #367 and what the line wins, loses or
threatens; ``template_board_explanation`` words those facts deterministically.

Grounding: each line is one evidence item (``line-1`` ...) whose only verified
line is the full engine line; ``comparison`` holds every line and the score
differences to the best line. The template's own claims pass the same
validator as provider output (a failure is a bug and raises). Provider output
that fails validation, or a claim linked to no line, falls back to the template.

Coordinates: every line starts at the board position; "you" and "the learner"
in claim grammar mean the side to move. Evaluations are the engine's MultiPV
scores from the side to move's point of view: a line's score is recorded as the
evaluation after its first move; no other move is scored (``unknown``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import chess
import httpx
from chess_lesson_spec import Explanation, ExplanationClaim
from pydantic import Field

from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.assembly import (
    TemplateFallbackReason,
    record_narrator_accepted,
    record_narrator_attempt,
    record_template_fallback,
    run_with_explanation_provider,
    slot_rules,
)
from scan64.explanations.facts import (
    MAX_LINE_PLIES,
    UNKNOWN,
    CentipawnEvaluation,
    ColourName,
    Evaluation,
    MateEvaluation,
    Material,
    MoveFacts,
    PieceOnSquare,
    StoredEvaluations,
    _colour_name,
    _Facts,
    _move_facts,
    _position_key,
    material,
)
from scan64.explanations.slots import (
    Slot,
    SlotCatalogue,
    evaluation_value,
    line_slots,
    numbered_san,
    pawn_units,
)
from scan64.explanations.validator import (
    GroundedExplanationContext,
    GroundedExplanationValidationError,
    validate_generated_explanation,
)
from scan64.explanations.voice import DEFAULT_NARRATOR_VOICE, NarratorVoice, with_voice
from scan64.learning.evidence.composer import line_material_gain
from scan64.learning.evidence.models import Evidence
from scan64.providers.llm import ExplanationRequest, LLMMessage, LLMProviderError
from scan64.providers.llm.adapters import LLMExplanationProvider
from scan64.providers.llm.contracts import GeneratedExplanation

BOARD_SCHEMA_VERSION: Literal["board-lines-facts/1"] = "board-lines-facts/1"
BOARD_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "schemas" / "board-lines-facts.schema.json"
)
COMPARISON_EVIDENCE_ID = "comparison"
# The explanation is requested explicitly and shown at once, as in assembly.py.
_FULL_DISCLOSURE_HINT_LEVEL = 3
_MAX_TEMPLATE_THREATS = 2
# The narrator prompt holds per-move facts for this many plies of each line, as
# the lesson prompt does (#388). ``uci`` and ``outcome`` still cover the whole
# line, so a claim can cite it all. Full move facts for four 27-ply lines are
# about 11,000 tokens and do not fit the default 12288-token Ollama context
# with the answer; the cut keeps the largest recorded prompt near 6,000.
PROMPT_MOVE_PLIES = MAX_LINE_PLIES


class BoardLinesError(ValueError):
    """The engine lines cannot be turned into board-checked facts."""


# --- Engine lines --------------------------------------------------------------


@dataclass(frozen=True)
class ScoredLine:
    """One MultiPV line with a score and a principal variation legal to its end.

    Scores and ``wdl`` are from the side to move, as stored by the adapters.
    """

    score_cp: int | None
    score_mate: int | None
    moves: tuple[chess.Move, ...]
    depth: int | None
    seldepth: int | None
    wdl: tuple[int, int, int] | None

    @property
    def uci(self) -> tuple[str, ...]:
        return tuple(move.uci() for move in self.moves)


def _strict_int(value: object) -> int | None:
    return value if type(value) is int else None


def _wdl(value: object) -> tuple[int, int, int] | None:
    if isinstance(value, list | tuple) and len(value) == 3 and all(type(v) is int for v in value):
        return (value[0], value[1], value[2])
    return None


def scored_legal_lines(board: chess.Board, analysis: EngineAnalysis) -> list[ScoredLine]:
    """Engine-ranked lines with exactly one score kind and a fully legal PV.

    A line with any malformed, null or illegal move is dropped whole: a cut
    line would no longer be the line the engine scored.
    """
    lines: list[ScoredLine] = []
    for result in analysis.raw_result:
        cp = result.get("score_cp")
        mate = result.get("score_mate")
        if not ((type(cp) is int and mate is None) or (cp is None and type(mate) is int)):
            continue
        pv = result.get("pv")
        if not isinstance(pv, list) or not pv:
            continue
        replay = board.copy(stack=False)
        moves: list[chess.Move] = []
        for uci in pv:
            if not isinstance(uci, str):
                break
            try:
                move = replay.parse_uci(uci)
            except ValueError:
                break
            # parse_uci accepts the null move, which is not a legal continuation.
            if move not in replay.legal_moves:
                break
            moves.append(move)
            replay.push(move)
        else:
            lines.append(
                ScoredLine(
                    score_cp=_strict_int(cp),
                    score_mate=_strict_int(mate),
                    moves=tuple(moves),
                    depth=_strict_int(result.get("depth")),
                    seldepth=_strict_int(result.get("seldepth")),
                    wdl=_wdl(result.get("wdl")),
                )
            )
    return lines


# --- Facts -----------------------------------------------------------------------


class LineOutcome(_Facts):
    """What the whole line wins, loses or threatens, from python-chess replay."""

    material_change: int = Field(
        description="Pawn units the side to move wins (positive) or loses (negative) over the "
        "line: captures and promotions (pawn 1, knight 3, bishop 3, rook 5, queen 9)."
    )
    material_at_end: Material
    checks_by_side_to_move: int = Field(ge=0)
    checks_by_opponent: int = Field(ge=0)
    ends_in_checkmate: bool = False
    ends_in_stalemate: bool = False
    first_move_threats: tuple[PieceOnSquare, ...] = Field(
        default=(), description="new_threats of the line's first move."
    )


class BoardLineFacts(_Facts):
    line_id: str
    rank: int = Field(ge=1, description="Engine MultiPV rank, 1 is the engine's first choice.")
    evaluation: Evaluation = Field(description="Engine score of the line, side to move's view.")
    difference_from_best_cp: int | None = Field(
        default=None,
        description="Best line's score minus this line's, side to move's view; absent unless "
        "both are centipawn scores.",
    )
    depth: int | None = None
    seldepth: int | None = None
    wdl: tuple[int, int, int] | None = Field(
        default=None, description="Win, draw, loss per mille for the side to move."
    )
    uci: tuple[str, ...] = Field(
        min_length=1, description="The full engine line. Cite it, or a prefix, as a claim line."
    )
    moves: tuple[MoveFacts, ...] = Field(
        min_length=1,
        description=f"Facts for the moves of uci, in order. The narrator prompt holds the first "
        f"{PROMPT_MOVE_PLIES} only; outcome covers the whole line.",
    )
    outcome: LineOutcome


class BoardPositionFacts(_Facts):
    fen: str
    side_to_move: ColourName
    material: Material
    evaluation: Evaluation = Field(description="Best line's engine score, side to move's view.")


class BoardLinesFacts(_Facts):
    """Every chess fact an Analysis Board explanation may state."""

    schema_version: Literal["board-lines-facts/1"]
    position: BoardPositionFacts
    lines: tuple[BoardLineFacts, ...] = Field(min_length=1)

    def to_prompt_json(self) -> str:
        """Compact JSON; fields equal to their schema default are omitted."""
        return self.model_dump_json(exclude_defaults=True)

    def for_prompt(self) -> BoardLinesFacts:
        """These facts with each line's ``moves`` cut to ``PROMPT_MOVE_PLIES``.

        ``uci``, ``outcome`` and the scores are unchanged; the response, the
        template and the grounding keep using the full facts.
        """
        return self.model_copy(
            update={
                "lines": tuple(
                    line.model_copy(update={"moves": line.moves[:PROMPT_MOVE_PLIES]})
                    for line in self.lines
                )
            }
        )


def board_lines_facts_schema() -> dict[str, Any]:
    schema = BoardLinesFacts.model_json_schema()
    schema["$id"] = "https://github.com/Mathews-Tom/Scan64/schemas/board-lines-facts.schema.json"
    return schema


def write_schema(path: Path = BOARD_SCHEMA_PATH) -> None:
    path.write_text(
        json.dumps(board_lines_facts_schema(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def _evaluation(line: ScoredLine) -> CentipawnEvaluation | MateEvaluation:
    if line.score_mate is not None:
        return MateEvaluation(status="mate", mate_in=line.score_mate, source="analysis_board_pass")
    if line.score_cp is None:
        raise BoardLinesError("An engine line has no score")
    return CentipawnEvaluation(
        status="centipawns", centipawns=line.score_cp, source="analysis_board_pass"
    )


def _difference_cp(best: ScoredLine, line: ScoredLine) -> int | None:
    if best.score_cp is None or line.score_cp is None:
        return None
    return best.score_cp - line.score_cp


def _stored_evaluations(board: chess.Board, lines: Sequence[ScoredLine]) -> StoredEvaluations:
    by_position: dict[str, CentipawnEvaluation | MateEvaluation] = {}
    for line in lines:
        after = board.copy(stack=False)
        after.push(line.moves[0])
        by_position.setdefault(_position_key(after), _evaluation(line))
    return StoredEvaluations(
        before=_evaluation(lines[0]), after_played=UNKNOWN, by_position=by_position
    )


def _line_facts(
    board: chess.Board,
    rank: int,
    line: ScoredLine,
    best: ScoredLine,
    evaluations: StoredEvaluations,
) -> BoardLineFacts:
    side = board.turn
    replay = board
    moves: list[MoveFacts] = []
    for ply, move in enumerate(line.moves, start=1):
        fact, replay = _move_facts(replay, move, ply, side, evaluations)
        moves.append(fact)
    gain = line_material_gain(board, list(line.uci))
    if gain is None:
        raise BoardLinesError(f"Engine line {rank} does not replay legally")
    outcome = LineOutcome(
        material_change=gain,
        material_at_end=moves[-1].material_after,
        checks_by_side_to_move=sum(1 for m in moves if m.check and m.mover == "learner"),
        checks_by_opponent=sum(1 for m in moves if m.check and m.mover == "opponent"),
        ends_in_checkmate=replay.is_checkmate(),
        ends_in_stalemate=replay.is_stalemate(),
        first_move_threats=moves[0].new_threats,
    )
    return BoardLineFacts(
        line_id=f"line-{rank}",
        rank=rank,
        evaluation=_evaluation(line),
        difference_from_best_cp=_difference_cp(best, line) if rank > 1 else None,
        depth=line.depth,
        seldepth=line.seldepth,
        wdl=line.wdl,
        uci=line.uci,
        moves=tuple(moves),
        outcome=outcome,
    )


def build_board_lines_facts(board: chess.Board, lines: Sequence[ScoredLine]) -> BoardLinesFacts:
    """Facts for ``lines`` (engine order, best first) searched from ``board``."""
    if not lines:
        raise BoardLinesError("No engine line to explain")
    root = board.copy(stack=False)
    evaluations = _stored_evaluations(root, lines)
    return BoardLinesFacts(
        schema_version=BOARD_SCHEMA_VERSION,
        position=BoardPositionFacts(
            fen=root.fen(),
            side_to_move=_colour_name(root.turn),
            material=material(root, root.turn),
            evaluation=evaluations.before,
        ),
        lines=tuple(
            _line_facts(root, rank, line, lines[0], evaluations)
            for rank, line in enumerate(lines, start=1)
        ),
    )


# --- Grounding ---------------------------------------------------------------------


def _multipv_entry(line: BoardLineFacts) -> dict[str, Any]:
    evaluation = line.evaluation
    return {
        "pv": list(line.uci),
        "score_cp": evaluation.centipawns if isinstance(evaluation, CentipawnEvaluation) else None,
        "score_mate": evaluation.mate_in if isinstance(evaluation, MateEvaluation) else None,
    }


def board_lines_grounding(facts: BoardLinesFacts) -> GroundedExplanationContext:
    """One evidence item per line plus ``comparison`` (every line) when there are two or more."""
    evidence = [
        Evidence(
            evidence_id=line.line_id,
            kind="engine_line",
            position_id=facts.position.fen,
            engine_analysis_id="analysis-board",
            claim=f"Engine line {line.rank}",
            # A line's own facts include how far it trails line 1.
            payload={
                "board_multipv": [_multipv_entry(line)],
                "best_differences_cp": [line.difference_from_best_cp]
                if line.difference_from_best_cp is not None
                else [],
            },
        )
        for line in facts.lines
    ]
    verified: dict[str, tuple[tuple[str, ...], ...]] = {
        line.line_id: (line.uci,) for line in facts.lines
    }
    if len(facts.lines) > 1:
        evidence.append(
            Evidence(
                evidence_id=COMPARISON_EVIDENCE_ID,
                kind="engine_line_comparison",
                position_id=facts.position.fen,
                engine_analysis_id="analysis-board",
                claim="Comparison of the engine lines",
                payload={
                    "board_multipv": [_multipv_entry(line) for line in facts.lines],
                    "best_differences_cp": [
                        line.difference_from_best_cp
                        for line in facts.lines
                        if line.difference_from_best_cp is not None
                    ],
                },
            )
        )
        verified[COMPARISON_EVIDENCE_ID] = tuple(line.uci for line in facts.lines)
    return GroundedExplanationContext(
        fen=facts.position.fen,
        evidence=tuple(evidence),
        verified_lines=verified,
        requested_hint_level=_FULL_DISCLOSURE_HINT_LEVEL,
    )


# --- Slots (#386) ---------------------------------------------------------------------

SIDE_TO_MOVE = "the side to move"
_BOARD_OPPONENT = "the opponent"
_BOARD_MOVERS = {"learner": "side to move", "opponent": "opponent"}


def _points(value: int) -> str:
    return f"{value} point{'s' if value != 1 else ''} of material"


def _board_line_slots(board: chess.Board, line: BoardLineFacts) -> list[Slot]:
    lid, k = line.line_id, line.rank
    slots = [
        Slot(f"label:{lid}", f"line {k}", "the line's name", phrase=True),
        Slot(
            f"first:{lid}",
            numbered_san(board, line.uci[:1]),
            f"first move of line {k}",
            line.uci[:1],
        ),
    ]
    evaluation = evaluation_value(line.evaluation, side=SIDE_TO_MOVE, other=_BOARD_OPPONENT)
    if evaluation is not None:
        slots.append(Slot(f"eval:{lid}", evaluation, f"engine score of line {k}, side to move"))
    if line.difference_from_best_cp is not None and line.difference_from_best_cp != 0:
        relation = "less" if line.difference_from_best_cp > 0 else "more"
        slots.append(
            Slot(
                f"diff:{lid}",
                f"{pawn_units(line.difference_from_best_cp, signed=False)} {relation} than line 1",
                f"line {k} against line 1, side to move",
            )
        )
    outcome = line.outcome
    if outcome.material_change > 0:
        change = f"over line {k} the side to move wins {_points(outcome.material_change)}"
    elif outcome.material_change < 0:
        change = f"over line {k} the side to move loses {_points(-outcome.material_change)}"
    else:
        change = f"over line {k} the material balance does not change"
    slots.append(Slot(f"outcome:{lid}", change, "", line.uci, phrase=True))
    advantage = outcome.material_at_end.learner_advantage
    if advantage == 0:
        balance = f"at the end of line {k} material is equal"
    else:
        direction = "up" if advantage > 0 else "down"
        balance = (
            f"at the end of line {k} the side to move is {direction} {_points(abs(advantage))}"
        )
    slots.append(Slot(f"balance:{lid}", balance, "", line.uci, phrase=True))
    if outcome.checks_by_side_to_move:
        n = outcome.checks_by_side_to_move
        times = "once" if n == 1 else f"{n} times"
        slots.append(
            Slot(
                f"checks:{lid}",
                f"the side to move gives check {times} in line {k}",
                "",
                line.uci,
                phrase=True,
            )
        )
    if outcome.ends_in_checkmate:
        slots.append(Slot(f"end:{lid}", f"line {k} ends in checkmate", "", line.uci, phrase=True))
    elif outcome.ends_in_stalemate:
        slots.append(Slot(f"end:{lid}", f"line {k} ends in stalemate", "", line.uci, phrase=True))
    slots += line_slots(lid, board, line.uci, line.moves, mover_words=_BOARD_MOVERS)
    return slots


def board_slot_catalogue(facts: BoardLinesFacts) -> SlotCatalogue:
    """Slots per line evidence item; ``comparison`` holds every line's slots.

    The prompt lists ``comparison`` by reference to the lines, so each line's
    slots appear once (#415: repeating them doubled the Analysis Board prompt).
    """
    board = chess.Board(facts.position.fen)
    shared: list[Slot] = []
    evaluation = evaluation_value(
        facts.position.evaluation, side=SIDE_TO_MOVE, other=_BOARD_OPPONENT
    )
    if evaluation is not None:
        shared.append(Slot("eval", evaluation, "the position's engine score, side to move"))
    per_line = {line.line_id: _board_line_slots(board, line) for line in facts.lines}
    by_evidence: dict[str, dict[str, Slot]] = {}
    for line in facts.lines:
        by_evidence[line.line_id] = {slot.name: slot for slot in (*shared, *per_line[line.line_id])}
    if len(facts.lines) > 1:
        by_evidence[COMPARISON_EVIDENCE_ID] = {
            slot.name: slot
            for slot in (*shared, *(slot for slots in per_line.values() for slot in slots))
        }
    combined = (
        {COMPARISON_EVIDENCE_ID: tuple(line.line_id for line in facts.lines)}
        if len(facts.lines) > 1
        else {}
    )
    return SlotCatalogue(
        by_evidence=by_evidence,
        shared=frozenset(slot.name for slot in shared),
        combined=combined,
    )


def board_slot_grounding(facts: BoardLinesFacts) -> GroundedExplanationContext:
    """The grounding context for provider wording: the line grounding plus the slots."""
    return replace(board_lines_grounding(facts), slots=board_slot_catalogue(facts))


def claim_line_id(claim: ExplanationClaim, facts: BoardLinesFacts) -> str:
    """The line a claim is linked to; raises when it is linked to none."""
    by_id = {line.line_id: line for line in facts.lines}
    if claim.evidence_ref in by_id:
        return claim.evidence_ref
    if claim.line:
        for line in facts.lines:
            if line.uci[: len(claim.line)] == claim.line:
                return line.line_id
    raise GroundedExplanationValidationError(
        "Explanation claim is linked to no engine line", reason="missing_line"
    )


# --- Template ------------------------------------------------------------------------


def _score_phrase(evaluation: Evaluation) -> str:
    if isinstance(evaluation, MateEvaluation):
        side = "for" if evaluation.mate_in > 0 else "against"
        return f"mate in {abs(evaluation.mate_in)} {side} the side to move"
    if isinstance(evaluation, CentipawnEvaluation):
        return f"{evaluation.centipawns / 100:+.2f} for the side to move"
    raise BoardLinesError("An engine line has no score")


def _claim(text: str, evidence_ref: str, line: Sequence[str]) -> ExplanationClaim:
    return ExplanationClaim(
        text=text, evidence_ref=evidence_ref, line=tuple(line), disclosure_level=1
    )


def _line_claims(board: chess.Board, side: str, line: BoardLineFacts) -> list[ExplanationClaim]:
    k = line.rank
    claims = [_claim(f"Line {k}: {numbered_san(board, line.uci)}.", line.line_id, line.uci)]
    depth = f" at depth {line.depth}" if line.depth is not None else ""
    claims.append(
        _claim(
            f"The engine scores line {k} {_score_phrase(line.evaluation)}{depth}.",
            line.line_id,
            line.uci,
        )
    )
    first = numbered_san(board, line.uci[:1])
    for threat in line.outcome.first_move_threats[:_MAX_TEMPLATE_THREATS]:
        claims.append(
            _claim(
                f"After {first} the side to move attacks the undefended {threat.piece} on "
                f"{threat.square}.",
                line.line_id,
                line.uci[:1],
            )
        )
    outcome = line.outcome
    if outcome.material_change > 0:
        change = "Over line {k} the side to move wins material."
    elif outcome.material_change < 0:
        change = "Over line {k} the side to move loses material."
    else:
        change = "Over line {k} the material balance does not change."
    advantage = outcome.material_at_end.learner_advantage
    if advantage == 0:
        balance = "At its end material is equal."
    else:
        direction = "up" if advantage > 0 else "down"
        points = abs(advantage)
        balance = (
            f"At its end {side} is {direction} {points} "
            f"point{'s' if points != 1 else ''} of material."
        )
    claims.append(_claim(f"{change.format(k=k)} {balance}", line.line_id, line.uci))
    checks = outcome.checks_by_side_to_move
    if checks:
        claims.append(
            _claim(
                f"In line {k} the side to move gives check "
                f"{'once' if checks == 1 else f'{checks} times'}.",
                line.line_id,
                line.uci,
            )
        )
    if outcome.ends_in_checkmate:
        claims.append(_claim(f"Line {k} ends in checkmate.", line.line_id, line.uci))
    elif outcome.ends_in_stalemate:
        claims.append(_claim(f"Line {k} ends in stalemate.", line.line_id, line.uci))
    return claims


def _comparison_claim(
    board: chess.Board, best: BoardLineFacts, line: BoardLineFacts
) -> ExplanationClaim:
    k = line.rank
    first = numbered_san(board, line.uci[:1])
    difference = line.difference_from_best_cp
    if difference is None:
        text = (
            f"Line {k} ({first}) is scored {_score_phrase(line.evaluation)}, against "
            f"{_score_phrase(best.evaluation)} for line 1."
        )
    elif difference == 0:
        text = f"Line {k} ({first}) scores the same as line 1 for the side to move."
    else:
        relation = "less" if difference > 0 else "more"
        text = (
            f"Line {k} ({first}) scores {abs(difference) / 100:.2f} pawns {relation} than "
            "line 1 for the side to move."
        )
    return _claim(text, COMPARISON_EVIDENCE_ID, line.uci)


def template_board_claims(facts: BoardLinesFacts) -> GeneratedExplanation:
    """Deterministic claims for every line and the comparison; no LLM involved."""
    board = chess.Board(facts.position.fen)
    side = "White" if board.turn == chess.WHITE else "Black"
    best = facts.lines[0]
    claims = [
        _claim(
            f"{side} to move. The engine's first choice is {numbered_san(board, best.uci[:1])}.",
            best.line_id,
            best.uci[:1],
        )
    ]
    for line in facts.lines:
        claims.extend(_line_claims(board, side, line))
    claims.extend(_comparison_claim(board, best, line) for line in facts.lines[1:])
    return GeneratedExplanation(claims=tuple(claims))


def template_board_explanation(facts: BoardLinesFacts) -> Explanation:
    """The template, validated like provider output; a rejection is a template bug."""
    return validate_generated_explanation(
        template_board_claims(facts), board_lines_grounding(facts)
    )


# --- Provider wording ----------------------------------------------------------------

BOARD_NARRATOR_SYSTEM_PROMPT = (
    "You explain the chess engine's top lines for one position. The first user message is a "
    f"JSON document following the Scan64 schema {BOARD_SCHEMA_VERSION} "
    "(schemas/board-lines-facts.schema.json). The board library and the chess engine computed "
    "every fact in it; a field left out has its schema default (no capture, no check, no "
    "threats). Use only those facts: do not work out moves, captures, checks, threats, "
    "material or evaluations yourself, and state nothing the facts do not state.\n"
    "- position: side_to_move is the side whose options the lines are. In move facts, mover "
    '"learner" is the side to move and "opponent" the other side.\n'
    "- lines: engine lines in engine order (rank 1 is best), each from the position.\n"
    "- "
    + slot_rules(
        "{first:line-1} or {line:line-1}",
        "{label:line-2} starts with {first:line-2} and scores {diff:line-2}.",
    )
    + "\n"
    "Answer with claims: explain what each line wins, loses or threatens, then compare the "
    "lines. Each claim: text (one or two plain sentences with slots), evidence_ref (the "
    f'line_id of the line it is about, or "{COMPARISON_EVIDENCE_ID}" for a claim comparing '
    'lines), line (a JSON list of UCI move strings such as "e2e4", copied in order from the '
    "start of that line's uci list, never SAN and never empty; for "
    f'"{COMPARISON_EVIDENCE_ID}" copy from the line being compared), certainty "observed", '
    'disclosure_level 1. Call the sides "the side to move" and "the opponent".'
)


def board_narrator_system_prompt(voice: NarratorVoice = DEFAULT_NARRATOR_VOICE) -> str:
    """BOARD_NARRATOR_SYSTEM_PROMPT with the voice fragment as its last line (#387)."""
    return with_voice(BOARD_NARRATOR_SYSTEM_PROMPT, voice)


def board_lines_request(
    facts: BoardLinesFacts, voice: NarratorVoice = DEFAULT_NARRATOR_VOICE
) -> ExplanationRequest:
    return ExplanationRequest(
        messages=(
            LLMMessage(role="system", content=board_narrator_system_prompt(voice)),
            LLMMessage(role="user", content=facts.for_prompt().to_prompt_json()),
            LLMMessage(role="user", content=board_slot_catalogue(facts).prompt()),
        )
    )


ExplanationSource = Literal["template", "narrator"]


@dataclass(frozen=True)
class BoardLinesExplanation:
    """The explanation shown, which path produced it, and why a provider was not used."""

    explanation: Explanation
    source: ExplanationSource
    fallback_reason: TemplateFallbackReason | None


def _validated_provider_explanation(
    generated: GeneratedExplanation, facts: BoardLinesFacts
) -> Explanation:
    explanation = validate_generated_explanation(generated, board_slot_grounding(facts))
    for claim in explanation.claims:
        claim_line_id(claim, facts)
    return explanation


async def resolve_board_lines_explanation(
    facts: BoardLinesFacts,
    *,
    local_provider: LLMExplanationProvider | None = None,
    environment: Mapping[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
    voice: NarratorVoice | None = None,
) -> BoardLinesExplanation:
    """Template first; a configured provider's wording replaces it only when grounded.

    ``voice`` overrides the configured narrator voice (``run_with_explanation_provider``).
    """
    template = BoardLinesExplanation(template_board_explanation(facts), "template", None)

    async def attempt(
        provider: LLMExplanationProvider, chosen: NarratorVoice
    ) -> BoardLinesExplanation:
        record_narrator_attempt("board")
        try:
            generated = await provider.generate(board_lines_request(facts, chosen))
            explanation = _validated_provider_explanation(generated, facts)
        except GroundedExplanationValidationError as error:
            record_template_fallback(error.reason, "board")
            return BoardLinesExplanation(template.explanation, "template", error.reason)
        except LLMProviderError:
            record_template_fallback("provider_error", "board")
            return BoardLinesExplanation(template.explanation, "template", "provider_error")
        record_narrator_accepted("board")
        return BoardLinesExplanation(explanation, "narrator", None)

    return await run_with_explanation_provider(
        template,
        attempt,
        local_provider=local_provider,
        environment=environment,
        client=client,
        voice=voice,
    )


__all__ = [
    "BOARD_SCHEMA_PATH",
    "BOARD_SCHEMA_VERSION",
    "COMPARISON_EVIDENCE_ID",
    "PROMPT_MOVE_PLIES",
    "BoardLineFacts",
    "BoardLinesError",
    "BoardLinesExplanation",
    "BoardLinesFacts",
    "ScoredLine",
    "board_lines_grounding",
    "board_slot_catalogue",
    "board_slot_grounding",
    "board_lines_request",
    "board_narrator_system_prompt",
    "build_board_lines_facts",
    "claim_line_id",
    "numbered_san",
    "resolve_board_lines_explanation",
    "scored_legal_lines",
    "template_board_claims",
    "template_board_explanation",
]


if __name__ == "__main__":
    write_schema()
    print(f"Wrote {BOARD_SCHEMA_PATH}")

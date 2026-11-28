"""Verified training pairs: code facts in, a human annotator's sentence out (#464).

For each aligned comment of the annotated corpus (#463), the facts for its
position are computed with the live narrator's code: the role-labelled facts
(``role_facts``, #460) of the game move against the engine's best move, plus
the concept library's facts (#459). The target is one sentence of the comment,
kept only when the facts back every chess claim in it:

- every move it names is one of the facts' moves; descriptive notation
  ("Kt-B3") is read against the positions of those moves and written in SAN;
- every square, piece and chess term it names is in the facts
  (``sentence_check``, #454), and a capture word needs a capture in the facts;
- it has no judgement the facts do not make ("better", "a mistake");
- the engine does not contradict it: no mate claim without a mate, no draw
  claim in a won position, no "winning" in a level one.

A sentence whose only fault is a judgement or term is trimmed to its longest
leading part that passes, when there is one.

Everything here is pure: the engine's lines come in as ``EngineView`` values,
so the rules are tested without an engine. ``scripts/build-training-pairs.py``
runs the engine, writes the pairs and the report.
"""

from __future__ import annotations

import copy
import re
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import chess

from scan64.explanations.concepts import (
    ConceptFact,
    intent_concepts,
    move_concepts,
    position_concepts,
)
from scan64.explanations.role_facts import RoleFact, numbered_san, role_facts
from scan64.explanations.sentence_check import (
    CAPTURE_WORDS,
    JUDGEMENT_WORDS,
    MOVE,
    SQUARE,
    TERMS,
    fact_takes,
    judgement_problems,
    pairing_problems,
    violations,
)
from scan64.learning.annotated_corpus.descriptive import DescriptiveError, Origins, parse_move

Outcome = Literal["kept", "trimmed", "dropped"]

# The order reasons are given in: the first that applies names the drop.
REASONS: tuple[str, ...] = (
    "no move before the comment",
    "no chess content",
    "descriptive square or piece",
    "move not in the facts",
    "move ambiguous",
    "engine: no mate",
    "engine: not a draw",
    "engine: level",
    "unsupported term",
    "unsupported judgement",
    "unsupported piece or square",
    "capture without a capture fact",
    "piece on the wrong square",
    "no move or square of the facts",
    "more than one sentence",
    "played move is not the game move",
    "name or date",
    "negation",
)

# A descriptive move in prose: "P-K4", "Kt - R 4", "KtxP ch", "B(Q2)-K3", "Castles",
# with an optional move number ("30.", "8. ...", "19...").
_DESIGNATOR = r"(?:[KQ]\s?(?:Kt|R|B)|Kt|K|Q|R|B)"
_PIECE = r"(?:Kt|K|Q|R|B|P)"
_FROM = r"(?:\s?\(\s?[A-Za-z]+\s?[1-8]\s?\))?"
DESCRIPTIVE_MOVE = re.compile(
    rf"(?:\b\d+(?:\s?\.(?:\s?\.\.\.?)?|\s?\.\.\.)?\s?)?"
    rf"(?:\bCastles(?:\s?\((?:KR|QR|K|Q)\))?"
    rf"|\b(?:{_DESIGNATOR}\s?)?{_PIECE}{_FROM}\s?"
    rf"(?:-\s?{_DESIGNATOR}?\s?[1-8]|[x×]\s?(?:{_DESIGNATOR}\s?)?{_PIECE}{_FROM}))"
    rf"(?:\s?(?:dis\.?\s?ch|ch|mate)\b)?"
)
# A descriptive square left once the moves are read: "at Kt2", "KB4", "Q 5".
DESCRIPTIVE_SQUARE = re.compile(
    rf"\b{_DESIGNATOR}\s?[1-8]\b|\b(?:[KQ]?(?:Kt|R|B)|[KQ])?P\b|\bKt\b|\b(?:[KQ](?:Kt|R|B)|Q|R|B|K)\b"
)
# A clause boundary a trim may cut at.
_CLAUSE = re.compile(r",\s(?=(?:as|for|because|since|which|so)\b)")
# A sentence opening with one of these is a dependent clause: no trim keeps its start alone.
_DEPENDENT = re.compile(
    r"^(?:if|against|after|in view|when|although|though|as|since|while|unless|because|should)\b",
    re.IGNORECASE,
)
# "played", "answered", "chose": a claim about the game move, unless it is "could have played".
_PLAY_VERB = re.compile(
    r"\b(?:play|plays|played|answer|answers|answered|chose|chooses|repl(?:y|ies|ied))\b",
    re.IGNORECASE,
)
_COUNTERFACTUAL = re.compile(
    r"\b(?:could|should|would|might|must|can|cannot|may)(?:\s+not)?(?:\s+have)?\s+(?:been\s+)?(?:play|played|answer|answered|chose|chosen|repl(?:y|ied))\b",
    re.IGNORECASE,
)
_NUMBERED_SAN = re.compile(r"\b\d+\.(?:\.\.)?[A-Za-z][\w=+#-]*[\w+#]")
# A capitalised word after the first that is not a side or a piece: a player, an event, a place.
_NAME = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-z]+\b")
_NOT_A_NAME = frozenset(
    {"White", "Black", "King", "Queen", "Rook", "Bishop", "Knight", "Pawn", "Kt"}
)
_YEAR = re.compile(r"\b1[5-9]\d\d\b")
# A denial ("is not attacked", "nothing defends", "did not play"): the facts state what
# is, so a negated claim is never backed.
_NEGATION = re.compile(
    r"\b(?:not|no|nothing|nobody|none|never|neither|nor|without|cannot)\b|n't\b", re.IGNORECASE
)
# A side the sentence names: an unnumbered move is that side's move.
_SIDE = re.compile(r"\b(White|Black)(?:'s)?\b")
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
_NUMBER = re.compile(r"^(\d+)\s?(\.\s?\.\.\.?|\.\.\.)?")
_MATE = re.compile(r"\b(?:check)?mate[sd]?\b", re.IGNORECASE)
_DRAW = re.compile(r"\bdraw(?:s|n)?\b", re.IGNORECASE)
# "Winning" or "wins." about the game; "wins a pawn" is for the capture rule.
_WINNING = re.compile(
    r"\b(?:winning|won|wins)\b(?!\s+(?:a|an|the|two|three|both|his|her|its|their|white's|black's)\b)",
    re.IGNORECASE,
)
# Claims an annotator makes that no role fact or concept states yet, on top of
# ``sentence_check.TERMS``: a threat, a plan, a defence, an opening name. A sentence
# may use one only when its facts do.
CLAIM_WORDS: tuple[str, ...] = (
    "threat",
    "hope",
    "plan",
    "idea",
    "defen",
    "prevent",
    "avoid",
    "escape",
    "attack",
    "gambit",
    "opening",
    "variation",
    "complicat",
    "promising",
    "chance",
    "cannot",
    "can't",
    "bad",
    "los",
    "would",
    "resign",
    "recapture",
    "fall",
    "wary",
    "careful",
    "beware",
    "must",
    "should",
)
# A score this close to level contradicts "winning"; one this far from level, "a draw".
LEVEL_CP = 150
DRAW_CP = 200
MATE_CP = 100_000


@dataclass(frozen=True)
class EngineView:
    """The engine's view of a comment's move, scores from the mover's side.

    ``best_line`` starts with the best move from the position before the
    move; ``played_line`` follows the game move; ``threat_line`` is the
    opponent's best line if the mover passed (``None`` when the null move
    is illegal, in check).
    """

    best_line: tuple[str, ...]
    played_line: tuple[str, ...]
    threat_line: tuple[str, ...] | None
    best_score_cp: int
    played_score_cp: int
    best_mate: bool
    played_mate: bool


@dataclass(frozen=True)
class PositionFacts:
    """The live narrator's facts for one comment's move."""

    fen_before: str
    played: str
    role: tuple[RoleFact, ...]
    concepts: tuple[ConceptFact, ...]
    # Every move the facts hold, with the board and piece origins before it.
    positions: tuple[tuple[chess.Board, Origins, chess.Move], ...]

    @property
    def text(self) -> str:
        return " ".join([fact.text for fact in self.role] + [c.text for c in self.concepts])

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(relation.kind for fact in self.role for relation in fact.relations)

    @property
    def sides(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for fact in self.role:
            for relation in fact.relations:
                for piece in (relation.subject, relation.target, relation.through):
                    if piece is not None:
                        found[chess.square_name(piece.square)] = (
                            "white" if piece.colour == chess.WHITE else "black"
                        )
        return found


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    # The sentence in SAN, trimmed when ``outcome`` is "trimmed"; ``None`` when dropped.
    text: str | None
    reason: str | None
    # Chess terms the sentence names that no fact backs, for the #459 ranking.
    unmatched_terms: tuple[str, ...] = field(default=())


def _origins(sans: Sequence[str]) -> tuple[chess.Board, Origins]:
    board = chess.Board()
    origins = Origins(board)
    for san in sans:
        move = board.parse_san(san)
        origins.push(board, move)
        board.push(move)
    return board, origins


def _line_moves(
    board: chess.Board, origins: Origins, line: Sequence[str]
) -> list[tuple[chess.Board, Origins, chess.Move]]:
    """Each move of ``line`` with the board and piece origins before it."""
    scratch = board.copy(stack=False)
    tracked = copy.deepcopy(origins)
    out = []
    for uci in line:
        move = chess.Move.from_uci(uci)
        if move not in scratch.legal_moves:
            raise ValueError(f"illegal line move {uci} in {scratch.fen()}")
        out.append((scratch.copy(stack=False), copy.deepcopy(tracked), move))
        tracked.push(scratch, move)
        scratch.push(move)
    return out


def position_facts(sans: Sequence[str], ply: int, view: EngineView) -> PositionFacts:
    """The facts for the move at ``ply`` (1-based) of a game given in SAN."""
    if ply < 1 or ply > len(sans):
        raise ValueError(f"ply {ply} outside the game's {len(sans)} moves")
    board, origins = _origins(sans[: ply - 1])
    played = board.parse_san(sans[ply - 1])
    role = role_facts(
        board.fen(),
        played.uci(),
        view.best_line[0],
        best_line=view.best_line,
        played_line=view.played_line,
    )
    after = board.copy(stack=False)
    after.push(played)
    after_origins = copy.deepcopy(origins)
    after_origins.push(board, played)
    concepts = [
        *move_concepts(board, played),
        *position_concepts(after),
        *intent_concepts(
            board,
            played,
            view.played_line,
            move_score_cp=view.played_score_cp,
            best_score_cp=view.best_score_cp,
            threat_line=view.threat_line,
        ),
    ]
    positions = (
        *_line_moves(board, origins, (played.uci(),)),
        *_line_moves(board, origins, view.best_line),
        *_line_moves(after, after_origins, view.played_line),
    )
    return PositionFacts(board.fen(), played.uci(), role, tuple(concepts), positions)


def _resolve(token: str, sentence: str, facts: PositionFacts) -> set[str]:
    """The numbered SAN of every fact move ``token`` names."""
    number = _NUMBER.match(token)
    text = token[number.end() :].lstrip(" .") if number else token
    sides = {chess.WHITE if side == "White" else chess.BLACK for side in _SIDE.findall(sentence)}
    named: set[str] = set()
    for board, origins, move in facts.positions:
        if number is None and len(sides) == 1 and board.turn not in sides:
            continue
        if number is not None:
            # "21. PxP" names White's 21st move, "21...PxP" Black's.
            if board.fullmove_number != int(number.group(1)):
                continue
            black = number.group(2) is not None
            if black != (board.turn == chess.BLACK):
                continue
        try:
            parsed = parse_move(board, text, origins.squares)
        except DescriptiveError:
            continue
        if parsed.move == move:
            named.add(numbered_san(board, move))
    return named


def _unmatched_terms(text: str, facts: str) -> tuple[str, ...]:
    low, source = text.lower(), facts.lower()
    return tuple(
        word
        for word in (*TERMS, *JUDGEMENT_WORDS, *CLAIM_WORDS)
        if re.search(r"\b" + word, low) and not re.search(r"\b" + word, source)
    )


def _game_move(facts: PositionFacts) -> str:
    board, _, move = facts.positions[0]
    return numbered_san(board, move)


def _check(text: str, facts: PositionFacts, view: EngineView) -> str | None:
    """The first reason ``text`` (already in SAN) fails, or ``None`` when it passes."""
    if any(name not in _NOT_A_NAME for name in _NAME.findall(text)) or _YEAR.search(text):
        return "name or date"
    if _NEGATION.search(text):
        return "negation"
    plays = len(_PLAY_VERB.findall(text))
    if plays and len(_COUNTERFACTUAL.findall(text)) not in (0, plays):
        # "played X but could have played Y": the game move and an alternative in one.
        return "played move is not the game move"
    if plays and not _COUNTERFACTUAL.search(text):
        game = _game_move(facts).rstrip("+#")
        if any(move.rstrip("+#") != game for move in _NUMBERED_SAN.findall(text)):
            return "played move is not the game move"
    if _MATE.search(text) and not (view.best_mate or view.played_mate):
        return "engine: no mate"
    if _DRAW.search(text) and abs(view.played_score_cp) >= DRAW_CP:
        return "engine: not a draw"
    if (
        _WINNING.search(text)
        and abs(view.played_score_cp) < LEVEL_CP
        and abs(view.best_score_cp) < LEVEL_CP
    ):
        return "engine: level"
    problems = violations(text, facts.text)
    low, source = text.lower(), facts.text.lower()
    if any(problem.startswith("term ") for problem in problems) or any(
        re.search(r"\b" + word, low) and not re.search(r"\b" + word, source) for word in CLAIM_WORDS
    ):
        return "unsupported term"
    if judgement_problems(text, facts.text):
        return "unsupported judgement"
    if problems:
        return "unsupported piece or square"
    if CAPTURE_WORDS.search(text) and not fact_takes(facts.text, facts.kinds):
        return "capture without a capture fact"
    if pairing_problems(text, facts.text, facts.sides):
        return "piece on the wrong square"
    fact_squares = set(SQUARE.findall(facts.text))
    fact_moves = {move.rstrip("+#") for move in MOVE.findall(facts.text)}
    squares = set(SQUARE.findall(text))
    moves = {move.rstrip("+#") for move in MOVE.findall(text)}
    if not (fact_squares & squares or fact_moves & moves):
        return "no move or square of the facts"
    return None


def to_san(sentence: str, facts: PositionFacts) -> tuple[str | None, str | None]:
    """The sentence with each descriptive move written as its fact's SAN, or why not."""
    out: list[str] = []
    last = 0
    for match in DESCRIPTIVE_MOVE.finditer(sentence):
        named = _resolve(match.group(0).strip(), sentence, facts)
        if not named:
            return None, "move not in the facts"
        if len(named) > 1:
            return None, "move ambiguous"
        out += [sentence[last : match.start()], named.pop()]
        last = match.end()
    out.append(sentence[last:])
    text = "".join(out)
    rest = SQUARE.sub("", MOVE.sub("", text))
    if DESCRIPTIVE_SQUARE.search(rest):
        return None, "descriptive square or piece"
    return text, None


def _has_chess(sentence: str) -> bool:
    return bool(
        DESCRIPTIVE_MOVE.search(sentence)
        or DESCRIPTIVE_SQUARE.search(sentence)
        or re.search(r"\b(king|queen|rook|bishop|knight|pawn)s?\b", sentence, re.IGNORECASE)
    )


def judge(sentence: str, facts: PositionFacts, view: EngineView) -> Verdict:
    """Keep, trim or drop one annotator sentence against its position's facts."""
    if not _has_chess(sentence):
        return Verdict("dropped", None, "no chess content", _unmatched_terms(sentence, facts.text))
    text, reason = to_san(sentence, facts)
    if text is None:
        return Verdict("dropped", None, reason, _unmatched_terms(sentence, facts.text))
    terms = _unmatched_terms(text, facts.text)
    if len(_SENTENCE_END.findall(text.strip())) > 1:
        return Verdict("dropped", None, "more than one sentence", terms)
    reason = _check(text, facts, view)
    if reason is None:
        return Verdict("kept", text, None, terms)
    if reason in ("unsupported term", "unsupported judgement") and not _DEPENDENT.match(text):
        # Only at the first boundary, so the kept part is the whole main clause.
        cut = _CLAUSE.search(text)
        if cut is not None:
            part = text[: cut.start()].rstrip(" ,;") + "."
            if len(part.split()) >= 4 and _check(part, facts, view) is None:
                return Verdict("trimmed", part, reason, terms)
    return Verdict("dropped", None, reason, terms)


def heldout(source: str, game: str, every: int) -> bool:
    """Whether a game is in the held-out split: fixed by game, before any training."""
    return zlib.crc32(f"{source}:{game}".encode()) % every == 0


__all__ = [
    "DESCRIPTIVE_MOVE",
    "DESCRIPTIVE_SQUARE",
    "REASONS",
    "EngineView",
    "PositionFacts",
    "Verdict",
    "heldout",
    "judge",
    "position_facts",
    "to_san",
]

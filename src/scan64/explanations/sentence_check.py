"""Code checks on one narrator sentence written for one fact (#454, part of #457).

The voice pipeline asks a small model to rewrite one code-written fact as one
lively sentence. Before a player sees that sentence, code checks it against its
own fact:

- it names no move, square, piece or chess term that the fact does not contain;
- it keeps at least one of the fact's moves or squares;
- it describes a piece as taken only when the fact says a piece is taken (an
  attacked piece is never "taken");
- it is one sentence.

``repair`` removes a "!" written directly after the played move, and
``colour_words`` finds the words a sentence adds to its fact, so the pipeline
can stop the same picture word from repeating across sentences.

The rules are the ones measured by the narrator voice benchmark
(``benchmarks/narrator/voice/check.py``, runs 6-9 in #457 and #462).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

SQUARE = re.compile(r"\b[a-h][1-8]\b")
MOVE = re.compile(
    r"\b(?:O-O(?:-O)?|[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8]|[a-h][1-8](?==|\b))"
)
PIECES = re.compile(r"\b(king|queen|rook|bishop|knight|pawn)s?\b", re.IGNORECASE)
# Chess judgements and terms a sentence may use only when its fact uses them.
TERMS: tuple[str, ...] = (
    "sacrific",
    "initiative",
    "tempo",
    "transpos",
    "compensat",
    "prophyla",
    "blockad",
    "zugzwang",
    "fork",
    "develop",
    "material",
    "advantage",
    "decisive",
    "counterattack",
    "positional",
    "structure",
    "space",
    "endgame",
    "promot",
    "exchange",
    "castl",
    "file",
    "diagonal",
    "pin",
    "mate",
    "centre",
    "stalemate",
    "draw",
    "forced",
    "only move",
)
# Words that say a piece changes hands. A sentence may use them only when its
# fact says a piece is taken: "7.Qe2 snags the queen" for a queen that is only
# attacked teaches a false lesson.
CAPTURE_WORDS = re.compile(
    r"\b(take[sn]?|taking|took|captur\w*|snag\w*|snatch\w*|grab\w*|seiz\w*|swallow\w*|"
    r"gobbl\w*|devour\w*|eat[s]?|eaten|ate|pick\w* (up|off)|carr(y|ies|ied) off|collect\w*|"
    r"claim\w*|win|wins|winning|won|cast\w*|steal\w*|stole|stolen|nab\w*)\b",
    re.IGNORECASE,
)
# Judgements a sentence may use only when its fact uses them: the model must not
# call a move better, stronger or more spirited on its own (#454 blind judge).
JUDGEMENT_WORDS: tuple[str, ...] = (
    "better",
    "best",
    "worse",
    "worst",
    "stronger",
    "strongest",
    "weaker",
    "weakest",
    "sharper",
    "quicker",
    "faster",
    "livelier",
    "spirited",
    "grander",
    "superior",
    "inferior",
    "footing",
    "direct route",
    "mistake",
    "blunder",
    "error",
    "brilliant",
    "clever",
    "wise",
    "wiser",
    "winning",
    "losing",
)
# "more daring", "less safe"; "<word>er path" such as "a safer path".
_MORE_ADJECTIVE = re.compile(r"\b(more|less)\s+(?!than\b|and\b|of\b)[a-z]+", re.IGNORECASE)
_ER_PATH = re.compile(r"\b[a-z]+er\s+(path|route|road|way)\b", re.IGNORECASE)
# "<piece> on <square>", with an optional side: "Black's queen on g5", "the white king on e8".
_PIECE_ON = re.compile(
    r"\b(?:(white|black)(?:'s)?\s+)?(king|queen|rook|bishop|knight|pawn)\s+on\s+([a-h][1-8])\b",
    re.IGNORECASE,
)
_LETTERS = {"K": "king", "Q": "queen", "R": "rook", "B": "bishop", "N": "knight"}
# Same word, other form: these never change the fact.
_SAME: tuple[tuple[str, str], ...] = (
    (r"\bcenter", "centre"),
    (r"\bopen(s|ed|ing)?\b", "open"),
    (r"\bcheckmate", "mate"),
    (r"\bundefended|\bunprotected|\bno defender", "undefended"),
    (r"\bprotect(s|ed|ing)?\b|\bguard(s|ed|ing)?\b", "defend"),
)
# Words that never count as a repeated picture word.
STOP_WORDS = frozenset(
    "a after all also an and any are as at be been before black black's but by can could "
    "first for from had has have here in instead is it its just move moves no not now of on "
    "one or play played plays so some still than that the then there this to too two very "
    "was were while white white's will with would".split()
)


def _normal(text: str) -> str:
    for pattern, word in _SAME:
        text = re.sub(pattern, word, text)
    return text


def violations(text: str, fact: str) -> list[str]:
    """Squares, moves, pieces and terms in ``text`` that ``fact`` does not contain."""
    moves = MOVE.findall(fact)
    allowed_squares = set(SQUARE.findall(fact)) | {
        move.rstrip("+#")[-2:] for move in moves if move[0] != "O"
    }
    allowed_pieces = {piece.lower() for piece in PIECES.findall(fact)}
    allowed_pieces |= {_LETTERS[move[0]] for move in moves if move[0] in _LETTERS}
    if any(move[0].islower() for move in moves):
        allowed_pieces.add("pawn")
    if any(move.startswith("O-O") for move in moves):
        allowed_pieces |= {"king", "rook"}
    problems = [
        f"square {square}" for square in sorted(set(SQUARE.findall(text)) - allowed_squares)
    ]
    fact_moves = {move.rstrip("+#") for move in moves}
    problems.extend(
        f"move {move}"
        for move in sorted(set(MOVE.findall(text)))
        if move.rstrip("+#") not in fact_moves and not SQUARE.fullmatch(move)
    )
    problems.extend(
        f"piece {piece}"
        for piece in sorted({piece.lower() for piece in PIECES.findall(text)} - allowed_pieces)
    )
    low, source = _normal(text.lower()), _normal(fact.lower())
    problems.extend(
        f"term {term}"
        for term in TERMS
        if re.search(r"\b" + term, low) and not re.search(r"\b" + term, source)
    )
    if "$" in text or "\\" in text:
        problems.append("markup")
    return problems


def _tokens(text: str) -> tuple[set[str], set[str]]:
    """Squares named (including each move's target square) and moves."""
    moves = {move.rstrip("+#") for move in MOVE.findall(text)}
    return set(SQUARE.findall(text)) | {move[-2:] for move in moves if move[0] != "O"}, moves


def fact_takes(fact: str, relation_kinds: Iterable[str] = ()) -> bool:
    """True when the fact says a piece is taken: a ``takes`` relation, a capture word or ``x``."""
    if "takes" in set(relation_kinds):
        return True
    if CAPTURE_WORDS.search(fact):
        return True
    return any("x" in move for move in MOVE.findall(fact))


def judgement_problems(sentence: str, fact: str) -> list[str]:
    """Comparative or judgement words in ``sentence`` that ``fact`` does not contain."""
    low, source = sentence.lower(), fact.lower()
    problems = [
        f"judgement {word}"
        for word in JUDGEMENT_WORDS
        if re.search(r"\b" + word + r"\b", low) and not re.search(r"\b" + word + r"\b", source)
    ]
    problems.extend(
        f"judgement {match.group(0).lower()}"
        for pattern in (_MORE_ADJECTIVE, _ER_PATH)
        for match in pattern.finditer(sentence)
        if match.group(0).lower() not in source
    )
    return problems


def piece_pairs(text: str) -> list[tuple[str | None, str, str]]:
    """Each "<piece> on <square>" with its side when named: ("black", "queen", "g5")."""
    return [
        (side.lower() if side else None, piece.lower(), square)
        for side, piece, square in _PIECE_ON.findall(text)
    ]


def pairing_problems(sentence: str, fact: str, sides: Mapping[str, str] | None = None) -> list[str]:
    """A piece named on a square the fact does not pair it with, or with the wrong side.

    ``sides`` maps a square to the side of the piece on it ("white" or "black"),
    from the fact's relations; a side the fact text names counts too.
    """
    fact_pairs = piece_pairs(fact)
    pieces_on = {(piece, square) for _, piece, square in fact_pairs}
    known = dict(sides or {})
    known.update({square: side for side, _, square in fact_pairs if side})
    problems = []
    for side, piece, square in piece_pairs(sentence):
        if (piece, square) not in pieces_on:
            problems.append(f"pairs {piece} with {square}")
        elif side and square in known and known[square] != side:
            problems.append(f"calls the {piece} on {square} {side}")
    return problems


def sentence_problems(
    sentence: str,
    fact: str,
    relation_kinds: Iterable[str] = (),
    sides: Mapping[str, str] | None = None,
) -> list[str]:
    """Problems with one sentence written for one fact; an empty list means it passes."""
    problems = violations(sentence, fact)
    problems.extend(judgement_problems(sentence, fact))
    problems.extend(pairing_problems(sentence, fact, sides))
    fact_squares, fact_moves = _tokens(fact)
    squares, moves = _tokens(sentence)
    if (fact_squares or fact_moves) and not (fact_squares & squares or fact_moves & moves):
        problems.append("keeps no move or square of the fact")
    if CAPTURE_WORDS.search(sentence) and not fact_takes(fact, relation_kinds):
        problems.append("says a piece is taken; the fact takes nothing")
    # Two end marks are allowed, for a move written as "1.Re1!" inside the sentence.
    if len(re.findall(r"[.!?](\s|$)", sentence.strip())) > 2:
        problems.append("more than one sentence")
    return problems


def repair(text: str, played: str) -> str:
    """Remove a '!' (or '!!', '!?') written directly after the played move."""
    return re.sub(re.escape(played) + r"(!!|!\?|!)", played, text)


def limit_exclamations(sentences: list[str], keep: int = 1) -> list[str]:
    """Keep the first ``keep`` "!" of a comment.

    Each item is one sentence, so a later "!" at its end becomes a full stop,
    and a later "!" inside it (after a move, "1.Re1!") is removed.
    """
    kept = 0
    out = []
    for sentence in sentences:
        sentence = sentence.rstrip()
        chars: list[str] = []
        for index, char in enumerate(sentence):
            if char != "!":
                chars.append(char)
                continue
            if kept < keep:
                kept += 1
                chars.append(char)
            elif index == len(sentence) - 1 and not (chars and chars[-1] in ".!?"):
                chars.append(".")
        out.append("".join(chars))
    return out


def finish(sentence: str) -> str:
    """End a sentence with a full stop when it has no end mark."""
    sentence = sentence.strip()
    return sentence if sentence.endswith((".", "!", "?")) else sentence + "."


def colour_words(sentence: str, fact: str) -> set[str]:
    """Words longer than 3 letters that the sentence adds to its fact."""
    fact_words = set(re.findall(r"[a-z']+", fact.lower()))
    words = set(re.findall(r"[a-z']+", sentence.lower()))
    return {word for word in words - fact_words - STOP_WORDS if len(word) > 3}


__all__ = [
    "CAPTURE_WORDS",
    "JUDGEMENT_WORDS",
    "judgement_problems",
    "limit_exclamations",
    "pairing_problems",
    "piece_pairs",
    "colour_words",
    "fact_takes",
    "finish",
    "repair",
    "sentence_problems",
    "violations",
]

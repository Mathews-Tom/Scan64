"""Code checks on model text: the word check, the per-sentence check, the "!" repair, repetition.

The word check (``violations``) scores whole comments in runs 1-3 and checks each sentence in
runs 6-9. It flags a square, a move, a piece word or a chess term that the facts do not contain.
"""

import re

from benchmarks.narrator.voice.data import STOP_RUN8

SQUARE = re.compile(r"\b[a-h][1-8]\b")
MOVE = re.compile(
    r"\b(?:O-O(?:-O)?|[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8]|[a-h][1-8](?==|\b))"
)
PIECES = re.compile(r"\b(king|queen|rook|bishop|knight|pawn)s?\b", re.I)
# Chess judgement that no fact here supports: a violation unless the facts use it.
TERMS = [
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
]
LETTERS = {"K": "king", "Q": "queen", "R": "rook", "B": "bishop", "N": "knight"}
# Same word, other form: these never change the fact.
SAME = [
    (r"\bcenter", "centre"),
    (r"\bopen(s|ed|ing)?\b", "open"),
    (r"\bcheckmate", "mate"),
    (r"\bundefended|\bunprotected|\bno defender", "undefended"),
    (r"\bprotect(s|ed|ing)?\b|\bguard(s|ed|ing)?\b", "defend"),
]
STOP = set(STOP_RUN8)


def normal(text: str) -> str:
    for pattern, word in SAME:
        text = re.sub(pattern, word, text)
    return text


def violations(text: str, facts: list[str], position: str = "") -> list[str]:
    """Squares, moves, pieces and terms in ``text`` that the facts (and position) do not contain."""
    source = " ".join(facts) + " " + position
    moves = MOVE.findall(" ".join(facts))
    allowed_squares = set(SQUARE.findall(source)) | {
        m.rstrip("+#")[-2:] for m in moves if m[0] != "O"
    }
    allowed_pieces = {p.lower() for p in PIECES.findall(source)}
    allowed_pieces |= {LETTERS[m[0]] for m in moves if m[0] in LETTERS}
    if any(m[0].islower() for m in moves):
        allowed_pieces.add("pawn")
    if any(m.startswith("O-O") for m in moves):
        allowed_pieces |= {"king", "rook"}
    bad = []
    for sq in set(SQUARE.findall(text)) - allowed_squares:
        bad.append(f"square {sq}")
    fact_moves = {m.rstrip("+#") for m in moves}
    for mv in set(MOVE.findall(text)):
        if mv.rstrip("+#") not in fact_moves and not SQUARE.fullmatch(mv):
            bad.append(f"move {mv}")
    for piece in {p.lower() for p in PIECES.findall(text)} - allowed_pieces:
        bad.append(f"piece {piece}")
    low, src = normal(text.lower()), normal(source.lower())
    for term in TERMS:
        if re.search(r"\b" + term, low) and not re.search(r"\b" + term, src):
            bad.append(f"term {term}")
    if "$" in text or "\\" in text:
        bad.append("markup")
    return bad


def tokens(text: str) -> tuple[set[str], set[str]]:
    """Squares named (including each move's target square) and moves."""
    moves = {m.rstrip("+#") for m in MOVE.findall(text)}
    return set(SQUARE.findall(text)) | {m[-2:] for m in moves if m[0] != "O"}, moves


def sentence_ok(sentence: str, fact: str) -> list[str]:
    """Problems with one sentence written for one fact; an empty list means it passes.

    No move, square, piece or term outside the fact; it keeps at least one of the fact's moves or
    squares; it is at most one sentence (two end marks allowed, for a move such as "1.Re1!").
    """
    bad = violations(sentence, [fact])
    f_sq, f_mv = tokens(fact)
    s_sq, s_mv = tokens(sentence)
    if (f_sq or f_mv) and not ((f_sq & s_sq) or (f_mv & s_mv)):
        bad.append("keeps no move or square of the fact")
    if len(re.findall(r"[.!?](\s|$)", sentence.strip())) > 2:
        bad.append("more than one sentence")
    return bad


def repair(text: str, played: str) -> tuple[str, int]:
    """Remove a '!' (or '!!', '!?') written directly after the played move."""
    return re.subn(re.escape(played) + r"(!!|!\?|!)", played, text)


def finish(sentence: str) -> str:
    """End a sentence with a full stop when it has no end mark."""
    sentence = sentence.strip()
    return sentence if sentence.endswith((".", "!", "?")) else sentence + "."


def colour_words(sentence: str, fact: str) -> set[str]:
    """Content words of the sentence that its fact does not contain (longer than 3 letters)."""
    fact_words = set(re.findall(r"[a-z']+", fact.lower()))
    words = set(re.findall(r"[a-z']+", sentence.lower()))
    return {w for w in words - fact_words - STOP if len(w) > 3}


def markers(text: str) -> dict[str, float]:
    """Style markers of a comment: sentences, words, exclamations, questions, 'you'."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    n_words = len(text.split())
    return {
        "sentences": len(sentences),
        "words": n_words,
        "words_per_sentence": n_words / max(1, len(sentences)),
        "exclamations": text.count("!") - len(re.findall(r"[a-h1-8]!", text)),
        "questions": text.count("?"),
        "you": len(re.findall(r"\byou(r)?\b", text, re.I)),
    }

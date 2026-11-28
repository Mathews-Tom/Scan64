"""The code metric: does an explanation state the root cause and the turning point (#403)?

It reads the finished sentences, not the pipeline's own bookkeeping, so it can fail when the
pipeline passes a sentence that lost the theme. It needs the theme's own details in one passage:
pieces, squares, and for pawns the kind of weakness. It has no list of synonyms or pictures.
"""

import re
from collections.abc import Sequence
from typing import Any

from scan64.explanations.themes import ThemeFacts, TurningPointFacts

TURN_WORDS = re.compile(
    r"\b(turn\w*|tilt\w*|tip(s|ped|ping)?|swing\w*|swung|shift\w*|slid\w*|slip\w*|level|"
    r"balance\w*)\b",
    re.IGNORECASE,
)


def _word(word: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(word)}s?\b", text, re.IGNORECASE) is not None


def _move(san: str, text: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(san.rstrip('+#'))}(?![A-Za-z0-9])"
    return re.search(pattern, text) is not None


def states_theme(theme: ThemeFacts, passage: str) -> bool:
    """One passage holds what identifies the theme.

    unsafe_king: "king", its square, and an open file or an attacker's square.
    weak_pawns: "pawn", every weak pawn's square, every kind of weakness.
    passive_pieces, lagging_development: each learner piece and its square.
    """
    if theme.key == "weak_pawns":
        needed = ["pawn", *theme.squares, *theme.weaknesses]
        return all(_word(word, passage) for word in needed)
    mine = [piece for piece in theme.pieces if piece.owner == "learner"]
    if not all(_word(p.piece, passage) and _word(p.square, passage) for p in mine):
        return False
    if theme.key != "unsafe_king":
        return bool(mine)
    # "the d-file", "the d- and e-files".
    files = [rf"\b{name}-(file|\s)" for name in theme.files]
    attackers = [p.square for p in theme.pieces if p.owner == "opponent"]
    return any(re.search(f, passage, re.IGNORECASE) for f in files) or any(
        _word(square, passage) for square in attackers
    )


def states_turn(point: TurningPointFacts, passage: str) -> bool:
    """One passage holds the turning move and a word for the turn of the game."""
    return _move(point.san, passage) and TURN_WORDS.search(passage) is not None


def coverage(text: str, key_moves: Sequence[str]) -> float:
    """Share of the key moves (played, best, the engine's reply) that the text states."""
    return sum(_move(san, text) for san in key_moves) / len(key_moves)


def rates(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One arm's rates, from its stored records only."""
    accepted = [r for r in records if r["error"] is None]
    sentences = [s for r in accepted for s in r["sentences"]]
    written = [s for s in sentences if s["calls"]]
    themed = turned = both = theme_hits = turn_hits = both_hits = theme_model = 0
    for record in accepted:
        theme = ThemeFacts.model_validate(record["theme"]) if record["theme"] else None
        point = TurningPointFacts.model_validate(record["turn"]) if record["turn"] else None
        named = [s for s in record["sentences"] if theme and states_theme(theme, s["text"])]
        hit_turn = bool(point) and any(states_turn(point, s["text"]) for s in record["sentences"])
        themed += theme is not None
        theme_hits += bool(named)
        theme_model += any(s["source"] == "model" for s in named)
        turned += point is not None
        turn_hits += hit_turn
        if theme and point:
            both += 1
            both_hits += bool(named) and hit_turn
    return {
        "records": len(records),
        "accepted": len(accepted),
        # Grounding pass: sentences the model wrote that the code check let through.
        "sentences": len(written),
        "grounded": sum(s["source"] == "model" for s in written),
        "first_try": sum(s["source"] == "model" and s["calls"] == 1 for s in written),
        "coverage": sum(coverage(r["text"], r["key_moves"]) for r in accepted)
        / max(1, len(accepted)),
        "themed": themed,
        "theme_stated": theme_hits,
        "theme_by_model": theme_model,
        "turned": turned,
        "turn_stated": turn_hits,
        "both": both,
        "both_stated": both_hits,
    }

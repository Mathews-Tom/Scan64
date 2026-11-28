"""Split a comment into sentences, without changing a character of it.

A comment is split only at whitespace after ".", "!" or "?" when the next word
starts a sentence. The pieces joined with single spaces are the comment.
Abbreviations ("Dr.", "ch.", "e.g.") and move numbers ("8. KtxP") do not end
a sentence.
"""

from __future__ import annotations

import re

_ABBREVIATIONS = frozenset(
    {
        "Dr",
        "Mr",
        "Mrs",
        "St",
        "No",
        "Nos",
        "ch",
        "e.g",
        "i.e",
        "cf",
        "p",
        "pp",
        "viz",
        "vs",
        "etc",
        "ibid",
    }
)
# A space before a word that could start a sentence; ``_ends_sentence`` decides.
_BOUNDARY = re.compile(r" (?=[\"'(\[]?[A-Z])")


def _ends_sentence(before: str) -> bool:
    word = before.rsplit(" ", 1)[-1].rstrip("\"')]")
    if not word.endswith((".", "!", "?")):
        return False
    stem = word.rstrip(".!?")
    if word.endswith(".") and (stem in _ABBREVIATIONS or stem.lstrip("(") in _ABBREVIATIONS):
        return False
    # A move number ("8.") or a lone initial ("J.") is not the end of a sentence.
    if word.endswith(".") and (stem.lstrip("(").isdigit() or (len(stem) == 1 and stem.isupper())):
        return False
    return True


def split_sentences(comment: str) -> list[str]:
    """The sentences of ``comment``, a string with single spaces between words."""
    if comment != " ".join(comment.split()):
        raise ValueError("a comment must have single spaces between words")
    sentences: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(comment):
        if _ends_sentence(comment[start : match.start()]):
            sentences.append(comment[start : match.start()])
            start = match.end()
    sentences.append(comment[start:])
    if " ".join(sentences) != comment:
        raise AssertionError("sentences do not rebuild the comment")
    return [sentence for sentence in sentences if sentence]

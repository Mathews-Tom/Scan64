"""Games and the comments between their moves, read from a book's plain text.

The books set games in two columns: a numbered line holds White's move and,
after a wide gap, Black's ("7. B - Q 3" alone, then "7. ........ Kt × Kt"
after a comment). Prose between move lines is the annotator's comment on the
position after the last move above it. A reader is configured per book by its
game header, its end-of-game separator and the lines it skips (diagrams,
illustration markers). Moves stay text here; ``corpus`` replays them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_START = re.compile(r"^\*\*\* ?START OF (?:THE|THIS) PROJECT GUTENBERG")
_END = re.compile(r"^\*\*\* ?END OF (?:THE|THIS) PROJECT GUTENBERG")
# "7. B - Q 3"; "4 ........ P × P" (a dot the printer dropped before a placeholder).
_MOVE_LINE = re.compile(r"^\s{4,}(?P<number>\d+)(?:\.\s*|\s+(?=\.{2,}|…))(?P<rest>\S.*?)\s*$")
_BLACK_ONLY = re.compile(r"^\s{20,}(?P<rest>\S.*?)\s*$")
_PLACEHOLDER = re.compile(r"^(?:\.{2,}|…+)$")
_RESULT = re.compile(r"^(?P<result>Resigns|Drawn|Draws|Draw|Draw agreed|Mate)\.?$", re.IGNORECASE)
_PAGE_MARKER = re.compile(r"\{\d+\}")
_MARKUP = re.compile(r"^\{\d+\}\s*|/\*|\*/")
_DIAGRAM = re.compile(r"^\s*(?:\d\s*)?\|.*\|\s*$|^\s*-{5,}\s*$|^\s*(?:[a-h]\s+){7}[a-h]\s*$")
_EVENT = re.compile(r"^\s*\((?P<event>[^()]+)\)\s*$")
_PLAYERS = re.compile(r"^\s*White:\s*(?P<white>.+?)\.?\s+Black:\s*(?P<black>.+?)\.?\s*$")
# A move text: piece letters, squares, captures and marks; prose has lowercase words.
_MOVE_TEXT = re.compile(r"^(?:[KQRBPNt\d\s\-×xX()=!?.,+#]|KT|ch|e\.p\.|double|dis|Castles|O|0)+$")


class BookFormatError(ValueError):
    """The text breaks the layout its reader expects."""


@dataclass(frozen=True)
class BookLayout:
    """What marks a game in one book."""

    # A line that starts a game; group "number" names it.
    header: re.Pattern[str]
    # A line that ends a game (a separator); ``None`` when only a header or result does.
    separator: re.Pattern[str] | None
    # After the result line, stop: the prose that follows belongs to the chapter.
    stop_at_result: bool
    # Lines that are not text: illustration markers, diagrams.
    skip: tuple[re.Pattern[str], ...] = ()


@dataclass
class BookGame:
    """One game as the book prints it: move texts and the comments between them."""

    number: str
    heading: str
    white: str | None = None
    black: str | None = None
    # "(Match, 1909)": the event line under the heading.
    event: str | None = None
    # Move texts in order, White's first.
    moves: list[str] = field(default_factory=list)
    # (ply, text): the comment on the position after ``ply`` moves (0 = before move 1).
    comments: list[tuple[int, str]] = field(default_factory=list)
    result: str | None = None
    # Why the game cannot be read; set instead of raising so the book reads on.
    problem: str | None = None


def _body(text: str) -> list[str]:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if _START.match(line)]
    ends = [i for i, line in enumerate(lines) if _END.match(line)]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise BookFormatError("expected one Project Gutenberg START and END marker")
    return lines[starts[0] + 1 : ends[0]]


def _is_move_text(text: str) -> bool:
    return bool(_MOVE_TEXT.match(text)) and (
        any(c in text for c in "KQRBPNO0") or text.startswith("Castles")
    )


class _Reader:
    def __init__(self, layout: BookLayout) -> None:
        self.layout = layout
        self.games: list[BookGame] = []
        self.game: BookGame | None = None
        self.paragraph: list[str] = []
        self.done = False

    # --- comments -------------------------------------------------------
    def flush(self) -> None:
        if self.game is None or not self.paragraph:
            self.paragraph = []
            return
        text = " ".join(_PAGE_MARKER.sub(" ", " ".join(self.paragraph)).split())
        self.paragraph = []
        if text:
            self.game.comments.append((len(self.game.moves), text))

    # --- games ----------------------------------------------------------
    def close(self) -> None:
        self.flush()
        if self.game is not None:
            self.games.append(self.game)
        self.game = None
        self.done = False

    def fail(self, problem: str) -> None:
        assert self.game is not None
        if self.game.problem is None:
            self.game.problem = problem

    def expected(self) -> tuple[int, bool]:
        """(move number, White to move) for the next move."""
        assert self.game is not None
        plies = len(self.game.moves)
        return plies // 2 + 1, plies % 2 == 0

    def play(self, part: str) -> None:
        assert self.game is not None
        result = _RESULT.match(part.strip())
        if result is not None:
            self.game.result = result.group("result")
            self.done = self.layout.stop_at_result
            return
        if not _is_move_text(part):
            self.fail(f"move {self.expected()[0]}: {part!r} is not a move text")
            return
        self.game.moves.append(part.strip())

    def move_line(self, number: int, rest: str) -> None:
        assert self.game is not None
        self.flush()
        expected, white = self.expected()
        parts = [part for part in re.split(r"\s{2,}", rest) if part]
        if number != expected:
            self.fail(f"move number {number} where {expected} was expected")
            return
        if white:
            if _PLACEHOLDER.match(parts[0]):
                self.fail(f"move {number}: White's move is missing")
                return
            self.play(parts[0])
            for part in parts[1:2]:
                self.play(part)
            for part in parts[2:]:
                self.fail(f"move {number}: unexpected text {part!r}")
        else:
            if not _PLACEHOLDER.match(parts[0]) or len(parts) != 2:
                self.fail(f"move {number}: expected '{number}. ... <Black's move>'")
                return
            self.play(parts[1])

    def line(self, line: str) -> None:
        # Gutenberg's "/* ... */" marks a preformatted block; a page marker can
        # open a line. Neither is text.
        line = _MARKUP.sub(lambda match: " " * len(match.group(0)), line).rstrip()
        header = self.layout.header.match(line)
        if header is not None:
            self.close()
            self.game = BookGame(number=header.group("number"), heading=line.strip())
            return
        if self.game is None:
            return
        if self.layout.separator is not None and self.layout.separator.match(line):
            self.close()
            return
        if any(pattern.match(line) for pattern in self.layout.skip) or _DIAGRAM.match(line):
            return
        if self.done:
            return
        if not line.strip():
            self.flush()
            return
        players = _PLAYERS.match(line)
        if players is not None and not self.game.moves and self.game.white is None:
            self.flush()
            self.game.white, self.game.black = players.group("white"), players.group("black")
            return
        event = _EVENT.match(line)
        if (
            event is not None
            and not self.game.moves
            and self.game.event is None
            and not self.paragraph
        ):
            self.game.event = event.group("event")
            return
        move = _MOVE_LINE.match(line)
        first = re.split(r"\s{2,}", move.group("rest"))[0] if move is not None else ""
        # "40. Resigns.": the result on a numbered line.
        if move is not None and (
            _is_move_text(first) or _PLACEHOLDER.match(first) or _RESULT.match(first)
        ):
            self.move_line(int(move.group("number")), move.group("rest"))
            return
        black = _BLACK_ONLY.match(line)
        if black is not None and self.game.moves and not self.expected()[1]:
            if _is_move_text(black.group("rest")) or _RESULT.match(black.group("rest")):
                self.flush()
                self.play(black.group("rest"))
                return
        if line[:1].isspace() and _RESULT.match(line.strip()):
            self.flush()
            self.play(line.strip())
            return
        self.paragraph.append(line.strip())


def read_games(text: str, layout: BookLayout) -> list[BookGame]:
    """Every game ``layout`` finds in a Project Gutenberg plain-text book."""
    reader = _Reader(layout)
    for line in _body(text):
        reader.line(line)
    reader.close()
    return reader.games

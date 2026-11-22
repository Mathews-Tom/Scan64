"""Bounded, owner-owned opening trees and verified decision reviews (no rewards)."""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import chess
import chess.pgn
from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, Column, ForeignKeyConstraint, String, UniqueConstraint
from sqlmodel import Field, SQLModel

MAX_PGN_BYTES = 256 * 1024
MAX_RECORDS = 16
MAX_NODES = 4096
MAX_PLY = 256
MAX_VARIATION_DEPTH = 32
MAX_FEN_COUNTER = 1_000_000
MAX_FEN_LENGTH = 120
# Unlike read_game's forgiving lexer, consume every character. Unsupported syntax
# is an input error rather than an apparently successful, truncated repertoire.
_TOKEN = re.compile(
    r'\s+|\[[A-Za-z0-9_]+\s+"(?:[^"\\\r\n]|\\["\\])*"\s*\]'
    r"|\{[^}]*\}|;[^\r\n]*|\$\d+|\d+\.(?:\.\.)?|\.\.\."
    r"|1/2-1/2|1-0|0-1|\*|[()]|[!?]+"
    r"|(?:O-O-O|O-O|0-0-0|0-0|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=?[QRBN])?)[+#]?"
)
_SAN = re.compile(r"(?:O-O|0-0|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8])")


class RepertoireNode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: int
    parent: int | None
    fen: str
    uci: str | None
    san: str | None
    children: list[int]


class RepertoireTree(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    nodes: list[RepertoireNode]


class Repertoire(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("player_id", "id", name="uq_repertoire_owner"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    player_id: str = Field(index=True)
    title: str = Field(min_length=1, max_length=200)
    pgn: str
    tree: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RepertoireAttempt(SQLModel, table=True):
    __table_args__ = (
        ForeignKeyConstraint(
            ["player_id", "repertoire_id"],
            ["repertoire.player_id", "repertoire.id"],
            name="fk_repertoire_attempt_owner",
        ),
        UniqueConstraint(
            "player_id",
            "repertoire_id",
            "node_id",
            "sequence",
            name="uq_repertoire_decision_review",
        ),
    )

    id: UUID = Field(primary_key=True)
    player_id: str = Field(index=True)
    repertoire_id: UUID
    node_id: int = Field(ge=0)
    sequence: int = Field(ge=1)
    submitted_move: str
    success: bool
    completed_at: datetime
    verification_status: Literal["verified", "historical"] = Field(
        default="verified", sa_column=Column(String, nullable=False)
    )


def repertoire_item_id(repertoire_id: UUID, node_id: int) -> str:
    """Stable exact pre-move decision identity, including distinct branch paths."""
    return f"repertoire:{repertoire_id}:{node_id}"


def validate_repertoire_fen(fen: str) -> None:
    fields = fen.split()
    if len(fen) > MAX_FEN_LENGTH or len(fields) != 6:
        raise ValueError("Repertoire FEN must have six bounded fields")
    for index, minimum in ((4, 0), (5, 1)):
        counter = fields[index]
        if (
            not counter.isascii()
            or not counter.isdecimal()
            or len(counter) > 7
            or not minimum <= int(counter) <= MAX_FEN_COUNTER
        ):
            raise ValueError("Repertoire FEN counters must be between 0/1 and 1000000")


class _StrictBuilder(chess.pgn.GameBuilder[chess.pgn.Game]):
    def visit_header(self, tagname: str, tagvalue: str) -> None:
        if tagname == "FEN":
            # read_game constructs its first board after visiting headers.
            validate_repertoire_fen(tagvalue)
        if tagname == "Result" and tagvalue not in {"1-0", "0-1", "1/2-1/2", "*"}:
            raise ValueError("PGN Result must be 1-0, 0-1, 1/2-1/2, or *")
        super().visit_header(tagname, tagvalue)

    def handle_error(self, error: Exception) -> None:
        raise ValueError(f"Invalid PGN move: {error}") from error


def _check_tokens(pgn: str) -> int:
    if not pgn.strip():
        raise ValueError("Paste a PGN containing at least one legal move")
    if len(pgn.encode("utf-8")) > MAX_PGN_BYTES:
        raise ValueError("Repertoire PGN must be at most 256 KiB")
    offset = depth = moves = 0
    record_ended = False
    while offset < len(pgn):
        match = _TOKEN.match(pgn, offset)
        if match is None:
            raise ValueError(f"Unsupported PGN text near {pgn[offset : offset + 32]!r}")
        token = match.group()
        offset = match.end()
        if token.isspace():
            if record_ended and re.search(r"\r?\n[ \t]*\r?\n", token):
                record_ended = False
            continue
        if token.startswith(("{", ";")):
            continue
        if record_ended:
            raise ValueError("No moves after a result; separate PGN records with a blank line")
        if token in {"1-0", "0-1", "1/2-1/2", "*"} and depth == 0:
            record_ended = True
        if token == "(":
            depth += 1
            if depth > MAX_VARIATION_DEPTH:
                raise ValueError("PGN variations may nest at most 32 levels")
        elif token == ")":
            depth -= 1
            if depth < 0:
                raise ValueError("PGN has an unmatched variation parenthesis")
        elif _SAN.match(token):
            moves += 1
            if moves >= MAX_NODES:
                raise ValueError("PGN must contain fewer than 4096 moves")
    if depth:
        raise ValueError("PGN has an unclosed variation")
    return moves


def _game_tree(game: chess.pgn.Game) -> RepertoireTree:
    if game.headers.get("Variant", "Standard").lower() not in {"standard", "chess", "normal"}:
        raise ValueError("Only standard chess repertoires are supported")
    board = game.board()
    if board.chess960 or not board.is_valid():
        raise ValueError("Repertoire root must be a valid standard chess position")
    validate_repertoire_fen(board.fen())
    nodes = [RepertoireNode(id=0, parent=None, fen=board.fen(), uci=None, san=None, children=[])]

    def visit(parent: chess.pgn.GameNode, parent_id: int, position: chess.Board, ply: int) -> None:
        if parent.variations and ply >= MAX_PLY:
            raise ValueError("A repertoire line may contain at most 256 plies")
        seen: set[str] = set()
        for child in parent.variations:
            if child.move is None or child.move not in position.legal_moves:
                raise ValueError("PGN contains an illegal or null move")
            uci = child.move.uci()
            if uci in seen:
                raise ValueError("Duplicate continuations at one position are not supported")
            seen.add(uci)
            after = position.copy(stack=False)
            san = after.san(child.move)
            after.push(child.move)
            validate_repertoire_fen(after.fen())
            node_id = len(nodes)
            nodes[parent_id].children.append(node_id)
            nodes.append(
                RepertoireNode(
                    id=node_id,
                    parent=parent_id,
                    fen=after.fen(),
                    uci=uci,
                    san=san,
                    children=[],
                )
            )
            visit(child, node_id, after, ply + 1)

    visit(game, 0, board, 0)
    if len(nodes) == 1:
        raise ValueError("Each PGN record must contain at least one legal move")
    return RepertoireTree(nodes=nodes)


def parse_repertoire_pgn(pgn: str) -> list[tuple[str, str, RepertoireTree]]:
    """Parse every record before a caller writes anything; preserve nested alternatives."""
    expected_moves = _check_tokens(pgn)
    stream = io.StringIO(pgn)
    records: list[tuple[str, str, RepertoireTree]] = []
    moves = 0
    while (game := chess.pgn.read_game(stream, Visitor=_StrictBuilder)) is not None:
        if len(records) >= MAX_RECORDS:
            raise ValueError("Import at most 16 PGN records at once")
        tree = _game_tree(game)
        moves += len(tree.nodes) - 1
        title = game.headers.get("Event", "").strip()
        if not title or title == "?":
            title = "Repertoire"
        normalized = game.accept(chess.pgn.StringExporter())
        if len(normalized.encode("utf-8")) > MAX_PGN_BYTES:
            raise ValueError(
                "Normalized repertoire PGN exceeds 256 KiB; reduce comments or headers"
            )
        records.append((title[:200], normalized, tree))
    if not records or moves != expected_moves:
        raise ValueError(
            "PGN contains ignored or incomplete move records; separate games with blank lines"
        )
    return records


def verify_repertoire_move(repertoire: Repertoire, node_id: int, submitted_move: str) -> bool:
    tree = RepertoireTree.model_validate(repertoire.tree)
    if not 0 <= node_id < len(tree.nodes) or not tree.nodes[node_id].children:
        raise ValueError("Choose a saved position with at least one continuation")
    node = tree.nodes[node_id]
    board = chess.Board(node.fen)
    try:
        move = board.parse_uci(submitted_move)
    except ValueError as error:
        raise ValueError("Submit a legal UCI move for the saved position") from error
    if not move or move not in board.legal_moves or move.uci() != submitted_move:
        raise ValueError("Submit a legal UCI move for the saved position")
    return any(tree.nodes[child].uci == submitted_move for child in node.children)

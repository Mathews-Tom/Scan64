"""Read-only comparison of a saved repertoire against imported standard games."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import chess
from pydantic import BaseModel
from sqlmodel import Session, col, select

from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.online_import import STANDARD_VARIANTS
from scan64.learning.repertoire import Repertoire, RepertoireTree


class RepertoireDeviation(BaseModel):
    node_id: int
    uci: str
    san: str
    played_by: Literal["you", "opponent"]
    games: int


class RepertoireDeviationReport(BaseModel):
    covered_games: int
    uncovered_games: int
    deviations: list[RepertoireDeviation]


@dataclass(slots=True)
class _Decision:
    node_id: int
    continuations: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _Departure:
    node_id: int
    uci: str
    san: str
    played_by: Literal["you", "opponent"]


def _first_departure(
    headers: Mapping[str, str],
    moves: Sequence[str],
    owner_color: str | None,
    positions: dict[str, _Decision | None],
) -> tuple[bool, _Departure | None]:
    if (
        owner_color not in ("white", "black")
        or headers.get("Variant", "Standard").casefold() not in STANDARD_VARIANTS
    ):
        return False, None
    owner_turn = owner_color == "white"
    covered = False
    departure = None
    visited: set[str] = set()
    confirmed = False
    try:
        board = chess.Board(headers.get("FEN", chess.STARTING_FEN))
        if not board.is_valid():
            return False, None
        position = board.epd(en_passant="legal")
        for uci in moves:
            move = board.parse_uci(uci)
            if not move:  # parse_uci also accepts a null move, which is not playable.
                return False, None
            if not confirmed and position in positions:
                visited.add(position)
                if (decision := positions[position]) is not None:
                    covered = True
                    if uci not in decision.continuations:
                        departure = _Departure(
                            decision.node_id,
                            uci,
                            board.san(move),
                            "you" if board.turn == owner_turn else "opponent",
                        )
            board.push(move)
            if not confirmed:
                position = board.epd(en_passant="legal")
                if departure is not None and position in positions:
                    if position in visited:
                        # Repetition of a visited position does not erase a departure.
                        confirmed = True
                    else:
                        departure = None
    except ValueError:
        return False, None  # Legacy rows with unusable positions or moves are not evidence.
    return covered, departure


def compare_repertoire(repertoire: Repertoire, session: Session) -> RepertoireDeviationReport:
    tree = RepertoireTree.model_validate(repertoire.tree)
    positions: dict[str, _Decision | None] = {}
    for node in tree.nodes:
        position = chess.Board(node.fen).epd(en_passant="legal")
        if not node.children:
            positions.setdefault(position, None)
            continue
        decision = positions.get(position)
        if decision is None:
            decision = positions[position] = _Decision(node.id)
        for child_id in node.children:
            if (uci := tree.nodes[child_id].uci) is not None:
                decision.continuations.add(uci)

    # Play can continue an imported row in place and can replace its owner side.
    # Those rows no longer provide unambiguous imported-only evidence.
    linked_games = select(PlaySession.game_id).where(col(PlaySession.game_id).is_not(None))
    games = session.exec(
        select(Game.headers, Game.moves, Game.owner_color)
        .where(Game.owner_player_id == repertoire.player_id, col(Game.id).not_in(linked_games))
        .execution_options(yield_per=128)
    )
    counts: dict[_Departure, int] = {}
    covered = uncovered = 0
    for headers, moves, owner_color in games:
        matched, departure = _first_departure(headers, moves, owner_color, positions)
        if matched:
            covered += 1
        else:
            uncovered += 1
        if departure is not None:
            counts[departure] = counts.get(departure, 0) + 1
    ranked = sorted(
        counts.items(), key=lambda item: (-item[1], item[0].node_id, item[0].uci, item[0].played_by)
    )
    return RepertoireDeviationReport(
        covered_games=covered,
        uncovered_games=uncovered,
        deviations=[
            RepertoireDeviation(
                node_id=move.node_id,
                uci=move.uci,
                san=move.san,
                played_by=move.played_by,
                games=games_count,
            )
            for move, games_count in ranked
        ],
    )

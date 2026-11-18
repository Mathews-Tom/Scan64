"""Read-only, owner-move pooled accuracy and matched PGN clock coverage."""

from __future__ import annotations

import io
import math

import chess
import chess.pgn
from pydantic import BaseModel
from sqlmodel import Session, col, select

from scan64.chess.analysis.models import GameMoveReview
from scan64.chess.boards import board_from
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.openings.curated import OPENING_FAMILIES
from scan64.content.openings.models import OpeningFamilyPayload
from scan64.learning.scheduling.opening_rotation import classify_opening_family
from scan64.learning.tolerance import review_loss


class AccuracyFigure(BaseModel):
    label: str
    accuracy: float | None
    scored_moves: int
    eligible_moves: int


class TimePressureFigure(BaseModel):
    share: float | None
    pressure_moves: int
    valid_clock_moves: int
    eligible_moves: int


class ProfileInsightsRead(BaseModel):
    overall: AccuracyFigure
    phases: list[AccuracyFigure]
    colours: list[AccuracyFigure]
    openings: list[AccuracyFigure]
    time_pressure: TimePressureFigure


def _clocks(game: Game) -> tuple[bool, dict[int, float]]:
    """Only matched real-time PGNs establish clock eligibility (not annotations)."""
    try:
        parsed = chess.pgn.read_game(io.StringIO(game.pgn))
        if parsed is None or parsed.errors:
            return False, {}
        control = chess.pgn.parse_time_control(parsed.headers.get("TimeControl", "?"))
        # PGN per-move correspondence controls otherwise classify as STANDARD.
        realtime = (
            control.type
            in {
                chess.pgn.TimeControlType.BULLET,
                chess.pgn.TimeControlType.BLITZ,
                chess.pgn.TimeControlType.RAPID,
                chess.pgn.TimeControlType.STANDARD,
            }
            and bool(control.parts)
            and all(
                0 <= part.time < 86400
                and (part.time > 0 or part.increment > 0 or part.delay > 0)
                and part.moves >= 0
                and math.isfinite(part.increment)
                and part.increment >= 0
                and math.isfinite(part.delay)
                and part.delay >= 0
                for part in control.parts
            )
        )
        if not realtime:
            return False, {}
        nodes = list(parsed.mainline())
        if (
            parsed.board().fen() != board_from(game.headers.get("FEN")).fen()
            or [node.move.uci() for node in nodes] != game.moves
        ):
            return True, {}
        clocks = {}
        for ply, node in enumerate(nodes, 1):
            value = node.clock()
            if value is not None and math.isfinite(value) and value >= 0:
                clocks[ply] = value
        return True, clocks
    except (ValueError, TypeError, IndexError):
        return False, {}


def read_profile_insights(player_id: str, session: Session) -> ProfileInsightsRead:
    families = [OpeningFamilyPayload.model_validate(item.payload) for item in OPENING_FAMILIES]
    family_names = {family.family_id: family.name for family in families}
    games = session.exec(
        select(Game).where(Game.owner_player_id == player_id, col(Game.owner_color).is_not(None))
    ).all()
    rows = {
        (row.game_id, row.ply): row
        for row in session.exec(
            select(GameMoveReview).where(col(GameMoveReview.game_id).in_([g.id for g in games]))
        ).all()
    }
    groups: dict[str, tuple[list[float], int]] = {
        key: ([], 0) for key in ["Overall", "Opening", "Middlegame", "Endgame", "White", "Black"]
    }
    opening_labels: set[str] = set()
    eligible_clocks = valid_clocks = pressure = 0
    positions = {
        position.id: position.fen
        for position in session.exec(
            select(Position).where(col(Position.game_id).in_([g.id for g in games]))
        ).all()
    }
    for game in games:
        board = board_from(game.headers.get("FEN"))
        family_id = classify_opening_family(game.moves, families)
        opening = family_names[family_id] if family_id else "Unclassified opening"
        opening_key = f"opening:{opening}"
        opening_labels.add(opening_key)
        groups.setdefault(opening_key, ([], 0))
        realtime, clocks = _clocks(game)
        for ply, uci in enumerate(game.moves, 1):
            source_fen = board.fen()
            mover = "white" if board.turn else "black"
            material = sum(
                len(board.pieces(piece, colour)) * value
                for piece, value in [
                    (chess.QUEEN, 9),
                    (chess.ROOK, 5),
                    (chess.BISHOP, 3),
                    (chess.KNIGHT, 3),
                ]
                for colour in chess.COLORS
            )
            phase = (
                "Opening"
                if board.fullmove_number <= 10
                else "Endgame"
                if material <= 26
                else "Middlegame"
            )
            board.push_uci(uci)
            if mover != game.owner_color:
                continue
            row = rows.get((game.id, ply))
            loss = (
                review_loss(row.before_probability, row.after_probability)
                if row is not None
                and row.mover == mover
                and row.move_uci == uci
                and positions.get(row.source_position_id) == source_fen
                else None
            )
            for key in ["Overall", phase, mover.title(), opening_key]:
                losses, count = groups[key]
                groups[key] = (losses, count + 1)
                if loss is not None:
                    losses.append(loss)
            if realtime:
                eligible_clocks += 1
                if ply in clocks:
                    valid_clocks += 1
                    pressure += clocks[ply] <= 30

    def figure(key: str) -> AccuracyFigure:
        losses, count = groups[key]
        return AccuracyFigure(
            label=key.removeprefix("opening:"),
            accuracy=100 * (1 - sum(losses) / len(losses)) if losses else None,
            scored_moves=len(losses),
            eligible_moves=count,
        )

    return ProfileInsightsRead(
        overall=figure("Overall"),
        phases=[figure(key) for key in ["Opening", "Middlegame", "Endgame"]],
        colours=[figure(key) for key in ["White", "Black"]],
        openings=[figure(key) for key in sorted(opening_labels)],
        time_pressure=TimePressureFigure(
            share=100 * pressure / valid_clocks if valid_clocks else None,
            pressure_moves=pressure,
            valid_clock_moves=valid_clocks,
            eligible_moves=eligible_clocks,
        ),
    )

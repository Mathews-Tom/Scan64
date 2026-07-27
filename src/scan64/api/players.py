from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, col, select

from scan64.api.auth import require_player_token
from scan64.api.models import (
    Player,
    PlayerCredential,
    PlayerProfile,
    issue_player_token,
)
from scan64.api.pagination import PaginatedResponse, decode_cursor, encode_cursor
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.persistence.database import get_session

router = APIRouter(tags=["players"])


class PlayerCreate(BaseModel):
    id: str
    display_name: str | None = None
    preferences: dict[str, Any] = {}


class PlayerRead(BaseModel):
    id: str
    preferences: dict[str, Any]


class PlayerCreateResponse(PlayerRead):
    access_token: str


class PlayerProfileRead(BaseModel):
    player_id: str
    rating: int
    display_name: str | None


@router.post("/v1/players", response_model=PlayerCreateResponse)
def create_player(
    player_in: PlayerCreate, session: Session = Depends(get_session)
) -> PlayerCreateResponse:
    existing = session.get(Player, player_in.id)
    if existing:
        raise HTTPException(status_code=409, detail="Player already exists")

    access_token, token_hash = issue_player_token()
    player = Player(id=player_in.id, preferences=player_in.preferences)
    profile = PlayerProfile(player_id=player.id, display_name=player_in.display_name)
    credential = PlayerCredential(player_id=player.id, token_hash=token_hash)
    session.add(player)
    session.add(profile)
    session.add(credential)
    session.commit()

    return PlayerCreateResponse(
        id=player.id,
        preferences=player.preferences,
        access_token=access_token,
    )


def read_player_profile(player_id: str, session: Session) -> PlayerProfileRead:
    profile = session.get(PlayerProfile, player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return PlayerProfileRead.model_validate(profile.model_dump())


@router.get("/v1/players/{player_id}/profile", response_model=PlayerProfileRead)
def get_player_profile(
    player_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> PlayerProfileRead:
    require_player_token(request, player_id, session)
    return read_player_profile(player_id, session)


class PlayerGameRead(BaseModel):
    id: UUID
    white: str
    black: str
    result: str
    date: str
    created_at: datetime
    diagnosis_count: int


def _diagnosis_counts(game_ids: list[UUID], session: Session) -> dict[UUID, int]:
    if not game_ids:
        return {}
    rows = session.exec(
        select(PersistedLessonOpportunity.game_id, func.count())
        .where(col(PersistedLessonOpportunity.game_id).in_(game_ids))
        .group_by(col(PersistedLessonOpportunity.game_id))
    ).all()
    return {game_id: count for game_id, count in rows}


def _decode_game_cursor(cursor: str) -> tuple[datetime, UUID]:
    cursor_data = decode_cursor(cursor)
    try:
        return datetime.fromisoformat(cursor_data["created_at"]), UUID(cursor_data["id"])
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid cursor") from error


@router.get("/v1/players/{player_id}/games", response_model=PaginatedResponse[PlayerGameRead])
def list_player_games(
    player_id: str,
    request: Request,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_session),
) -> PaginatedResponse[PlayerGameRead]:
    """Every game the player owns, played or imported, newest most recent first.

    ``date`` is the game's own date — an imported game keeps the date its PGN
    carried — while ``created_at`` is the row's insertion time and the sort and
    cursor key. An unfinished game is listed too, with result ``*``, so a
    client can find a session to resume.
    """
    require_player_token(request, player_id, session)

    query = (
        select(Game)
        .where(Game.owner_player_id == player_id)
        .order_by(col(Game.created_at).desc(), col(Game.id).desc())
    )
    if cursor:
        created_at, last_id = _decode_game_cursor(cursor)
        query = query.where(
            (Game.created_at < created_at) | ((Game.created_at == created_at) & (Game.id < last_id))
        )

    games = list(session.exec(query.limit(limit + 1)).all())
    next_cursor = None
    if len(games) > limit:
        games = games[:limit]
        last = games[-1]
        next_cursor = encode_cursor({"created_at": last.created_at.isoformat(), "id": str(last.id)})

    counts = _diagnosis_counts([game.id for game in games], session)
    return PaginatedResponse(
        items=[
            PlayerGameRead(
                id=game.id,
                white=game.white,
                black=game.black,
                result=game.result,
                date=game.date or game.created_at.strftime("%Y.%m.%d"),
                created_at=game.created_at,
                diagnosis_count=counts.get(game.id, 0),
            )
            for game in games
        ],
        next_cursor=next_cursor,
    )

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from scan64.api.models import Player, PlayerProfile
from scan64.persistence.database import get_session

router = APIRouter(tags=["players"])


class PlayerCreate(BaseModel):
    id: str
    display_name: str | None = None
    preferences: dict = {}


class PlayerRead(BaseModel):
    id: str
    preferences: dict


class PlayerProfileRead(BaseModel):
    player_id: str
    rating: int
    display_name: str | None


@router.post("/v1/players", response_model=PlayerRead)
def create_player(player_in: PlayerCreate, session: Session = Depends(get_session)):
    existing = session.get(Player, player_in.id)
    if existing:
        raise HTTPException(status_code=409, detail="Player already exists")

    player = Player(id=player_in.id, preferences=player_in.preferences)
    profile = PlayerProfile(player_id=player.id, display_name=player_in.display_name)
    session.add(player)
    session.add(profile)
    session.commit()
    session.refresh(player)
    return player


@router.get("/v1/players/{player_id}/profile", response_model=PlayerProfileRead)
def get_player_profile(player_id: str, session: Session = Depends(get_session)):
    profile = session.get(PlayerProfile, player_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile

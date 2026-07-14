from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from scan64.content.famous_games.curated import FAMOUS_GAMES
from scan64.content.models import ContentAttempt, ContentItem
from scan64.content.tracking import apply_content_attempt_to_profile
from scan64.learning.profiling.models import SkillState
from scan64.persistence.database import get_session

router = APIRouter(prefix="/content", tags=["content"])

class FamousGameRead(BaseModel):
    id: str
    payload: dict[str, Any]
    skill_mapping: dict[str, float]

class AttemptCreate(BaseModel):
    player_id: str
    success: bool
    hint_assisted: bool
    response_payload: dict[str, Any]

class AttemptRead(BaseModel):
    id: str
    success: bool
    hint_assisted: bool

@router.get("/famous-games", response_model=list[FamousGameRead])
def list_famous_games() -> list[FamousGameRead]:
    return [FamousGameRead(**g) for g in FAMOUS_GAMES]

@router.get("/famous-games/{game_id}", response_model=FamousGameRead)
def get_famous_game(game_id: str) -> FamousGameRead:
    for g in FAMOUS_GAMES:
        if g["id"] == game_id:
            return FamousGameRead(**g)
    raise HTTPException(status_code=404, detail="Game not found")

@router.post("/famous-games/{game_id}/attempts", response_model=AttemptRead)
def record_famous_game_attempt(
    game_id: str, attempt_in: AttemptCreate, session: Session = Depends(get_session)
) -> AttemptRead:
    game_data = None
    for g in FAMOUS_GAMES:
        if g["id"] == game_id:
            game_data = g
            break
    if not game_data:
        raise HTTPException(status_code=404, detail="Game not found")

    # Create dummy ContentItem representing the famous game since it's hardcoded in curated
    item = ContentItem(
        id=game_data["id"],
        domain=game_data["domain"],
        payload=game_data["payload"],
        provenance="curated",
        licence="mixed",
        skill_mapping=game_data["skill_mapping"]
    )

    attempt = ContentAttempt(
        item_id=item.id,
        player_id=attempt_in.player_id,
        success=attempt_in.success,
        hint_assisted=attempt_in.hint_assisted,
        response_payload=attempt_in.response_payload,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC)
    )
    session.add(attempt)

    # Fetch existing skills
    existing_skills = session.exec(
        select(SkillState).where(SkillState.player_id == attempt_in.player_id)
    ).all()

    # Route attempt to shared profile
    updated_skills = apply_content_attempt_to_profile(attempt, item, list(existing_skills))
    for skill in updated_skills:
        session.add(skill)

    session.commit()
    session.refresh(attempt)
    return AttemptRead(id=attempt.id, success=attempt.success, hint_assisted=attempt.hint_assisted)

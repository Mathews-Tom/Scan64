from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

from scan64.content.famous_games.curated import FAMOUS_GAMES
from scan64.content.famous_games.models import FamousGamePayload
from scan64.content.models import ContentAttempt, ContentItem
from scan64.content.tracking import apply_content_attempt_to_profile
from scan64.learning.profiling.models import SkillState
from scan64.persistence.database import get_session

router = APIRouter(prefix="/v1/content", tags=["content"])


class FamousGameRead(BaseModel):
    id: str
    payload: dict[str, object]
    skill_mapping: dict[str, float]

    @classmethod
    def from_content_item(cls, item: ContentItem) -> FamousGameRead:
        return cls(
            id=item.id,
            payload=item.payload,
            skill_mapping=item.skill_mapping,
        )


class AttemptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: str
    decision_id: str
    hint_assisted: bool
    response_payload: dict[str, object]


class AttemptRead(BaseModel):
    id: str
    success: bool
    hint_assisted: bool


def seed_famous_games(session: Session) -> None:
    for game in FAMOUS_GAMES:
        content_item = game.to_content_item()
        existing_item = session.get(ContentItem, content_item.id)
        if existing_item is None:
            session.add(content_item)
            continue

        existing_item.version = content_item.version
        existing_item.payload = content_item.payload
        existing_item.provenance = content_item.provenance
        existing_item.licence = content_item.licence
        existing_item.skill_mapping = content_item.skill_mapping
        existing_item.difficulty_estimate = content_item.difficulty_estimate
        session.add(existing_item)
    session.commit()


def famous_game_payload(item: ContentItem) -> FamousGamePayload:
    if item.domain != "famous_games":
        raise HTTPException(status_code=404, detail="Game not found")
    return FamousGamePayload.model_validate(item.payload)


def get_famous_game_item(game_id: str, session: Session) -> ContentItem:
    item = session.get(ContentItem, game_id)
    if item is None or item.domain != "famous_games":
        raise HTTPException(status_code=404, detail="Game not found")
    return item


@router.get("/famous-games", response_model=list[FamousGameRead])
def list_famous_games(session: Session = Depends(get_session)) -> list[FamousGameRead]:
    seed_famous_games(session)
    items = session.exec(
        select(ContentItem).where(ContentItem.domain == "famous_games").order_by(ContentItem.id)
    ).all()
    return [FamousGameRead.from_content_item(item) for item in items]


@router.get("/famous-games/{game_id}", response_model=FamousGameRead)
def get_famous_game(game_id: str, session: Session = Depends(get_session)) -> FamousGameRead:
    seed_famous_games(session)
    return FamousGameRead.from_content_item(get_famous_game_item(game_id, session))


@router.post("/famous-games/{game_id}/attempts", response_model=AttemptRead)
def record_famous_game_attempt(
    game_id: str,
    attempt_in: AttemptCreate,
    session: Session = Depends(get_session),
) -> AttemptRead:
    seed_famous_games(session)
    item = get_famous_game_item(game_id, session)
    payload = famous_game_payload(item)
    decision = next(
        (decision for decision in payload.decisions if decision.id == attempt_in.decision_id),
        None,
    )
    if decision is None:
        raise HTTPException(status_code=422, detail="Unknown famous-game decision")

    raw_move = attempt_in.response_payload.get("move")
    if not isinstance(raw_move, str) or not (move := raw_move.strip()):
        raise HTTPException(status_code=422, detail="Famous-game attempts require a SAN move")

    attempt = ContentAttempt(
        item_id=item.id,
        player_id=attempt_in.player_id,
        success=move in decision.accepted_moves,
        hint_assisted=attempt_in.hint_assisted,
        response_payload={
            **attempt_in.response_payload,
            "move": move,
            "decision_id": attempt_in.decision_id,
        },
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(attempt)

    existing_skills = session.exec(
        select(SkillState).where(SkillState.player_id == attempt_in.player_id)
    ).all()
    updated_skills = apply_content_attempt_to_profile(attempt, item, list(existing_skills))
    for skill in updated_skills:
        session.add(skill)

    session.commit()
    session.refresh(attempt)
    return AttemptRead(
        id=attempt.id,
        success=attempt.success,
        hint_assisted=attempt.hint_assisted,
    )

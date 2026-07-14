from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from scan64.api.models import Player, PlayerProfile
from scan64.chess.games.models import Game, PlaySession
from scan64.content.models import ContentAttempt, StudySession
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import get_session

router = APIRouter(tags=["data_lifecycle"])


class ExportRequest(BaseModel):
    player_id: str


class ExportArchive(BaseModel):
    player: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    play_sessions: list[dict[str, Any]] = []
    games: list[dict[str, Any]] = []
    skill_states: list[dict[str, Any]] = []
    review_schedules: list[dict[str, Any]] = []
    study_sessions: list[dict[str, Any]] = []
    content_attempts: list[dict[str, Any]] = []


@router.post("/v1/exports", response_model=ExportArchive)
def export_player_data(
    req: ExportRequest, session: Session = Depends(get_session)
) -> ExportArchive:
    player_id = req.player_id
    player = session.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    profile = session.get(PlayerProfile, player_id)

    play_sessions = session.exec(
        select(PlaySession).where(PlaySession.player_id == player_id)
    ).all()
    game_ids = [ps.game_id for ps in play_sessions if ps.game_id]
    games = session.exec(select(Game).where(Game.id.in_(game_ids))).all() if game_ids else []

    skill_states = session.exec(select(SkillState).where(SkillState.player_id == player_id)).all()
    review_schedules = session.exec(
        select(ReviewSchedule).where(ReviewSchedule.player_id == player_id)
    ).all()
    study_sessions = session.exec(
        select(StudySession).where(StudySession.player_id == player_id)
    ).all()
    content_attempts = session.exec(
        select(ContentAttempt).where(ContentAttempt.player_id == player_id)
    ).all()

    return ExportArchive(
        player=player.model_dump(mode="json"),
        profile=profile.model_dump(mode="json") if profile else None,
        play_sessions=[ps.model_dump(mode="json") for ps in play_sessions],
        games=[g.model_dump(mode="json") for g in games],
        skill_states=[ss.model_dump(mode="json") for ss in skill_states],
        review_schedules=[rs.model_dump(mode="json") for rs in review_schedules],
        study_sessions=[ss.model_dump(mode="json") for ss in study_sessions],
        content_attempts=[ca.model_dump(mode="json") for ca in content_attempts],
    )


@router.post("/v1/imports")
def import_player_data(
    archive: ExportArchive, session: Session = Depends(get_session)
) -> dict[str, str]:
    if not archive.player:
        raise HTTPException(status_code=400, detail="Invalid archive: missing player data")

    player_id = archive.player.get("id")
    if not player_id:
        raise HTTPException(status_code=400, detail="Invalid archive: missing player id")

    existing = session.get(Player, player_id)
    if existing:
        raise HTTPException(status_code=409, detail="Player already exists")

    session.add(Player.model_validate(archive.player))
    if archive.profile:
        session.add(PlayerProfile.model_validate(archive.profile))

    for g_dict in archive.games:
        session.add(Game.model_validate(g_dict))

    for ps_dict in archive.play_sessions:
        session.add(PlaySession.model_validate(ps_dict))

    for ss_dict in archive.skill_states:
        session.add(SkillState.model_validate(ss_dict))

    for rs_dict in archive.review_schedules:
        session.add(ReviewSchedule.model_validate(rs_dict))

    for sess_dict in archive.study_sessions:
        session.add(StudySession.model_validate(sess_dict))

    for ca_dict in archive.content_attempts:
        session.add(ContentAttempt.model_validate(ca_dict))

    session.commit()
    return {"status": "imported"}

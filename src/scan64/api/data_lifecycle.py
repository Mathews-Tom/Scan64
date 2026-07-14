from __future__ import annotations

from hmac import compare_digest
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, col, delete, select

from scan64.api.models import (
    DeletionAudit,
    Player,
    PlayerCredential,
    PlayerProfile,
    player_token_hash,
)
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.content.models import ContentAttempt, StudySession
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.persistence.database import get_session

router = APIRouter(tags=["data_lifecycle"])


def require_player_token(
    request: Request,
    player_id: str,
    session: Session,
    expected_token_hash: str | None = None,
) -> str:
    authorization = request.headers.get("Authorization")
    scheme, separator, token = authorization.partition(" ") if authorization else ("", "", "")
    if scheme != "Bearer" or not separator or not token:
        raise HTTPException(
            status_code=401,
            detail="A player bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_hash = player_token_hash(token)
    if expected_token_hash is None:
        credential = session.get(PlayerCredential, player_id)
        expected_token_hash = credential.token_hash if credential else None

    if expected_token_hash is None or not compare_digest(token_hash, expected_token_hash):
        raise HTTPException(status_code=403, detail="Player bearer token does not match")

    return token_hash


class ExportRequest(BaseModel):
    player_id: str


class ExportArchive(BaseModel):
    player: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    play_sessions: list[dict[str, Any]] = Field(default_factory=list)
    games: list[dict[str, Any]] = Field(default_factory=list)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    engine_analyses: list[dict[str, Any]] = Field(default_factory=list)
    analysis_jobs: list[dict[str, Any]] = Field(default_factory=list)
    lesson_opportunities: list[dict[str, Any]] = Field(default_factory=list)
    skill_states: list[dict[str, Any]] = Field(default_factory=list)
    review_schedules: list[dict[str, Any]] = Field(default_factory=list)
    study_sessions: list[dict[str, Any]] = Field(default_factory=list)
    content_attempts: list[dict[str, Any]] = Field(default_factory=list)
    credential_hash: str | None = None


@router.post("/v1/exports", response_model=ExportArchive)
def export_player_data(
    request: Request, req: ExportRequest, session: Session = Depends(get_session)
) -> ExportArchive:
    player_id = req.player_id
    player = session.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    credential_hash = require_player_token(request, player_id, session)

    profile = session.get(PlayerProfile, player_id)
    play_sessions = session.exec(
        select(PlaySession).where(col(PlaySession.player_id) == player_id)
    ).all()
    game_ids = list(
        {play_session.game_id for play_session in play_sessions if play_session.game_id}
    )
    games = session.exec(select(Game).where(col(Game.id).in_(game_ids))).all() if game_ids else []
    positions = (
        session.exec(select(Position).where(col(Position.game_id).in_(game_ids))).all()
        if game_ids
        else []
    )
    position_ids = [position.id for position in positions]
    engine_analyses = (
        session.exec(
            select(EngineAnalysis).where(col(EngineAnalysis.position_id).in_(position_ids))
        ).all()
        if position_ids
        else []
    )
    analysis_jobs = (
        session.exec(select(AnalysisJob).where(col(AnalysisJob.game_id).in_(game_ids))).all()
        if game_ids
        else []
    )
    lesson_opportunities = (
        session.exec(
            select(PersistedLessonOpportunity).where(
                col(PersistedLessonOpportunity.game_id).in_(game_ids)
            )
        ).all()
        if game_ids
        else []
    )
    skill_states = session.exec(
        select(SkillState).where(col(SkillState.player_id) == player_id)
    ).all()
    review_schedules = session.exec(
        select(ReviewSchedule).where(col(ReviewSchedule.player_id) == player_id)
    ).all()
    study_sessions = session.exec(
        select(StudySession).where(col(StudySession.player_id) == player_id)
    ).all()
    content_attempts = session.exec(
        select(ContentAttempt).where(col(ContentAttempt.player_id) == player_id)
    ).all()

    return ExportArchive(
        player=player.model_dump(mode="json"),
        profile=profile.model_dump(mode="json") if profile else None,
        play_sessions=[play_session.model_dump(mode="json") for play_session in play_sessions],
        games=[game.model_dump(mode="json") for game in games],
        positions=[position.model_dump(mode="json") for position in positions],
        engine_analyses=[analysis.model_dump(mode="json") for analysis in engine_analyses],
        analysis_jobs=[analysis_job.model_dump(mode="json") for analysis_job in analysis_jobs],
        lesson_opportunities=[
            opportunity.model_dump(mode="json") for opportunity in lesson_opportunities
        ],
        skill_states=[skill_state.model_dump(mode="json") for skill_state in skill_states],
        review_schedules=[
            review_schedule.model_dump(mode="json") for review_schedule in review_schedules
        ],
        study_sessions=[study_session.model_dump(mode="json") for study_session in study_sessions],
        content_attempts=[
            content_attempt.model_dump(mode="json") for content_attempt in content_attempts
        ],
        credential_hash=credential_hash,
    )


@router.post("/v1/imports")
def import_player_data(
    request: Request, archive: ExportArchive, session: Session = Depends(get_session)
) -> dict[str, str]:
    if not archive.player:
        raise HTTPException(status_code=400, detail="Invalid archive: missing player data")

    player_id = archive.player.get("id")
    if not isinstance(player_id, str) or not player_id:
        raise HTTPException(status_code=400, detail="Invalid archive: missing player id")
    if archive.credential_hash is None:
        raise HTTPException(status_code=400, detail="Invalid archive: missing credential hash")

    token_hash = require_player_token(
        request,
        player_id,
        session,
        expected_token_hash=archive.credential_hash,
    )
    existing = session.get(Player, player_id)
    if existing:
        raise HTTPException(status_code=409, detail="Player already exists")

    player = Player.model_validate(archive.player)
    profile = PlayerProfile.model_validate(archive.profile) if archive.profile else None
    play_sessions = [PlaySession.model_validate(data) for data in archive.play_sessions]
    games = [Game.model_validate(data) for data in archive.games]
    positions = [Position.model_validate(data) for data in archive.positions]
    engine_analyses = [EngineAnalysis.model_validate(data) for data in archive.engine_analyses]
    analysis_jobs = [AnalysisJob.model_validate(data) for data in archive.analysis_jobs]
    lesson_opportunities = [
        PersistedLessonOpportunity.model_validate(data) for data in archive.lesson_opportunities
    ]
    skill_states = [SkillState.model_validate(data) for data in archive.skill_states]
    review_schedules = [ReviewSchedule.model_validate(data) for data in archive.review_schedules]
    study_sessions = [StudySession.model_validate(data) for data in archive.study_sessions]
    content_attempts = [ContentAttempt.model_validate(data) for data in archive.content_attempts]

    game_ids = {play_session.game_id for play_session in play_sessions if play_session.game_id}
    position_ids = {position.id for position in positions}
    study_session_ids = {study_session.id for study_session in study_sessions}
    has_foreign_owner = (
        player.id != player_id
        or profile is not None
        and profile.player_id != player_id
        or any(play_session.player_id != player_id for play_session in play_sessions)
        or any(skill_state.player_id != player_id for skill_state in skill_states)
        or any(schedule.player_id != player_id for schedule in review_schedules)
        or any(study_session.player_id != player_id for study_session in study_sessions)
        or any(content_attempt.player_id != player_id for content_attempt in content_attempts)
        or any(
            content_attempt.session_id is not None
            and content_attempt.session_id not in study_session_ids
            for content_attempt in content_attempts
        )
        or {game.id for game in games} != game_ids
        or any(position.game_id not in game_ids for position in positions)
        or any(analysis.position_id not in position_ids for analysis in engine_analyses)
        or any(analysis_job.game_id not in game_ids for analysis_job in analysis_jobs)
        or any(opportunity.game_id not in game_ids for opportunity in lesson_opportunities)
    )
    if has_foreign_owner:
        raise HTTPException(
            status_code=400,
            detail="Invalid archive: records do not belong to the authorized player",
        )

    session.add(player)
    session.add(PlayerCredential(player_id=player_id, token_hash=token_hash))
    if profile:
        session.add(profile)

    for game in games:
        if session.get(Game, game.id) is None:
            session.add(game)

    for position in positions:
        if session.get(Position, position.id) is None:
            session.add(position)

    for analysis in engine_analyses:
        if session.get(EngineAnalysis, analysis.id) is None:
            session.add(analysis)

    for analysis_job in analysis_jobs:
        if session.get(AnalysisJob, analysis_job.id) is None:
            session.add(analysis_job)

    for opportunity in lesson_opportunities:
        if session.get(PersistedLessonOpportunity, opportunity.id) is None:
            session.add(opportunity)

    for play_session in play_sessions:
        session.add(play_session)

    for skill_state in skill_states:
        session.add(skill_state)

    for review_schedule in review_schedules:
        session.add(review_schedule)

    for study_session in study_sessions:
        session.add(study_session)

    for content_attempt in content_attempts:
        session.add(content_attempt)

    session.commit()
    return {"status": "imported"}


class DeletionRequest(BaseModel):
    dry_run: bool = True
    confirmation: str | None = None


class DeletionResponse(BaseModel):
    dry_run: bool
    affected_rows: dict[str, int]
    audit_id: str | None = None


@router.delete("/v1/players/{player_id}/data", response_model=DeletionResponse)
def delete_player_data(
    player_id: str,
    request: Request,
    req: DeletionRequest,
    session: Session = Depends(get_session),
) -> DeletionResponse:
    player = session.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    require_player_token(request, player_id, session)

    if not req.dry_run and req.confirmation != f"delete-{player_id}":
        raise HTTPException(
            status_code=400,
            detail="Invalid confirmation string. Must be 'delete-{player_id}'",
        )

    play_sessions = session.exec(
        select(PlaySession).where(col(PlaySession.player_id) == player_id)
    ).all()
    game_ids = list(
        {play_session.game_id for play_session in play_sessions if play_session.game_id}
    )
    shared_game_ids = (
        set(
            session.exec(
                select(PlaySession.game_id).where(
                    col(PlaySession.game_id).in_(game_ids),
                    col(PlaySession.player_id) != player_id,
                )
            ).all()
        )
        if game_ids
        else set()
    )
    owned_game_ids = [game_id for game_id in game_ids if game_id not in shared_game_ids]
    positions = (
        session.exec(select(Position).where(col(Position.game_id).in_(owned_game_ids))).all()
        if owned_game_ids
        else []
    )
    position_ids = [position.id for position in positions]
    engine_analyses = (
        session.exec(
            select(EngineAnalysis).where(col(EngineAnalysis.position_id).in_(position_ids))
        ).all()
        if position_ids
        else []
    )
    analysis_jobs = (
        session.exec(select(AnalysisJob).where(col(AnalysisJob.game_id).in_(owned_game_ids))).all()
        if owned_game_ids
        else []
    )
    lesson_opportunities = (
        session.exec(
            select(PersistedLessonOpportunity).where(
                col(PersistedLessonOpportunity.game_id).in_(owned_game_ids)
            )
        ).all()
        if owned_game_ids
        else []
    )

    affected_rows = {
        "player": 1,
        "profile": 1 if session.get(PlayerProfile, player_id) else 0,
        "play_sessions": len(play_sessions),
        "games": len(owned_game_ids),
        "positions": len(positions),
        "engine_analyses": len(engine_analyses),
        "analysis_jobs": len(analysis_jobs),
        "lesson_opportunities": len(lesson_opportunities),
        "skill_states": len(
            session.exec(select(SkillState).where(col(SkillState.player_id) == player_id)).all()
        ),
        "review_schedules": len(
            session.exec(
                select(ReviewSchedule).where(col(ReviewSchedule.player_id) == player_id)
            ).all()
        ),
        "study_sessions": len(
            session.exec(select(StudySession).where(col(StudySession.player_id) == player_id)).all()
        ),
        "content_attempts": len(
            session.exec(
                select(ContentAttempt).where(col(ContentAttempt.player_id) == player_id)
            ).all()
        ),
    }

    if req.dry_run:
        return DeletionResponse(dry_run=True, affected_rows=affected_rows)

    if position_ids:
        session.exec(
            delete(EngineAnalysis).where(col(EngineAnalysis.position_id).in_(position_ids))
        )
    if owned_game_ids:
        session.exec(
            delete(PersistedLessonOpportunity).where(
                col(PersistedLessonOpportunity.game_id).in_(owned_game_ids)
            )
        )
        session.exec(delete(AnalysisJob).where(col(AnalysisJob.game_id).in_(owned_game_ids)))
        session.exec(delete(Position).where(col(Position.game_id).in_(owned_game_ids)))
    session.exec(delete(ContentAttempt).where(col(ContentAttempt.player_id) == player_id))
    session.exec(delete(StudySession).where(col(StudySession.player_id) == player_id))
    session.exec(delete(ReviewSchedule).where(col(ReviewSchedule.player_id) == player_id))
    session.exec(delete(SkillState).where(col(SkillState.player_id) == player_id))
    session.exec(delete(PlaySession).where(col(PlaySession.player_id) == player_id))
    if owned_game_ids:
        session.exec(delete(Game).where(col(Game.id).in_(owned_game_ids)))

    profile = session.get(PlayerProfile, player_id)
    if profile:
        session.delete(profile)
    session.exec(delete(PlayerCredential).where(col(PlayerCredential.player_id) == player_id))
    session.delete(player)

    audit_id = str(uuid4())
    session.add(DeletionAudit(id=audit_id, player_id=player_id, affected_rows=affected_rows))
    session.commit()

    return DeletionResponse(dry_run=False, affected_rows=affected_rows, audit_id=audit_id)

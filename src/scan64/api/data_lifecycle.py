from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, col, delete, select

from scan64.api.models import DeletionAudit, Player, PlayerProfile
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
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

    for game_data in archive.games:
        game = Game.model_validate(game_data)
        if session.get(Game, game.id) is None:
            session.add(game)

    for position_data in archive.positions:
        position = Position.model_validate(position_data)
        if session.get(Position, position.id) is None:
            session.add(position)

    for analysis_data in archive.engine_analyses:
        analysis = EngineAnalysis.model_validate(analysis_data)
        if session.get(EngineAnalysis, analysis.id) is None:
            session.add(analysis)

    for analysis_job_data in archive.analysis_jobs:
        analysis_job = AnalysisJob.model_validate(analysis_job_data)
        if session.get(AnalysisJob, analysis_job.id) is None:
            session.add(analysis_job)

    for opportunity_data in archive.lesson_opportunities:
        opportunity = PersistedLessonOpportunity.model_validate(opportunity_data)
        if session.get(PersistedLessonOpportunity, opportunity.id) is None:
            session.add(opportunity)

    for play_session_data in archive.play_sessions:
        session.add(PlaySession.model_validate(play_session_data))

    for skill_state_data in archive.skill_states:
        session.add(SkillState.model_validate(skill_state_data))

    for review_schedule_data in archive.review_schedules:
        session.add(ReviewSchedule.model_validate(review_schedule_data))

    for study_session_data in archive.study_sessions:
        session.add(StudySession.model_validate(study_session_data))

    for content_attempt_data in archive.content_attempts:
        session.add(ContentAttempt.model_validate(content_attempt_data))

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
    player_id: str, req: DeletionRequest, session: Session = Depends(get_session)
) -> DeletionResponse:
    player = session.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

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
    session.delete(player)

    audit_id = str(uuid4())
    session.add(DeletionAudit(id=audit_id, player_id=player_id, affected_rows=affected_rows))
    session.commit()

    return DeletionResponse(dry_run=False, affected_rows=affected_rows, audit_id=audit_id)

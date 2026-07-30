from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import update
from sqlmodel import Session, col, delete, select

from scan64.api.auth import require_player_token
from scan64.api.middleware import IdempotencyRecord
from scan64.api.models import DeletionAudit, Player, PlayerCredential, PlayerProfile
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    PersistedDiagnosis,
    PersistedLessonOpportunity,
)
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.coach.models import CoachStudentLink
from scan64.content.models import ContentAttempt, LessonAttempt, StudySession
from scan64.learning.evaluation.transfer_measurement import TransferMeasurement
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.transfer import TransferPosition
from scan64.learning.profiling.models import ProfileObservation, SkillState
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
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    profile_observations: list[dict[str, Any]] = Field(default_factory=list)
    lesson_attempts: list[dict[str, Any]] = Field(default_factory=list)
    transfer_positions: list[dict[str, Any]] = Field(default_factory=list)
    transfer_measurements: list[dict[str, Any]] = Field(default_factory=list)
    coach_student_links: list[dict[str, Any]] = Field(default_factory=list)
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
    credential_hash = require_player_token(request, player_id, session)
    player = session.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    profile = session.get(PlayerProfile, player_id)
    play_sessions = session.exec(
        select(PlaySession).where(col(PlaySession.player_id) == player_id)
    ).all()
    games = session.exec(
        select(Game).where(col(Game.owner_player_id) == player_id)
    ).all()
    game_ids = [game.id for game in games]
    archived_game_ids = set(game_ids)
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
    evidence = (
        session.exec(
            select(Evidence).where(
                col(Evidence.position_id).in_([str(position_id) for position_id in position_ids])
            )
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
    profile_observations = (
        session.exec(
            select(ProfileObservation).where(
                col(ProfileObservation.player_id) == player_id,
                col(ProfileObservation.game_id).in_([str(game_id) for game_id in game_ids]),
                col(ProfileObservation.position_id).in_(
                    [str(position_id) for position_id in position_ids]
                ),
            )
        ).all()
        if game_ids and position_ids
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
    study_session_ids = [study_session.id for study_session in study_sessions]
    content_attempts = session.exec(
        select(ContentAttempt).where(col(ContentAttempt.player_id) == player_id)
    ).all()
    lesson_attempts = (
        session.exec(
            select(LessonAttempt).where(
                col(LessonAttempt.player_id) == player_id,
                col(LessonAttempt.session_id).in_(study_session_ids),
            )
        ).all()
        if study_session_ids
        else []
    )
    transfer_measurements = session.exec(
        select(TransferMeasurement).where(col(TransferMeasurement.player_id) == player_id)
    ).all()
    transfer_position_ids = {
        position_id
        for measurement in transfer_measurements
        for position_id in (measurement.source_position_id, measurement.target_position_id)
        if position_id is not None
    }
    transfer_positions = (
        session.exec(
            select(TransferPosition).where(
                col(TransferPosition.id).in_(transfer_position_ids)
            )
        ).all()
        if transfer_position_ids
        else []
    )
    coach_student_links = session.exec(
        select(CoachStudentLink).where(
            (col(CoachStudentLink.coach_id) == player_id)
            | (col(CoachStudentLink.student_id) == player_id)
        )
    ).all()

    return ExportArchive(
        player=player.model_dump(mode="json"),
        profile=profile.model_dump(mode="json") if profile else None,
        play_sessions=[
            play_session.model_copy(update={"game_id": None}).model_dump(mode="json")
            if play_session.game_id not in archived_game_ids
            else play_session.model_dump(mode="json")
            for play_session in play_sessions
        ],
        games=[game.model_dump(mode="json") for game in games],
        positions=[position.model_dump(mode="json") for position in positions],
        engine_analyses=[analysis.model_dump(mode="json") for analysis in engine_analyses],
        analysis_jobs=[analysis_job.model_dump(mode="json") for analysis_job in analysis_jobs],
        lesson_opportunities=[
            opportunity.model_dump(mode="json") for opportunity in lesson_opportunities
        ],
        evidence=[item.model_dump(mode="json") for item in evidence],
        profile_observations=[
            observation.model_dump(mode="json") for observation in profile_observations
        ],
        lesson_attempts=[attempt.model_dump(mode="json") for attempt in lesson_attempts],
        transfer_positions=[position.model_dump(mode="json") for position in transfer_positions],
        transfer_measurements=[
            measurement.model_dump(mode="json") for measurement in transfer_measurements
        ],
        coach_student_links=[link.model_dump(mode="json") for link in coach_student_links],
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

    try:
        player = Player.model_validate(archive.player)
        profile = PlayerProfile.model_validate(archive.profile) if archive.profile else None
        play_sessions = [PlaySession.model_validate(data) for data in archive.play_sessions]
        games = [Game.model_validate(data) for data in archive.games]
        positions = [Position.model_validate(data) for data in archive.positions]
        engine_analyses = [
            EngineAnalysis.model_validate(data) for data in archive.engine_analyses
        ]
        analysis_jobs = [AnalysisJob.model_validate(data) for data in archive.analysis_jobs]
        lesson_opportunities = [
            PersistedLessonOpportunity.model_validate(data)
            for data in archive.lesson_opportunities
        ]
        evidence = [Evidence.model_validate(data) for data in archive.evidence]
        profile_observations = [
            ProfileObservation.model_validate(data) for data in archive.profile_observations
        ]
        lesson_attempts = [LessonAttempt.model_validate(data) for data in archive.lesson_attempts]
        transfer_positions = [
            TransferPosition.model_validate(data) for data in archive.transfer_positions
        ]
        transfer_measurements = [
            TransferMeasurement.model_validate(data) for data in archive.transfer_measurements
        ]
        coach_student_links = [
            CoachStudentLink.model_validate(data) for data in archive.coach_student_links
        ]
        skill_states = [SkillState.model_validate(data) for data in archive.skill_states]
        review_schedules = [
            ReviewSchedule.model_validate(data) for data in archive.review_schedules
        ]
        study_sessions = [StudySession.model_validate(data) for data in archive.study_sessions]
        content_attempts = [
            ContentAttempt.model_validate(data) for data in archive.content_attempts
        ]
        for opportunity in lesson_opportunities:
            PersistedDiagnosis.model_validate(opportunity.lesson_spec.get("diagnosis"))
    except ValidationError as error:
        raise HTTPException(
            status_code=400, detail="Invalid archive: malformed records"
        ) from error

    game_ids = {game.id for game in games}
    existing_games = {game.id: session.get(Game, game.id) for game in games}
    position_ids = {position.id for position in positions}
    position_ids_as_strings = {str(position_id) for position_id in position_ids}
    analysis_ids = {str(analysis.id) for analysis in engine_analyses}
    positions_by_id = {position.id: position for position in positions}
    study_session_ids = {study_session.id for study_session in study_sessions}
    lesson_opportunity_ids = {opportunity.id for opportunity in lesson_opportunities}
    transfer_position_ids = {position.id for position in transfer_positions}
    has_foreign_owner = (
        player.id != player_id
        or profile is not None
        and profile.player_id != player_id
        or any(
            play_session.player_id != player_id
            or play_session.game_id is not None
            and play_session.game_id not in game_ids
            for play_session in play_sessions
        )
        or any(game.owner_player_id != player_id for game in games)
        or any(
            existing_game is not None and existing_game.owner_player_id != player_id
            for existing_game in existing_games.values()
        )
        or any(skill_state.player_id != player_id for skill_state in skill_states)
        or any(schedule.player_id != player_id for schedule in review_schedules)
        or any(observation.player_id != player_id for observation in profile_observations)
        or any(study_session.player_id != player_id for study_session in study_sessions)
        or any(content_attempt.player_id != player_id for content_attempt in content_attempts)
        or any(attempt.player_id != player_id for attempt in lesson_attempts)
        or any(measurement.player_id != player_id for measurement in transfer_measurements)
        or any(position.game_id not in game_ids for position in positions)
        or any(analysis.position_id not in position_ids for analysis in engine_analyses)
        or any(analysis_job.game_id not in game_ids for analysis_job in analysis_jobs)
        or any(
            item.position_id not in position_ids_as_strings
            or item.engine_analysis_id not in analysis_ids
            for item in evidence
        )
        or any(
            opportunity.source_position_id not in positions_by_id
            or positions_by_id[opportunity.source_position_id].game_id
            != opportunity.game_id
            for opportunity in lesson_opportunities
        )
        or any(
            observation.game_id not in {str(game_id) for game_id in game_ids}
            or observation.position_id not in position_ids_as_strings
            for observation in profile_observations
        )
        or any(
            attempt.session_id not in study_session_ids
            or attempt.opportunity_id is not None
            and attempt.opportunity_id not in lesson_opportunity_ids
            for attempt in lesson_attempts
        )
        or any(
            session.get(TransferPosition, position_id) is None
            for position_id in transfer_position_ids
        )
        or any(
            measurement.source_position_id not in transfer_position_ids
            or measurement.target_position_id is not None
            and measurement.target_position_id not in transfer_position_ids
            for measurement in transfer_measurements
        )
        or any(
            player_id not in (link.coach_id, link.student_id)
            or session.get(
                Player,
                link.student_id if link.coach_id == player_id else link.coach_id,
            )
            is None
            for link in coach_student_links
        )
        or any(
            content_attempt.session_id is not None
            and content_attempt.session_id not in study_session_ids
            for content_attempt in content_attempts
        )
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
    for item in evidence:
        if session.get(Evidence, item.evidence_id) is None:
            session.add(item)

    for observation in profile_observations:
        session.add(observation)

    # TransferPosition rows are global curated references. Archives may describe
    # them for referential closure but never mutate the shared catalog.

    for measurement in transfer_measurements:
        if session.get(TransferMeasurement, measurement.id) is None:
            session.add(measurement)

    for link in coach_student_links:
        if session.get(CoachStudentLink, (link.coach_id, link.student_id)) is None:
            session.add(link)


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

    for lesson_attempt in lesson_attempts:
        session.add(lesson_attempt)

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
    require_player_token(request, player_id, session)
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
    game_ids = {
        play_session.game_id for play_session in play_sessions if play_session.game_id
    } | set(
        session.exec(
            select(Game.id).where(col(Game.owner_player_id) == player_id)
        ).all()
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
    disowned_game_ids = set(
        session.exec(
            select(Game.id).where(
                col(Game.id).in_(shared_game_ids),
                col(Game.owner_player_id) == player_id,
            )
        ).all()
    )
    owned_game_ids = list(game_ids - shared_game_ids)
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
    lesson_opportunities = session.exec(
        select(PersistedLessonOpportunity).where(
            (col(PersistedLessonOpportunity.game_id).in_(owned_game_ids))
            | (col(PersistedLessonOpportunity.player_id) == player_id)
        )
    ).all()
    evidence = (
        session.exec(
            select(Evidence).where(
                col(Evidence.position_id).in_([str(position_id) for position_id in position_ids])
            )
        ).all()
        if position_ids
        else []
    )
    profile_observations = session.exec(
        select(ProfileObservation).where(col(ProfileObservation.player_id) == player_id)
    ).all()
    lesson_attempts = session.exec(
        select(LessonAttempt).where(col(LessonAttempt.player_id) == player_id)
    ).all()
    transfer_measurements = session.exec(
        select(TransferMeasurement).where(col(TransferMeasurement.player_id) == player_id)
    ).all()

    coach_student_links = session.exec(
        select(CoachStudentLink).where(
            (col(CoachStudentLink.coach_id) == player_id)
            | (col(CoachStudentLink.student_id) == player_id)
        )
    ).all()

    affected_rows = {
        "player": 1,
        "player_credentials": 1
        if session.get(PlayerCredential, player_id) is not None
        else 0,
        "profile": 1 if session.get(PlayerProfile, player_id) else 0,
        "play_sessions": len(play_sessions),
        "games": len(owned_game_ids),
        "games_disowned": len(disowned_game_ids),
        "positions": len(positions),
        "engine_analyses": len(engine_analyses),
        "analysis_jobs": len(analysis_jobs),
        "lesson_opportunities": len(lesson_opportunities),
        "evidence": len(evidence),
        "profile_observations": len(profile_observations),
        "lesson_attempts": len(lesson_attempts),
        "transfer_measurements": len(transfer_measurements),
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
        "coach_student_links": len(coach_student_links),
    }

    if req.dry_run:
        return DeletionResponse(dry_run=True, affected_rows=affected_rows)

    if position_ids:
        session.exec(
            delete(Evidence).where(
                col(Evidence.position_id).in_([str(position_id) for position_id in position_ids])
            )
        )
        session.exec(
            delete(EngineAnalysis).where(col(EngineAnalysis.position_id).in_(position_ids))
        )
    session.exec(
        delete(ProfileObservation).where(col(ProfileObservation.player_id) == player_id)
    )
    session.exec(delete(LessonAttempt).where(col(LessonAttempt.player_id) == player_id))
    session.exec(
        delete(TransferMeasurement).where(col(TransferMeasurement.player_id) == player_id)
    )
    session.exec(
        delete(PersistedLessonOpportunity).where(
            (col(PersistedLessonOpportunity.game_id).in_(owned_game_ids))
            | (col(PersistedLessonOpportunity.player_id) == player_id)
        )
    )
    if owned_game_ids:
        session.exec(delete(AnalysisJob).where(col(AnalysisJob.game_id).in_(owned_game_ids)))
        session.exec(delete(Position).where(col(Position.game_id).in_(owned_game_ids)))
    session.exec(delete(ContentAttempt).where(col(ContentAttempt.player_id) == player_id))
    session.exec(delete(StudySession).where(col(StudySession.player_id) == player_id))
    session.exec(delete(ReviewSchedule).where(col(ReviewSchedule.player_id) == player_id))
    session.exec(delete(SkillState).where(col(SkillState.player_id) == player_id))
    session.exec(
        delete(CoachStudentLink).where(
            (col(CoachStudentLink.coach_id) == player_id)
            | (col(CoachStudentLink.student_id) == player_id)
        )
    )
    session.exec(delete(PlaySession).where(col(PlaySession.player_id) == player_id))
    if owned_game_ids:
        session.exec(delete(Game).where(col(Game.id).in_(owned_game_ids)))
    if disowned_game_ids:
        session.exec(
            update(Game)
            .where(col(Game.id).in_(disowned_game_ids))
            .values(
                owner_player_id=None,
                pgn="",
                white="Anonymous",
                black="Anonymous",
                headers={},
            )
        )

    profile = session.get(PlayerProfile, player_id)
    if profile:
        session.delete(profile)
    session.exec(delete(PlayerCredential).where(col(PlayerCredential.player_id) == player_id))
    session.delete(player)
    principal = hashlib.sha256(request.headers["Authorization"].encode("utf-8")).hexdigest()
    session.exec(
        delete(IdempotencyRecord).where(
            col(IdempotencyRecord.idempotency_key).contains(f":{principal}:")
        )
    )

    audit_id = str(uuid4())
    session.add(DeletionAudit(id=audit_id, player_id=player_id, affected_rows=affected_rows))
    session.commit()
    request.state.skip_idempotency_cache = True

    return DeletionResponse(dry_run=False, affected_rows=affected_rows, audit_id=audit_id)

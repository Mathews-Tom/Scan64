from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, col, select

from scan64.api.auth import require_player_token
from scan64.api.models import Player
from scan64.chess.games.models import PlaySession
from scan64.chess.positions.models import Position
from scan64.learning.evidence.models import Evidence
from scan64.learning.profiling.models import SkillState
from scan64.persistence.database import get_session

router = APIRouter(tags=["reports"])


class ProgressReport(BaseModel):
    player_id: str
    skills: list[dict[str, Any]]


class EvidenceItemRead(BaseModel):
    evidence_id: str
    kind: str
    position_id: str
    claim: str
    payload: dict[str, Any]
    producer: dict[str, Any]


class EvidenceReport(BaseModel):
    player_id: str
    evidence_items: list[EvidenceItemRead]


class PatternsReport(BaseModel):
    player_id: str
    recurring_habits: list[dict[str, Any]]


def read_player_progress(player_id: str, session: Session) -> ProgressReport:
    player = session.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    skills = session.exec(select(SkillState).where(SkillState.player_id == player_id)).all()
    # Mock data for demonstration, normally calculated from SkillState
    return ProgressReport(
        player_id=player_id,
        skills=[
            {
                "concept": skill.concept_code,
                "mastery": skill.expected_mastery,
                "uncertainty": skill.uncertainty,
            }
            for skill in skills
        ],
    )


@router.get("/v1/players/{player_id}/progress", response_model=ProgressReport)
def get_player_progress(
    player_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> ProgressReport:
    require_player_token(request, player_id, session)
    return read_player_progress(player_id, session)


def read_player_evidence(player_id: str, session: Session) -> EvidenceReport:
    if session.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    game_ids = {
        game_id
        for game_id in session.exec(
            select(PlaySession.game_id).where(PlaySession.player_id == player_id)
        ).all()
        if game_id is not None
    }
    if not game_ids:
        return EvidenceReport(player_id=player_id, evidence_items=[])

    position_ids = {
        str(position_id)
        for position_id in session.exec(
            select(Position.id).where(col(Position.game_id).in_(game_ids))
        ).all()
    }
    if not position_ids:
        return EvidenceReport(player_id=player_id, evidence_items=[])

    evidence = session.exec(
        select(Evidence)
        .where(col(Evidence.position_id).in_(position_ids))
        .order_by(Evidence.evidence_id)
    ).all()
    return EvidenceReport(
        player_id=player_id,
        evidence_items=[
            EvidenceItemRead(
                evidence_id=item.evidence_id,
                kind=item.kind,
                position_id=item.position_id,
                claim=item.claim,
                payload=item.payload,
                producer=item.producer,
            )
            for item in evidence
        ],
    )


@router.get("/v1/players/{player_id}/evidence", response_model=EvidenceReport)
def get_player_evidence(
    player_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> EvidenceReport:
    require_player_token(request, player_id, session)
    return read_player_evidence(player_id, session)


def read_player_patterns(player_id: str, session: Session) -> PatternsReport:
    if session.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return PatternsReport(player_id=player_id, recurring_habits=[])


@router.get("/v1/players/{player_id}/patterns", response_model=PatternsReport)
def get_player_patterns(
    player_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> PatternsReport:
    require_player_token(request, player_id, session)
    return read_player_patterns(player_id, session)


@router.get("/v1/reports/weekly")
def get_weekly_report(player_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"player_id": player_id, "summary": "Weekly summary"}


@router.get("/v1/reports/openings")
def get_openings_report(player_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"player_id": player_id, "openings": []}

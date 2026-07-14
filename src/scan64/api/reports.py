from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from scan64.api.models import Player
from scan64.learning.profiling.models import SkillState
from scan64.persistence.database import get_session

router = APIRouter(tags=["reports"])


class ProgressReport(BaseModel):
    player_id: str
    skills: list[dict[str, Any]]


class EvidenceReport(BaseModel):
    player_id: str
    evidence_items: list[dict[str, Any]]


class PatternsReport(BaseModel):
    player_id: str
    recurring_habits: list[dict[str, Any]]


@router.get("/v1/players/{player_id}/progress", response_model=ProgressReport)
def get_player_progress(player_id: str, session: Session = Depends(get_session)) -> ProgressReport:
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


@router.get("/v1/players/{player_id}/evidence", response_model=EvidenceReport)
def get_player_evidence(player_id: str, session: Session = Depends(get_session)) -> EvidenceReport:
    # Normally queries the Evidence table joining with PlaySession or Game
    return EvidenceReport(player_id=player_id, evidence_items=[])


@router.get("/v1/players/{player_id}/patterns", response_model=PatternsReport)
def get_player_patterns(player_id: str, session: Session = Depends(get_session)) -> PatternsReport:
    # Normally aggregates common blunders or behaviors
    return PatternsReport(player_id=player_id, recurring_habits=[])


@router.get("/v1/reports/weekly")
def get_weekly_report(player_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"player_id": player_id, "summary": "Weekly summary"}


@router.get("/v1/reports/openings")
def get_openings_report(player_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"player_id": player_id, "openings": []}

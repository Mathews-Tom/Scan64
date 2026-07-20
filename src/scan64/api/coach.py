from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from scan64.api.auth import require_player_token
from scan64.api.players import PlayerProfileRead, read_player_profile
from scan64.api.reports import (
    EvidenceReport,
    PatternsReport,
    read_player_evidence,
    read_player_patterns,
)
from scan64.coach.models import CoachStudentLink
from scan64.persistence.database import get_session

router = APIRouter(prefix="/v1/coaches", tags=["coaches"])


class CoachStudentDashboard(BaseModel):
    student_id: str
    profile: PlayerProfileRead
    patterns: PatternsReport
    evidence: EvidenceReport


class CoachDashboard(BaseModel):
    coach_id: str
    students: list[CoachStudentDashboard]


@router.get("/{coach_id}/dashboard", response_model=CoachDashboard)
def get_coach_dashboard(
    coach_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> CoachDashboard:
    require_player_token(request, coach_id, session)
    links = session.exec(
        select(CoachStudentLink)
        .where(CoachStudentLink.coach_id == coach_id)
        .order_by(CoachStudentLink.student_id)
    ).all()
    students = [
        CoachStudentDashboard(
            student_id=link.student_id,
            profile=read_player_profile(link.student_id, session),
            patterns=read_player_patterns(link.student_id, session),
            evidence=read_player_evidence(link.student_id, session),
        )
        for link in links
    ]
    return CoachDashboard(coach_id=coach_id, students=students)

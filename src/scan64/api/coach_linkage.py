from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlmodel import Session

from scan64.api.auth import require_player_token
from scan64.api.models import Player
from scan64.coach.models import CoachStudentLink
from scan64.persistence.database import get_session

router = APIRouter(prefix="/v1/coaches", tags=["coaches"])


class CoachStudentLinkRead(BaseModel):
    coach_id: str
    student_id: str
    created_at: datetime


@router.post(
    "/{coach_id}/students/{student_id}",
    response_model=CoachStudentLinkRead,
    status_code=status.HTTP_201_CREATED,
)
def link_student_to_coach(
    coach_id: str,
    student_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> CoachStudentLink:
    require_player_token(request, student_id, session)
    if coach_id == student_id:
        raise HTTPException(status_code=422, detail="A coach cannot link to themselves")
    if session.get(Player, coach_id) is None:
        raise HTTPException(status_code=404, detail="Coach not found")
    if session.get(CoachStudentLink, (coach_id, student_id)) is not None:
        raise HTTPException(status_code=409, detail="Student is already linked to this coach")

    link = CoachStudentLink(coach_id=coach_id, student_id=student_id)
    session.add(link)
    session.commit()
    session.refresh(link)
    return link

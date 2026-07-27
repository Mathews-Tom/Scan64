from collections import defaultdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, col, select

from scan64.api.auth import require_player_token
from scan64.api.models import Player
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game
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


class DiagnosisPatternRead(BaseModel):
    diagnosis: str
    occurrence_count: int
    game_ids: list[str]
    evidence_references: list[str]


class PatternsReport(BaseModel):
    player_id: str
    minimum_occurrences: int
    status: Literal["insufficient_data", "no_recurring_diagnosis", "recurring_diagnosis"]
    recurring_diagnoses: list[DiagnosisPatternRead]


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

    game_ids = set(
        session.exec(select(Game.id).where(Game.owner_player_id == player_id)).all()
    )
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

    rows = session.exec(
        select(PersistedLessonOpportunity)
        .join(Game, col(PersistedLessonOpportunity.game_id) == Game.id)
        .where(Game.owner_player_id == player_id)
    ).all()
    occurrences: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"games": set(), "evidence": set()}
    )
    for row in rows:
        diagnosis = row.lesson_spec.get("diagnosis")
        if not isinstance(diagnosis, dict):
            continue
        primary = diagnosis.get("primary")
        evidence_refs = diagnosis.get("evidence_refs")
        if not isinstance(primary, str) or not primary:
            continue
        occurrences[primary]["games"].add(str(row.game_id))
        if isinstance(evidence_refs, list):
            occurrences[primary]["evidence"].update(
                ref for ref in evidence_refs if isinstance(ref, str) and ref
            )

    minimum_occurrences = 3
    game_count = len({str(row.game_id) for row in rows})
    recurring_diagnoses = [
        DiagnosisPatternRead(
            diagnosis=diagnosis,
            occurrence_count=len(values["games"]),
            game_ids=sorted(values["games"]),
            evidence_references=sorted(values["evidence"]),
        )
        for diagnosis, values in sorted(occurrences.items())
        if len(values["games"]) >= minimum_occurrences
    ]
    status: Literal["insufficient_data", "no_recurring_diagnosis", "recurring_diagnosis"]
    if game_count < minimum_occurrences:
        status = "insufficient_data"
    elif recurring_diagnoses:
        status = "recurring_diagnosis"
    else:
        status = "no_recurring_diagnosis"
    return PatternsReport(
        player_id=player_id,
        minimum_occurrences=minimum_occurrences,
        status=status,
        recurring_diagnoses=recurring_diagnoses,
    )


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

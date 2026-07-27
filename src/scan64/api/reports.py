from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, col, select

from scan64.api.auth import require_player_token
from scan64.api.models import Player
from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.openings.curated import OPENING_FAMILIES
from scan64.content.openings.models import OpeningFamilyPayload
from scan64.learning.evidence.models import Evidence
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.opening_rotation import classify_opening_family
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


class OpeningFamilyReport(BaseModel):
    family_id: str
    name: str
    game_count: int
    error_rate: float
    eligible_result_count: int
    excluded_result_count: int
    win_rate: float | None


class OpeningsReport(BaseModel):
    player_id: str
    openings: list[OpeningFamilyReport]


def read_openings_report(player_id: str, session: Session) -> OpeningsReport:
    if session.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    families = [
        OpeningFamilyPayload.model_validate(item.payload) for item in OPENING_FAMILIES
    ]
    games = session.exec(select(Game).where(Game.owner_player_id == player_id)).all()
    opportunity_game_ids = {
        opportunity.game_id
        for opportunity in session.exec(
            select(PersistedLessonOpportunity)
            .join(Game, col(PersistedLessonOpportunity.game_id) == Game.id)
            .where(Game.owner_player_id == player_id)
        ).all()
    }
    grouped: dict[str, list[Game]] = defaultdict(list)
    for game in games:
        family_id = classify_opening_family(game.moves, families)
        if family_id is not None:
            grouped[family_id].append(game)

    reports: list[OpeningFamilyReport] = []
    family_by_id = {family.family_id: family for family in families}
    for family_id, family_games in sorted(grouped.items()):
        eligible_games = [
            game for game in family_games if player_id in {game.white, game.black}
        ]
        wins = sum(
            (game.result == "1-0" and game.white == player_id)
            or (game.result == "0-1" and game.black == player_id)
            for game in eligible_games
        )
        reports.append(
            OpeningFamilyReport(
                family_id=family_id,
                name=family_by_id[family_id].name,
                game_count=len(family_games),
                error_rate=sum(game.id in opportunity_game_ids for game in family_games)
                / len(family_games),
                eligible_result_count=len(eligible_games),
                excluded_result_count=len(family_games) - len(eligible_games),
                win_rate=wins / len(eligible_games) if eligible_games else None,
            )
        )
    return OpeningsReport(player_id=player_id, openings=reports)


class MasterySnapshot(BaseModel):
    concept_code: str
    mastery: float


class WeeklyReport(BaseModel):
    player_id: str
    games_played: int
    active_concepts_observed: int
    active_mastery: list[MasterySnapshot]
    top_recurring_diagnosis: DiagnosisPatternRead | None


def read_weekly_report(player_id: str, session: Session) -> WeeklyReport:
    if session.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    week_start = datetime.now(UTC) - timedelta(days=7)
    games_played = len(
        session.exec(
            select(Game).where(
                Game.owner_player_id == player_id,
                col(Game.created_at) >= week_start,
            )
        ).all()
    )
    active_skills = session.exec(
        select(SkillState).where(
            SkillState.player_id == player_id, col(SkillState.retired_at).is_(None)
        )
    ).all()
    patterns = read_player_patterns(player_id, session)
    return WeeklyReport(
        player_id=player_id,
        games_played=games_played,
        active_concepts_observed=len(active_skills),
        active_mastery=[
            MasterySnapshot(
                concept_code=skill.concept_code, mastery=skill.expected_mastery
            )
            for skill in sorted(active_skills, key=lambda skill: skill.concept_code)
        ],
        top_recurring_diagnosis=(
            max(
                patterns.recurring_diagnoses,
                key=lambda diagnosis: (
                    diagnosis.occurrence_count,
                    diagnosis.diagnosis,
                ),
            )
            if patterns.recurring_diagnoses
            else None
        ),
    )


@router.get("/v1/reports/weekly", response_model=WeeklyReport)
def get_weekly_report(
    player_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> WeeklyReport:
    require_player_token(request, player_id, session)
    return read_weekly_report(player_id, session)


@router.get("/v1/reports/openings", response_model=OpeningsReport)
def get_openings_report(
    player_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> OpeningsReport:
    require_player_token(request, player_id, session)
    return read_openings_report(player_id, session)

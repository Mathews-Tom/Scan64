from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, col, select

from scan64.api.auth import require_player_token
from scan64.api.models import Player
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedLessonOpportunity,
)
from scan64.chess.boards import board_from
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.content.openings.curated import OPENING_FAMILIES
from scan64.content.openings.models import OpeningFamilyPayload
from scan64.content.puzzle_practice import THEME_SKILLS
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.diagnosis.visibility import visible_game_lesson
from scan64.learning.evidence.models import Evidence
from scan64.learning.profiling.models import SkillState
from scan64.learning.scheduling.opening_rotation import classify_opening_family
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.learning.tolerance import review_loss
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


class PatternMoveRead(BaseModel):
    game_id: str
    ply: int
    lesson_id: str
    loss: float | None


class DiagnosisPatternSummaryRead(BaseModel):
    diagnosis: str
    name: str
    template: str
    occurrence_count: int
    game_ids: list[str]
    evidence_references: list[str]
    total_occurrences: int
    scored_occurrences: int
    observed_loss: float | None


class DiagnosisPatternRead(DiagnosisPatternSummaryRead):
    supporting_moves: list[PatternMoveRead]
    practice_themes: list[str]


class PatternsSummaryReport(BaseModel):
    player_id: str
    minimum_occurrences: int
    eligible_game_count: int
    status: Literal["insufficient_data", "no_recurring_diagnosis", "recurring_diagnosis"]
    recurring_diagnoses: list[DiagnosisPatternSummaryRead]


class PatternsReport(BaseModel):
    player_id: str
    minimum_occurrences: int
    eligible_game_count: int
    status: Literal["insufficient_data", "no_recurring_diagnosis", "recurring_diagnosis"]
    recurring_diagnoses: list[DiagnosisPatternRead]

def _skill_name(code: str) -> str:
    definition = SEED_CODES.get(code)
    return definition.name if definition is not None else "Unclassified skill"


def read_player_progress(player_id: str, session: Session) -> ProgressReport:
    player = session.get(Player, player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    skills = session.exec(select(SkillState).where(SkillState.player_id == player_id)).all()
    return ProgressReport(
        player_id=player_id,
        skills=[
            {
                "concept": skill.concept_code,
                "name": _skill_name(skill.concept_code),
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

    game_ids = set(session.exec(select(Game.id).where(Game.owner_player_id == player_id)).all())
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
    """Project retained trusted owner evidence without engines or learning writes.

    Presence is descriptive, not a result/strength or statistical claim. Full
    source FEN replay supplies occurrence links even before review backfill;
    only stored, correctly joined review probabilities supply A8 costs.
    """
    if session.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail="Player not found")

    games = {
        game.id: game
        for game in session.exec(
            select(Game)
            .join(AnalysisJob, col(AnalysisJob.game_id) == Game.id)
            .where(
                Game.owner_player_id == player_id,
                col(Game.owner_color).is_not(None),
                AnalysisJob.status == "completed",
            )
        ).all()
    }
    retired_codes = set(
        session.exec(
            select(SkillState.concept_code).where(
                SkillState.player_id == player_id, col(SkillState.retired_at).is_not(None)
            )
        ).all()
    )
    occurrences: dict[str, dict[tuple[UUID, int], PatternMoveRead]] = defaultdict(dict)
    evidence: dict[str, set[str]] = defaultdict(set)
    if games:
        rows = session.exec(
            select(PersistedLessonOpportunity)
            .where(
                col(PersistedLessonOpportunity.game_id).in_(games),
                PersistedLessonOpportunity.player_id == player_id,
            )
            .order_by(col(PersistedLessonOpportunity.id))
        ).all()
        reviews = {
            (row.game_id, row.source_position_id, row.ply): row
            for row in session.exec(
                select(GameMoveReview).where(col(GameMoveReview.game_id).in_(games))
            ).all()
        }
        # Unlike canonical EPD, full FEN preserves occurrence clocks. Never
        # match an opportunity to an arbitrary reached or other-game position.
        source_plies: dict[tuple[UUID, str], list[int]] = defaultdict(list)
        for game in games.values():
            board = board_from(game.headers.get("FEN"))
            for ply, move in enumerate(game.moves, 1):
                if ("white" if board.turn else "black") == game.owner_color:
                    source_plies[(game.id, board.fen())].append(ply)
                board.push_uci(move)
        for row in rows:
            if row.verification_status == "invalid" or row.verification_analysis_id is None:
                continue
            schedule = session.get(ReviewSchedule, (player_id, str(row.id)))
            if schedule is None or schedule.retired_at is not None:
                continue
            try:
                spec = visible_game_lesson(row, session)
            except (TypeError, ValidationError, ValueError):
                continue
            if spec is None or spec.diagnosis is None:
                continue
            code = spec.diagnosis.primary
            if code in retired_codes:
                continue
            position = session.get(Position, row.source_position_id)
            analysis = session.get(EngineAnalysis, row.verification_analysis_id)
            if (
                position is None
                or position.game_id != row.game_id
                or position.fen != spec.source.fen
                or analysis is None
                or analysis.position_id != row.source_position_id
            ):
                continue
            game = games[row.game_id]
            for ply in source_plies.get((game.id, position.fen), []):
                review = reviews.get((game.id, position.id, ply))
                loss = None
                if (
                    review is not None
                    and review.mover == game.owner_color
                    and review.move_uci == game.moves[ply - 1]
                ):
                    loss = review_loss(review.before_probability, review.after_probability)
                key = (game.id, ply)
                # Duplicate retained lessons cannot multiply observed cost.
                if key not in occurrences[code]:
                    occurrences[code][key] = PatternMoveRead(
                        game_id=str(game.id), ply=ply, lesson_id=str(row.id), loss=loss
                    )
                evidence[code].update(spec.diagnosis.evidence_refs)

    recurring_diagnoses = []
    for code, values in occurrences.items():
        game_ids = sorted({str(game_id) for game_id, _ in values})
        if len(game_ids) < 3:
            continue
        moves = sorted(values.values(), key=lambda move: (move.game_id, move.ply))
        scored_losses = [move.loss for move in moves if move.loss is not None]
        recurring_diagnoses.append(
            DiagnosisPatternRead(
                diagnosis=code,
                name=_skill_name(code),
                template=SEED_CODES[code].detection_requirements,
                occurrence_count=len(game_ids),
                game_ids=game_ids,
                evidence_references=sorted(evidence[code]),
                total_occurrences=len(moves),
                scored_occurrences=len(scored_losses),
                observed_loss=sum(scored_losses) if scored_losses else None,
                supporting_moves=moves,
                practice_themes=sorted(
                    theme for theme, skills in THEME_SKILLS.items() if code in skills
                ),
            )
        )
    recurring_diagnoses.sort(
        key=lambda pattern: (
            pattern.observed_loss is None,
            -(pattern.observed_loss if pattern.observed_loss is not None else 0.0),
            -pattern.occurrence_count,
            pattern.diagnosis,
        )
    )
    status: Literal["insufficient_data", "no_recurring_diagnosis", "recurring_diagnosis"]
    if len(games) < 3:
        status = "insufficient_data"
    elif recurring_diagnoses:
        status = "recurring_diagnosis"
    else:
        status = "no_recurring_diagnosis"
    return PatternsReport(
        player_id=player_id,
        minimum_occurrences=3,
        eligible_game_count=len(games),
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

    families = [OpeningFamilyPayload.model_validate(item.payload) for item in OPENING_FAMILIES]
    games = session.exec(select(Game).where(Game.owner_player_id == player_id)).all()
    opportunity_game_ids = {
        opportunity.game_id
        for opportunity in session.exec(
            select(PersistedLessonOpportunity)
            .join(Game, col(PersistedLessonOpportunity.game_id) == Game.id)
            .where(Game.owner_player_id == player_id)
        ).all()
        if visible_game_lesson(opportunity, session) is not None
    }
    grouped: dict[str, list[Game]] = defaultdict(list)
    for game in games:
        family_id = classify_opening_family(game.moves, families)
        if family_id is not None:
            grouped[family_id].append(game)

    reports: list[OpeningFamilyReport] = []
    family_by_id = {family.family_id: family for family in families}
    for family_id, family_games in sorted(grouped.items()):
        eligible_games = [game for game in family_games if player_id in {game.white, game.black}]
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
    name: str
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
                concept_code=skill.concept_code,
                name=_skill_name(skill.concept_code),
                mastery=skill.expected_mastery,
            )
            for skill in sorted(active_skills, key=lambda skill: skill.concept_code)
        ],
        top_recurring_diagnosis=(
            patterns.recurring_diagnoses[0]
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

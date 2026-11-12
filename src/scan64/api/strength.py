"""Separate puzzle Elo from descriptive bot results, never a playing Elo."""

from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import Session, col, select

from scan64.api.models import PlayerOnboarding
from scan64.api.onboarding import owned_player
from scan64.chess.games.assistance import SessionHintReveal
from scan64.chess.games.eligibility import session_exclusion
from scan64.chess.games.models import Game, PlaySession
from scan64.content.puzzle_practice_models import INITIAL_RATING, PuzzleRating, PuzzleRatingEvent
from scan64.persistence.database import get_session

router = APIRouter(tags=["strength"])
LEGACY_POLICY = "v1 (unstamped)"
DISCLOSURE = (
    "Puzzle Elo is separate from playing strength "
    "(initial 1200, K32, provisional below 20 results). "
    "Bot levels are relative error budgets, not human Elo. Results include only owned, completed, "
    "tracked, standard-start, non-coach Stockfish games without recorded hints; "
    "resignations count. Takebacks and external help are not certified. "
    "Trends compare the latest five with the previous five games within one level "
    "and policy version; unstamped tracked sessions are v1 (unstamped). "
    "No automatic level changes. New-skill priors remain provisional (3,2); internal 1500 is not "
    "a measured chess rating."
)


class ResultsWindow(BaseModel):
    sample_size: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0


class BotCohort(ResultsWindow):
    provider: Literal["stockfish"] = "stockfish"
    bot_level: int
    bot_policy_version: str
    latest: ResultsWindow
    previous: ResultsWindow
    trend: Literal["improving", "steady", "declining", "insufficient_data"]


class PuzzleEventRead(BaseModel):
    sequence: int
    before: float
    after: float
    score: int


class StrengthRead(BaseModel):
    declared_level: str | None
    suggested_bot_level: int | None
    puzzle_rating: float
    puzzle_rated_count: int
    puzzle_provisional: bool
    puzzle_events: list[PuzzleEventRead]
    bot_cohorts: list[BotCohort]
    disclosure: str = DISCLOSURE


def results_window(scores: list[float]) -> ResultsWindow:
    return ResultsWindow(
        sample_size=len(scores),
        wins=scores.count(1.0),
        draws=scores.count(0.5),
        losses=scores.count(0.0),
    )


def read_strength(player_id: str, session: Session) -> StrengthRead:
    onboarding = session.get(PlayerOnboarding, player_id)
    rating = session.get(PuzzleRating, player_id)
    events = session.exec(
        select(PuzzleRatingEvent)
        .where(PuzzleRatingEvent.player_id == player_id)
        .order_by(col(PuzzleRatingEvent.sequence))
    ).all()
    hinted = set(
        session.exec(
            select(SessionHintReveal.session_id)
            .join(PlaySession, col(PlaySession.id) == SessionHintReveal.session_id)
            .where(PlaySession.player_id == player_id)
        ).all()
    )
    # A resumed/continued game can have several sessions. Count each game once and
    # conservatively reject it if any associated session is ineligible or hinted.
    games: dict[str, tuple[Game, list[PlaySession]]] = {}
    rows = session.exec(
        select(Game, PlaySession)
        .join(PlaySession, col(PlaySession.game_id) == Game.id)
        .where(Game.owner_player_id == player_id)
        .order_by(col(Game.created_at).desc(), col(Game.id).desc())
    ).all()
    for game, play in rows:
        entry = games.setdefault(str(game.id), (game, []))
        entry[1].append(play)
    cohorts: dict[tuple[int, str], list[float]] = defaultdict(list)
    for game, plays in games.values():
        if len(plays) != 1:
            continue
        play = plays[0]
        if session_exclusion(game, play) is not None or play.id in hinted:
            continue
        config = play.opponent_config
        if config.get("provider", "stockfish") != "stockfish":
            continue
        level = config.get("bot_level", "")
        if level not in {str(number) for number in range(1, 9)}:
            continue
        color = config.get("player_color") or game.owner_color
        if color not in {"white", "black"}:
            continue
        version = config.get("bot_policy_version", LEGACY_POLICY)
        if not version:
            continue
        score = (
            0.5
            if game.result == "1/2-1/2"
            else float(game.result == ("1-0" if color == "white" else "0-1"))
        )
        cohorts[(int(level), version)].append(score)
    projected: list[BotCohort] = []
    for (level_number, version), scores in sorted(cohorts.items()):
        latest, previous = scores[:5], scores[5:10]
        trend: Literal["improving", "steady", "declining", "insufficient_data"]
        if len(scores) < 10:
            trend = "insufficient_data"
        elif sum(latest) > sum(previous):
            trend = "improving"
        elif sum(latest) < sum(previous):
            trend = "declining"
        else:
            trend = "steady"
        projected.append(
            BotCohort(
                **results_window(scores).model_dump(),
                bot_level=level_number,
                bot_policy_version=version,
                latest=results_window(latest),
                previous=results_window(previous),
                trend=trend,
            )
        )
    count = rating.rated_count if rating is not None else 0
    return StrengthRead(
        declared_level=onboarding.declared_level if onboarding is not None else None,
        suggested_bot_level=onboarding.suggested_bot_level if onboarding is not None else None,
        puzzle_rating=rating.rating if rating is not None else INITIAL_RATING,
        puzzle_rated_count=count,
        puzzle_provisional=count < 20,
        puzzle_events=[PuzzleEventRead.model_validate(event.model_dump()) for event in events],
        bot_cohorts=projected,
    )


@router.get("/v1/players/{player_id}/strength", response_model=StrengthRead)
def get_strength(
    player_id: str, request: Request, session: Session = Depends(get_session)
) -> StrengthRead:
    owned_player(request, player_id, session)
    return read_strength(player_id, session)

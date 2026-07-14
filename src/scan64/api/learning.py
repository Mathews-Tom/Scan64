from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from scan64.chess.analysis.models import PersistedLessonOpportunity
from scan64.content.endgames.curated import ENDGAME_PUZZLES
from scan64.content.famous_games.curated import FAMOUS_GAMES
from scan64.content.openings.curated import OPENING_FAMILIES
from scan64.learning.scheduling.composer import SessionComposer
from scan64.learning.scheduling.priority import PriorityFactors
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.lessonspec.models import (
    AcceptedMove,
    Diagnosis,
    Interaction,
    LessonSpec,
    Objective,
    Source,
    Verification,
)
from scan64.persistence.database import get_session

router = APIRouter(prefix="/v1/learning", tags=["learning"])

def make_endgame_spec(puzzle: dict[str, Any]) -> LessonSpec:
    import chess
    board = chess.Board(puzzle["fen"])
    san_moves = []
    for uci in puzzle["solution"]:
        move = chess.Move.from_uci(uci)
        san_moves.append(board.san(move))
        board.push(move)
    return LessonSpec(
        schema_version="1.0",
        lesson_id=puzzle["id"],
        source=Source(kind="custom", fen=puzzle["fen"]),
        diagnosis=Diagnosis(primary="endgame", confidence=1.0),
        objective=Objective(type="find_best_move", instruction="Win the endgame."),
        interaction=Interaction(
            input="click",
            maximum_attempts=3,
            accepted_moves=[AcceptedMove(san=m) for m in san_moves]
        ),
        verification=Verification(status="verified", engine="syzygy")
    )

def make_opening_spec(family_item: Any) -> LessonSpec:
    payload = family_item.payload
    name = payload.get("name", "Unknown Opening")
    moves = payload.get("moves", [])

    import chess
    board = chess.Board()
    for m in moves[:-1]:
        board.push_san(m)
    fen = board.fen()
    last_move = moves[-1] if moves else "e4"

    return LessonSpec(
        schema_version="1.0",
        lesson_id=f"opening_{name.replace(' ', '_').lower()}",
        source=Source(
            kind="custom", fen=fen
        ),
        diagnosis=Diagnosis(primary="opening", confidence=1.0),
        objective=Objective(type="find_best_move", instruction=f"Play the {name}."),
        interaction=Interaction(
            input="click",
            maximum_attempts=3,
            accepted_moves=[AcceptedMove(san=last_move)]
        ),
        verification=Verification(status="verified", engine="expert")
    )

def make_famous_game_spec(game: Any, decision: Any) -> LessonSpec:
    return LessonSpec(
        schema_version="1.0",
        lesson_id=f"{game.id}_{decision.id}",
        source=Source(kind="custom", fen=decision.fen),
        diagnosis=Diagnosis(primary="tactics", confidence=1.0),
        objective=Objective(type="find_best_move", instruction=decision.prompt),
        interaction=Interaction(
            input="click",
            maximum_attempts=3,
            accepted_moves=[AcceptedMove(san=m) for m in decision.accepted_moves]
        ),
        verification=Verification(status="verified", engine="expert")
    )

@router.get("/session", response_model=list[LessonSpec])
def get_training_session(player_id: str, db: Session = Depends(get_session)) -> list[LessonSpec]:
    now = datetime.now(UTC)

    # 1. Gather all potential items
    pool: list[dict[str, Any]] = []

    # Endgames
    for eg in ENDGAME_PUZZLES:
        pool.append({
            "id": eg["id"],
            "source": "m15_tablebase",
            "content_type": "endgame",
            "spec": make_endgame_spec(eg),
            "base_priority": 0.5
        })

    # Openings
    for op in OPENING_FAMILIES:
        spec = make_opening_spec(op)
        pool.append({
            "id": spec.lesson_id,
            "source": "m16_opening",
            "content_type": "opening",
            "spec": spec,
            "base_priority": 0.5
        })

    # Famous Games
    for fg in FAMOUS_GAMES:
        for dec in fg.payload.decisions:
            spec = make_famous_game_spec(fg, dec)
            pool.append({
                "id": spec.lesson_id,
                "source": "m17_famous_game",
                "content_type": "famous_game",
                "spec": spec,
                "base_priority": 0.6
            })

    # Persisted M9 Opportunities
    opportunities = db.exec(select(PersistedLessonOpportunity)).all()
    for opp in opportunities:
        spec = LessonSpec.model_validate(opp.lesson_spec)
        pool.append({
            "id": str(opp.id),
            "source": "m9_exercise",
            "content_type": "exercise",
            "spec": spec,
            "base_priority": 0.9 # High priority for actual mistakes
        })

    # 2. Attach scheduling metadata (ReviewSchedule)
    for item in pool:
        schedule = db.get(ReviewSchedule, (player_id, item["id"]))
        if schedule:
            is_due = schedule.is_due(now)
            item["type"] = "due" if is_due else "exploration"
            pf = PriorityFactors(
                review_due=1.0 if is_due else 0.0,
                weakness_severity=0.8 if item["content_type"] == "exercise" else 0.0,
                user_interest=0.5 if item["content_type"] == "famous_game" else 0.0
            )
            item["priority"] = pf.compute_priority(session_fatigue=0.0)
        else:
            item["type"] = "exploration" if item["content_type"] != "exercise" else "mistakes"
            pf = PriorityFactors(
                review_due=0.0,
                weakness_severity=0.8 if item["content_type"] == "exercise" else 0.0,
                user_interest=0.5 if item["content_type"] == "famous_game" else 0.0,
                curriculum_relevance=0.8 if item["content_type"] in ("endgame", "opening") else 0.0
            )
            item["priority"] = pf.compute_priority(session_fatigue=0.0)

    # 3. Compose session
    composer = SessionComposer()
    composed_session = composer.compose_session(pool, session_size=5)

    return [item["spec"] for item in composed_session]

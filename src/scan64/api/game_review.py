"""Engine-free review projection; policy is computed from immutable fast inputs."""

from uuid import UUID

from chess_lesson_spec import LessonSpec
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, col, select

from scan64.api.learning import _reverify_persisted_lesson
from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview, PersistedLessonOpportunity
from scan64.chess.analysis.review import REVIEW_NODES
from scan64.chess.boards import board_from
from scan64.chess.games.models import Game
from scan64.content.models import LessonAttempt, StudySession
from scan64.learning.diagnosis.visibility import visible_game_lesson
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.learning.tolerance import ReviewBand, review_band, review_loss


class ReviewMoveRead(BaseModel):
    ply: int
    source_fen: str
    fen: str
    move_uci: str
    san: str
    mover: str
    before_probability: float | None
    after_probability: float | None
    white_probability: float | None
    loss: float | None
    band: ReviewBand | None
    is_owner_move: bool


class ReviewKeyMomentRead(BaseModel):
    ply: int
    lesson_id: str
    lesson: LessonSpec
    attempts_used: int
    attempts_remaining: int


class GameReviewRead(BaseModel):
    game_id: UUID
    owner_color: str | None
    initial_fen: str
    initial_white_probability: float | None
    moves: list[ReviewMoveRead]
    accuracy: float | None
    scored_moves: int
    total_owner_moves: int
    band_counts: dict[str, int]
    key_moments: list[ReviewKeyMomentRead]
    session_id: str | None
    backfill_required: bool
    nodes: int = REVIEW_NODES


def _key_moments(
    game: Game, moves: list[ReviewMoveRead], rows: dict[int, GameMoveReview], session: Session
) -> list[ReviewKeyMomentRead]:
    moments = []
    used_lessons: set[UUID] = set()
    # Read the floor dynamically, just like the bands; no threshold is persisted.
    from scan64.learning.tolerance import SOUND_MOVE_WIN_PROBABILITY_LOSS

    ranked = sorted(
        (
            move
            for move in moves
            if move.is_owner_move
            and move.loss is not None
            and move.loss > SOUND_MOVE_WIN_PROBABILITY_LOSS
        ),
        key=lambda move: (-(move.loss if move.loss is not None else 0.0), move.ply),
    )
    lessons = session.exec(
        select(PersistedLessonOpportunity)
        .where(
            PersistedLessonOpportunity.game_id == game.id,
            PersistedLessonOpportunity.player_id == game.owner_player_id,
        )
        .order_by(col(PersistedLessonOpportunity.id))
    ).all()
    for move in ranked:
        row = rows[move.ply]
        for opportunity in lessons:
            if (
                opportunity.id in used_lessons
                or opportunity.source_position_id != row.source_position_id
                or opportunity.verification_status == "invalid"
                or opportunity.verification_analysis_id is None
            ):
                continue
            # A kept, trusted lesson must reverify against its actual stored
            # source analysis, never whichever newer/deeper analysis is handy.
            analysis_id = opportunity.verification_analysis_id
            analysis = session.get(EngineAnalysis, analysis_id)
            if (
                analysis is None
                or analysis.position_id != opportunity.source_position_id
                or not analysis.raw_result
            ):
                continue
            schedule = session.get(ReviewSchedule, (game.owner_player_id, str(opportunity.id)))
            if schedule is None or visible_game_lesson(opportunity, session) is None:
                continue
            spec, _ = _reverify_persisted_lesson(opportunity, session, allow_fallback=False)
            session.add(opportunity)
            if (
                spec is None
                or spec.diagnosis is None
                or opportunity.verification_analysis_id != analysis_id
            ):
                continue
            spec.diagnosis.secondary = []
            spec.lesson_id = str(opportunity.id)
            attempts = session.exec(
                select(func.count())
                .select_from(LessonAttempt)
                .where(
                    LessonAttempt.player_id == game.owner_player_id,
                    LessonAttempt.opportunity_id == opportunity.id,
                )
            ).one()
            moments.append(
                ReviewKeyMomentRead(
                    ply=move.ply,
                    lesson_id=str(opportunity.id),
                    lesson=spec,
                    attempts_used=attempts,
                    attempts_remaining=max(0, spec.interaction.maximum_attempts - attempts),
                )
            )
            used_lessons.add(opportunity.id)
            break
        if len(moments) == 3:
            break
    return moments


def build_game_review(
    game: Game, session: Session, *, include_key_moments: bool = True
) -> GameReviewRead:
    rows = {
        row.ply: row
        for row in session.exec(
            select(GameMoveReview).where(GameMoveReview.game_id == game.id)
        ).all()
    }
    board = board_from(game.headers.get("FEN"))
    initial_fen = board.fen()
    initial_probability = rows[1].before_probability if 1 in rows else None
    initial_white_probability = (
        (initial_probability if board.turn else 1.0 - initial_probability)
        if initial_probability is not None
        else None
    )
    moves = []
    counts = {"sound": 0, "inaccuracy": 0, "mistake": 0, "blunder": 0, "unscored": 0}
    owner_losses = []
    total_owner_moves = 0
    for ply, uci in enumerate(game.moves, 1):
        source_fen = board.fen()
        mover = "white" if board.turn else "black"
        move = board.parse_uci(uci)
        san = board.san(move)
        board.push(move)
        row = rows.get(ply)
        before = row.before_probability if row is not None else None
        after = row.after_probability if row is not None else None
        loss = review_loss(before, after)
        is_owner = game.owner_color == mover
        band = review_band(loss) if is_owner else None
        if is_owner:
            total_owner_moves += 1
            counts[review_band(loss)] += 1
            if loss is not None:
                owner_losses.append(loss)
        moves.append(
            ReviewMoveRead(
                ply=ply,
                source_fen=source_fen,
                fen=board.fen(),
                move_uci=uci,
                san=san,
                mover=mover,
                before_probability=before,
                after_probability=after,
                white_probability=(after if board.turn else 1.0 - after)
                if after is not None
                else None,
                loss=loss,
                band=band,
                is_owner_move=is_owner,
            )
        )
    moments = _key_moments(game, moves, rows, session) if include_key_moments else []
    study_session = None
    if moments:
        domain = f"game_analysis:{game.id}"
        study_session = session.exec(
            select(StudySession)
            .where(StudySession.player_id == game.owner_player_id, StudySession.domain == domain)
            .order_by(col(StudySession.started_at).desc())
        ).first()
        if study_session is None:
            assert game.owner_player_id is not None
            study_session = StudySession(player_id=game.owner_player_id, domain=domain)
            session.add(study_session)
    if include_key_moments:
        session.commit()
    return GameReviewRead(
        game_id=game.id,
        owner_color=game.owner_color,
        initial_fen=initial_fen,
        initial_white_probability=initial_white_probability,
        moves=moves,
        accuracy=100.0 * (1.0 - sum(owner_losses) / len(owner_losses)) if owner_losses else None,
        scored_moves=len(owner_losses),
        total_owner_moves=total_owner_moves,
        band_counts=counts,
        key_moments=moments,
        session_id=study_session.id if study_session is not None else None,
        backfill_required=any(ply not in rows for ply in range(1, len(game.moves) + 1)),
    )

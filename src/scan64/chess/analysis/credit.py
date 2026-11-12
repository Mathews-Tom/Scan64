"""Conservative source-position proofs and transactional, prospective credit.

Review and archive validation use the same pure replay as the writer. Nothing in
this module searches an engine or turns a negative detector into positive proof.
"""

import math
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import chess
from sqlalchemy import or_, update
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, col, select

from scan64.chess.analysis.credit_models import PositiveMoveCredit
from scan64.chess.analysis.models import EngineAnalysis, GameMoveReview, PersistedLessonOpportunity
from scan64.chess.games.assistance import SessionHintReveal
from scan64.chess.games.eligibility import session_exclusion
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.content.models import LessonAttempt
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evidence.composer import exchange_material_gain, line_material_gain
from scan64.learning.profiling.models import ProfileObservation, SkillState
from scan64.learning.profiling.priors import get_prior_for_rating
from scan64.learning.tolerance import review_band, review_loss, win_probability

PROOF_VERSION = 1
CAPTURE_SKILL = "threat_processing.missed_capture"
CHECK_SKILL = "threat_processing.missed_check"
BEFORE_END = "analysed before the game ended"
MAX_POSITIVE_CREDITS_PER_GAME_SKILL = 2
PER_GAME_CREDIT_CAP_REACHED = "per-game credit cap reached"


def _credit_priority(
    proof: dict[str, Any], source_analysis: EngineAnalysis | None = None
) -> tuple[int, float, int]:
    """Prefer mate, then gain/ply, without changing v1 capture proof archives."""
    mate = (
        source_analysis.raw_result[0].get("score_mate")
        if source_analysis is not None and source_analysis.raw_result
        else proof.get("score_mate")
    )
    if isinstance(mate, int) and not isinstance(mate, bool) and mate > 0:
        return (0, 0.0, int(proof["ply"]))
    gain = proof.get("net_material_gain", proof["material_gain"])
    return (1, -float(gain), int(proof["ply"]))


def positive_move_proof(
    row: GameMoveReview,
    source: Position,
    analysis: EngineAnalysis,
    *,
    predecessor: Position | None = None,
    preceding_move_uci: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Replay the complete stored first PV; return proof or an honest exclusion.

    The previous occurrence is required for captures: a recapture that merely
    restores material is not evidence of finding a profitable opportunity.
    Scores and both A8 probabilities must be available, not a legacy unscored PV.
    """
    if (
        row.source_position_id != source.id
        or row.before_analysis_id != analysis.id
        or analysis.position_id != source.id
        or not analysis.raw_result
    ):
        return None, "missing source analysis"
    probabilities = (row.before_probability, row.after_probability)
    if any(
        value is None or not math.isfinite(value) or not 0 <= value <= 1
        for value in probabilities
    ):
        return None, "unscored move"
    if review_band(review_loss(*probabilities)) != "sound":
        return None, "move is not A8 sound"
    first = analysis.raw_result[0]
    if not isinstance(first, dict):
        return None, "invalid source analysis"
    score = first.get("score_mate")
    if score is None:
        score = first.get("score_cp")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None, "unscored source analysis"
    mate_score = first.get("score_mate")
    if mate_score is not None and (not isinstance(mate_score, int) or mate_score == 0):
        return None, "invalid source score"
    try:
        if not math.isfinite(score):
            return None, "invalid source score"
        probability = win_probability(first)
    except (TypeError, ValueError, OverflowError):
        return None, "invalid source score"
    if probability is None:
        return None, "unscored source analysis"
    if row.before_probability != probability:
        return None, "source score does not match review"
    line = first.get("pv")
    if not isinstance(line, list) or not line or not all(isinstance(uci, str) for uci in line):
        return None, "invalid proving line"
    if line[0] != row.move_uci:
        return None, "not the first source PV move"
    try:
        board = chess.Board(source.fen)
        if not board.is_valid() or source.canonical_id != board.epd():
            return None, "invalid source position"
        move = board.parse_uci(row.move_uci)
        if row.mover != ("white" if board.turn else "black"):
            return None, "invalid move owner"
        if row.san != board.san(move):
            return None, "invalid move notation"
        gain = line_material_gain(board, line)
        if gain is None:
            return None, "illegal or truncated proving line"
        replay = board.copy()
        for uci in line:
            replay.push_uci(uci)
    except (TypeError, ValueError, IndexError):
        return None, "illegal or truncated proving line"
    if replay.is_check() and not replay.is_checkmate():
        return None, "illegal or truncated proving line"
    proof: dict[str, Any] = {
        "proof_version": PROOF_VERSION,
        "canonical_id": source.canonical_id,
        "ply": row.ply,
        "move_uci": row.move_uci,
        "source_analysis_id": str(row.before_analysis_id),
        "reached_analysis_id": str(row.after_analysis_id),
        "line": list(line),
        "material_gain": gain,
        "assistance": "no in-app hint",
    }
    if board.is_capture(move):
        if board.is_check():
            return None, "capture was a reply to check"
        exchange_gain = exchange_material_gain(board, line)
        if exchange_gain is None or exchange_gain < 1:
            return None, "capture does not prove a profitable exchange"
        # Do not accept a PV cut off before an immediate recapture on the
        # exchange square. Full legal replay alone would price a hung queen as
        # a free pawn if the stored line stopped at Qxp.
        exchange = board.copy()
        target = move.to_square
        for uci in line:
            exchange_move = exchange.parse_uci(uci)
            if not exchange.is_capture(exchange_move) or exchange_move.to_square != target:
                break
            exchange.push(exchange_move)
        else:
            if any(
                candidate.to_square == target and exchange.is_capture(candidate)
                for candidate in exchange.legal_moves
            ):
                return None, "truncated capture exchange"
        if predecessor is None or preceding_move_uci is None:
            return None, "missing opponent preceding move"
        try:
            previous_board = chess.Board(predecessor.fen)
            if not previous_board.is_valid() or previous_board.turn == board.turn:
                return None, "invalid opponent preceding move"
            preceding = previous_board.parse_uci(preceding_move_uci)
            reached = previous_board.copy()
            reached.push(preceding)
            if reached.fen() != board.fen():
                return None, "invalid opponent preceding move"
            previous_gain = line_material_gain(previous_board, [preceding_move_uci, *line])
        except (TypeError, ValueError):
            return None, "invalid opponent preceding move"
        if previous_gain is None or -previous_gain < 1:
            return None, "recapture does not gain material overall"
        proof.update(
            skill_id=CAPTURE_SKILL,
            exchange_gain=exchange_gain,
            net_material_gain=-previous_gain,
            predecessor_fen=predecessor.fen,
            preceding_move_uci=preceding_move_uci,
            explanation=(
                f"{row.san} finds a profitable capture: the exchange gains {exchange_gain} "
                f"material point(s), and gains {-previous_gain} overall including the "
                "opponent's preceding move. No in-app hint was revealed at this position."
            ),
        )
    elif board.gives_check(move):
        mate = first.get("score_mate")
        winning_mate = (
            isinstance(mate, int)
            and not isinstance(mate, bool)
            and mate > 0
        )
        if not winning_mate and gain < 1:
            return None, "check does not prove mate or a material gain"
        proof.update(
            skill_id=CHECK_SKILL,
            score_mate=mate if winning_mate else None,
            explanation=(
                f"{row.san} finds a winning check: "
                + (
                    "the stored source analysis proves mate. "
                    if winning_mate
                    else f"the legal stored line gains {gain} material point(s). "
                )
                + "No in-app hint was revealed at this position."
            ),
        )
    else:
        return None, "no supported tactical proof"
    if proof["skill_id"] not in TRUSTED_CODES:
        return None, "untrusted skill"
    return proof, None


def _session_exclusion(game: Game, play: PlaySession | None) -> str | None:
    if (
        play is not None
        and play.player_id == game.owner_player_id
        and play.credit_analysis_status == BEFORE_END
    ):
        return BEFORE_END
    return session_exclusion(game, play)


def _lesson_sources(session: Session, player_id: str) -> set[tuple[str, str]]:
    """Previously studied sources include attempted legacy opportunities too."""
    attempted = select(LessonAttempt.opportunity_id).where(LessonAttempt.player_id == player_id)
    lessons = session.exec(
        select(PersistedLessonOpportunity, Position)
        .join(Position, col(Position.id) == PersistedLessonOpportunity.source_position_id)
        .where(
            or_(
                col(PersistedLessonOpportunity.player_id) == player_id,
                col(PersistedLessonOpportunity.id).in_(attempted),
            )
        )
    ).all()
    blocked = set()
    for lesson, source in lessons:
        diagnosis = lesson.lesson_spec.get("diagnosis")
        skill = diagnosis.get("primary") if isinstance(diagnosis, dict) else None
        if isinstance(skill, str):
            blocked.add((source.canonical_id, skill))
    return blocked


def _failed_sources(
    rows: Iterable[GameMoveReview],
    positions: dict[UUID, Position],
    observations: Iterable[tuple[str, str]],
) -> set[tuple[str, str]]:
    # ProfileObservation.position_id is REACHED, not source. Repetitions may
    # have different Position ids/counters; collapse only after mapping every
    # occurrence back to its own source.
    sources: dict[str, set[str]] = {}
    for row in rows:
        source = positions.get(row.source_position_id)
        if source is not None:
            sources.setdefault(str(row.reached_position_id), set()).add(source.canonical_id)
    return {
        (canonical_id, skill)
        for reached, skill in observations
        for canonical_id in sources.get(reached, ())
    }


def project_move_credits(
    game: Game,
    session: Session,
    *,
    staged_observations: Iterable[tuple[UUID, str]] = (),
    _for_award: bool = False,
) -> dict[int, tuple[dict[str, Any] | None, str | None]]:
    """Engine-free projection only. GET/backfill cannot insert or award credit."""
    if game.owner_player_id is None:
        return {}
    player_id = game.owner_player_id
    plays = session.exec(select(PlaySession).where(PlaySession.game_id == game.id)).all()
    # Multiple linked sessions cannot establish one assistance history safely.
    play = plays[0] if len(plays) == 1 else None
    session_exclusion = _session_exclusion(game, play)
    rows = session.exec(select(GameMoveReview).where(GameMoveReview.game_id == game.id)).all()
    by_ply = {row.ply: row for row in rows}
    positions = {
        position.id: position
        for position in session.exec(select(Position).where(Position.game_id == game.id)).all()
    }
    analyses = {
        analysis.id: analysis
        for analysis in session.exec(
            select(EngineAnalysis).join(Position).where(Position.game_id == game.id)
        ).all()
    }
    credits = {
        credit.ply: credit
        for credit in session.exec(
            select(PositiveMoveCredit).where(
                PositiveMoveCredit.game_id == game.id, PositiveMoveCredit.player_id == player_id
            )
        ).all()
    }
    hinted: set[str] = set()
    if play is not None:
        hinted = {
            reveal.canonical_id
            for reveal in session.exec(
                select(SessionHintReveal).where(
                    SessionHintReveal.session_id == play.id,
                    SessionHintReveal.player_id == player_id,
                )
            ).all()
        }
    blocked_lessons = _lesson_sources(session, player_id)
    observations = [
        (observation.position_id, observation.skill_id)
        for observation in session.exec(
            select(ProfileObservation).where(
                ProfileObservation.player_id == player_id,
                ProfileObservation.game_id == str(game.id),
            )
        ).all()
    ]
    observations.extend((str(reached), skill) for reached, skill in staged_observations)
    blocked_failures = _failed_sources(rows, positions, observations)
    retired = {
        skill.concept_code
        for skill in session.exec(
            select(SkillState).where(
                SkillState.player_id == player_id, col(SkillState.retired_at).is_not(None)
            )
        ).all()
    }
    initial_ply = 0
    if play is not None:
        try:
            initial_ply = int(play.opponent_config.get("initial_ply", "0"))
        except (TypeError, ValueError):
            session_exclusion = "unknown session starting prefix"
    result: dict[int, tuple[dict[str, Any] | None, str | None]] = {}
    for row in sorted(rows, key=lambda occurrence: occurrence.ply):
        if row.mover != game.owner_color:
            continue
        if row.ply in credits:
            credit = credits[row.ply]
            result[row.ply] = (
                {
                    **credit.proof,
                    "skill_id": credit.skill_id,
                    "credited_at": credit.credited_at.isoformat(),
                    "credited": True,
                },
                None,
            )
            continue
        exclusion = session_exclusion
        source = positions.get(row.source_position_id)
        analysis = analyses.get(row.before_analysis_id)
        if exclusion is None and row.ply <= initial_ply:
            exclusion = "imported or previously studied history"
        if exclusion is None and source is not None and source.canonical_id in hinted:
            exclusion = "in-app hint revealed at this position"
        if exclusion is not None:
            result[row.ply] = (None, exclusion)
            continue
        if source is None or analysis is None:
            result[row.ply] = (None, "missing source analysis")
            continue
        previous = by_ply.get(row.ply - 1)
        proof, exclusion = positive_move_proof(
            row,
            source,
            analysis,
            predecessor=(
                positions.get(previous.source_position_id) if previous is not None else None
            ),
            preceding_move_uci=previous.move_uci if previous is not None else None,
        )
        if proof is not None:
            key = (source.canonical_id, proof["skill_id"])
            if proof["skill_id"] in retired:
                proof, exclusion = None, "retired skill"
            elif key in blocked_lessons:
                proof, exclusion = None, "same-source lesson or attempt already records this skill"
            elif key in blocked_failures:
                proof, exclusion = None, "same-source failed observation already records this skill"
        result[row.ply] = (proof, exclusion)
    # Existing ledger rows reserve slots even after a recovered/lost job marker.
    # Choose across every eligible source before the first alpha update; input
    # order must not consume the budget before a stronger later tactic is seen.
    counts = Counter(credit.skill_id for credit in credits.values())
    sources = {
        (credit.canonical_id, credit.skill_id): credit.ply for credit in credits.values()
    }
    candidates = [
        (ply, proof) for ply, (proof, _) in result.items()
        if proof is not None and not proof.get("credited")
    ]
    for ply, proof in sorted(
        candidates,
        key=lambda item: _credit_priority(
            item[1], analyses.get(by_ply[item[0]].before_analysis_id)
        ),
    ):
        skill_id = proof["skill_id"]
        key = (proof["canonical_id"], skill_id)
        if key in sources:
            result[ply] = (
                None, f"same position and skill already credited at ply {sources[key]}"
            )
        elif counts[skill_id] >= MAX_POSITIVE_CREDITS_PER_GAME_SKILL:
            result[ply] = (None, PER_GAME_CREDIT_CAP_REACHED)
        elif _for_award:
            counts[skill_id] += 1
            sources[key] = ply
        else:
            result[ply] = (None, "not credited by full analysis")
    return result


def apply_positive_move_credits(
    game: Game,
    session: Session,
    *,
    rating: int,
    staged_observations: Iterable[tuple[UUID, str]] = (),
) -> None:
    """Claim the analysis once and award with its surrounding full transaction.

    The session UPDATE is the write fence: completion/hints/moves have finished
    before eligibility is re-read. The unique ledger INSERT wins before alpha is
    touched, so retry/recovery cannot turn one source into multiple successes.
    """
    plays = session.exec(select(PlaySession).where(PlaySession.game_id == game.id)).all()
    if len(plays) != 1:
        return
    play = plays[0]
    if play.player_id != game.owner_player_id:
        return
    # Acquire the database writer fence, not an in-memory per-worker lock.
    claimed = session.execute(
        update(PlaySession)
        .where(col(PlaySession.id) == play.id, col(PlaySession.credit_analysis_status).is_(None))
        .values(credit_analysis_status="completed")
        .returning(col(PlaySession.id))
    ).first()
    if claimed is None:
        return
    session.refresh(play)
    session.refresh(game)
    exclusion = _session_exclusion(game, play)
    if exclusion is not None:
        play.credit_analysis_status = exclusion
        session.add(play)
        return
    projected = project_move_credits(
        game, session, staged_observations=staged_observations, _for_award=True
    )
    now = datetime.now(UTC)
    for ply, (proof, exclusion) in projected.items():
        if proof is None or exclusion is not None or proof.get("credited"):
            continue
        skill_id = proof["skill_id"]
        if skill_id not in TRUSTED_CODES:
            continue
        skill = session.get(SkillState, (play.player_id, skill_id), populate_existing=True)
        if skill is not None and skill.retired_at is not None:
            continue
        inserted = session.execute(
            insert(PositiveMoveCredit)
            .values(
                player_id=play.player_id,
                game_id=game.id,
                canonical_id=proof["canonical_id"],
                skill_id=skill_id,
                ply=ply,
                source_analysis_id=UUID(proof["source_analysis_id"]),
                reached_analysis_id=UUID(proof["reached_analysis_id"]),
                proof=proof,
                proof_version=PROOF_VERSION,
                credited_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["player_id", "game_id", "canonical_id", "skill_id"]
            )
            .returning(col(PositiveMoveCredit.player_id))
        ).first()
        if inserted is None:
            continue
        if skill is None:
            alpha, beta = get_prior_for_rating(rating)
            skill = SkillState(
                player_id=play.player_id,
                concept_code=skill_id,
                alpha=alpha,
                beta=beta,
                prior_alpha=alpha,
                prior_beta=beta,
            )
        skill.apply_observation(success=True, timestamp=now)
        session.add(skill)
        session.flush()

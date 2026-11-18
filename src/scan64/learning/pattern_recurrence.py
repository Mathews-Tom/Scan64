"""Read-only, descriptive recurrence in saved online games, not habit eligibility."""

from __future__ import annotations

import io
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import batched
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import chess
import chess.pgn
from chess_lesson_spec import LessonSpec
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select as sql_select
from sqlmodel import Session, col, select

from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedLessonOpportunity,
)
from scan64.chess.boards import board_from
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.games.online_import import STANDARD_VARIANTS, source_identity
from scan64.chess.positions.models import Position
from scan64.content.models import ContentAttempt, ContentItem, LessonAttempt, StudySession
from scan64.content.puzzle_runs import PuzzleRun
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.diagnosis.visibility import visible_game_lesson
from scan64.learning.evidence.composer import compose_candidate_evidence, needs_mate_source
from scan64.learning.evidence.models import Evidence
from scan64.learning.evidence.support import supports_saved_diagnosis
from scan64.learning.plugins.host_registry import get_host_registry
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind
from scan64.learning.progress import progress_reset_at
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson

CAVEAT = (
    "Descriptive association only: training did not necessarily cause a change. "
    "95% Wilson intervals assume independent owner moves; moves within games are correlated. "
    "Occurrences are trusted primary diagnoses at saved >150cp fast-pass candidates, "
    "not all errors. "
    "Puzzle themes are related recognition practice, not proof of prevention or chess strength."
)


class RecurrencePeriod(BaseModel):
    candidate_games: int = 0
    games: int = 0
    owner_moves: int = 0
    occurrences: int | None = None
    per_100_moves: float | None = None
    wilson_95_per_100: tuple[float, float] | None = None
    excluded: dict[str, int] = Field(default_factory=dict)


class PatternRecurrence(BaseModel):
    code: str
    name: str
    first_training_at: datetime | None
    status: Literal["comparison", "not_enough_games"]
    reason: str
    before: RecurrencePeriod
    after: RecurrencePeriod


class PatternRecurrenceReport(BaseModel):
    player_id: str
    minimum_games_per_period: int = 3
    minimum_owner_moves_per_game: int = 20
    coverage: dict[str, int]
    patterns: list[PatternRecurrence]
    caveat: str = CAVEAT


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _first_training(player_id: str, session: Session) -> dict[str, datetime]:
    boundary = progress_reset_at(player_id, session)
    anchors: dict[str, datetime] = {}

    def record(codes: set[str], completed: datetime) -> None:
        when = _utc(completed)
        if boundary is not None and when < boundary:
            return
        for code in codes & TRUSTED_CODES:
            if code not in anchors or when < anchors[code]:
                anchors[code] = when

    lesson_rows = (
        session.execute(
            sql_select(
                LessonAttempt,
                StudySession,
                PersistedLessonOpportunity,
                Game,
                Position,
                EngineAnalysis,
            )
            .join(StudySession, col(StudySession.id) == col(LessonAttempt.session_id))
            .join(
                PersistedLessonOpportunity,
                col(PersistedLessonOpportunity.id) == col(LessonAttempt.opportunity_id),
            )
            .join(Game, col(Game.id) == col(PersistedLessonOpportunity.game_id))
            .join(Position, col(Position.id) == col(PersistedLessonOpportunity.source_position_id))
            .join(
                EngineAnalysis,
                col(EngineAnalysis.id) == col(PersistedLessonOpportunity.verification_analysis_id),
            )
            .where(
                col(LessonAttempt.player_id) == player_id,
                col(StudySession.player_id) == player_id,
                col(PersistedLessonOpportunity.player_id) == player_id,
                col(Game.owner_player_id) == player_id,
                col(LessonAttempt.grading_status) == "verified",
                col(LessonAttempt.source_kind) == "persisted_opportunity",
                col(PersistedLessonOpportunity.verification_status) == "verified",
            )
        )
        .tuples()
        .all()
    )
    for attempt, study, opportunity, game, position, analysis in lesson_rows:
        if (
            attempt.lesson_id != str(opportunity.id)
            or attempt.success is None
            or attempt.submitted_move is None
            or position.game_id != game.id
            or analysis.position_id != position.id
            or not analysis.raw_result
            or _utc(attempt.completed_at) < _utc(study.started_at)
        ):
            continue
        try:
            spec = visible_game_lesson(opportunity, session)
            if (
                spec is None
                or spec.diagnosis is None
                or spec.verification.status != "verified"
                or spec.source.fen != position.fen
            ):
                continue
            owned_evidence = True
            for reference in spec.diagnosis.evidence_refs:
                proof = session.get(Evidence, reference)
                if proof is None:
                    owned_evidence = False
                    break
                proof_position = session.get(Position, UUID(proof.position_id))
                proof_analysis = session.get(EngineAnalysis, UUID(proof.engine_analysis_id))
                if (
                    proof_position is None
                    or proof_position.game_id != game.id
                    or proof_analysis is None
                    or proof_analysis.position_id != proof_position.id
                    or not supports_saved_diagnosis(spec.diagnosis.primary, proof.payload)
                ):
                    owned_evidence = False
                    break
            if not owned_evidence or not spec.diagnosis.evidence_refs:
                continue
            board = board_from(game.headers.get("FEN"))
            source_owned = False
            for uci in game.moves:
                if (
                    board.fen() == position.fen
                    and ("white" if board.turn else "black") == game.owner_color
                ):
                    source_owned = True
                    break
                board.push_uci(uci)
            if not source_owned:
                continue
        except (KeyError, TypeError, ValidationError, ValueError):
            continue
        record({spec.diagnosis.primary}, attempt.completed_at)

    puzzle_rows = session.exec(
        select(ContentAttempt, PuzzleRun, ContentItem, StudySession)
        .join(PuzzleRun, col(PuzzleRun.id) == col(ContentAttempt.id))
        .join(ContentItem, col(ContentItem.id) == col(PuzzleRun.item_id))
        .join(StudySession, col(StudySession.id) == col(PuzzleRun.session_id))
        .where(
            ContentAttempt.player_id == player_id,
            PuzzleRun.player_id == player_id,
            StudySession.player_id == player_id,
            ContentItem.domain == "puzzles",
            col(ContentAttempt.completed_at).is_not(None),
            col(PuzzleRun.completed_at).is_not(None),
            col(PuzzleRun.state).in_(["solved", "failed"]),
        )
    ).all()
    for attempt, run, item, study in puzzle_rows:
        if (
            attempt.completed_at is None
            or run.completed_at is None
            or attempt.session_id != run.session_id
            or attempt.item_id != item.id
            or attempt.response_payload.get("run_id") != run.id
            or attempt.response_payload.get("wrong_attempts") != run.wrong_attempts
            or _utc(attempt.completed_at) != _utc(run.completed_at)
            or _utc(run.completed_at) < max(_utc(run.started_at), _utc(study.started_at))
            or attempt.success != (run.state == "solved")
        ):
            continue
        try:
            lesson = LessonSpec.model_validate(item.payload.get("lesson"))
            if lesson.source.kind != "external" or lesson.verification.status != "source_verified":
                continue
            verify_lesson(lesson)
            board = chess.Board(lesson.source.fen)
            for move in run.played_moves:
                board.push_uci(move)
            if board.fen() != run.fen or run.cursor != len(run.played_moves):
                continue
            if run.state == "solved":
                line = lesson.interaction.solution
                if not (
                    run.played_moves
                    and 0 <= run.wrong_attempts < 3
                    and len(run.played_moves) <= len(line)
                    and (
                        run.played_moves == line
                        or len(run.played_moves) % 2 == 1
                        and run.played_moves[:-1] == line[: run.cursor - 1]
                        and board.is_checkmate()
                    )
                ):
                    continue
            elif (
                run.wrong_attempts < 3
                or run.cursor >= len(lesson.interaction.solution)
                or run.played_moves != lesson.interaction.solution[: run.cursor]
            ):
                continue
        except (KeyError, TypeError, ValidationError, ValueError, LessonVerificationError):
            continue
        record(
            {code for code, weight in item.skill_mapping.items() if weight > 0}, run.completed_at
        )
    return anchors


def _online_played_at(game: Game) -> tuple[datetime | None, str | None]:
    """Require the actual PGN mainline and a game-specific supported HTTP(S) source."""
    identity = source_identity(game.headers)
    if identity is None:
        return None, "not_supported_online_source"
    identities = set()
    for key in ("Link", "Site"):
        value = game.headers.get(key, "")
        found = source_identity({key: value})
        if found is not None:
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or url.username or url.password:
                return None, "invalid_online_provenance"
            identities.add(found)
    if len(identities) != 1:
        return None, "invalid_online_provenance"
    try:
        stream = io.StringIO(game.pgn)
        parsed = chess.pgn.read_game(stream)
        if (
            parsed is None
            or parsed.errors
            or dict(parsed.headers) != game.headers
            or list(move.uci() for move in parsed.mainline_moves()) != game.moves
            or stream.read().strip()
            or not parsed.board().is_valid()
            or parsed.board().chess960
            or parsed.headers.get("Variant", "Standard").casefold() not in STANDARD_VARIANTS
            or game.white != parsed.headers.get("White")
            or game.black != parsed.headers.get("Black")
            or game.white.casefold() == game.black.casefold()
            or game.result != parsed.headers.get("Result")
        ):
            return None, "invalid_online_provenance"
    except (ValueError, IndexError):
        return None, "invalid_online_provenance"
    if game.result not in {"1-0", "0-1", "1/2-1/2"}:
        return None, "unfinished"
    # A date alone, local StartTime, or import time cannot identify a UTC instant.
    date = game.headers.get("UTCDate", "")
    time = game.headers.get("UTCTime", "")
    try:
        when = datetime.strptime(f"{date} {time}", "%Y.%m.%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None, "ambiguous_played_time"
    if when > datetime.now(UTC):
        return None, "ambiguous_played_time"
    return when, None


def _score(analysis: EngineAnalysis) -> int | None:
    if not analysis.raw_result:
        return None
    raw = analysis.raw_result[0]
    mate = raw.get("score_mate")
    cp = raw.get("score_cp")
    if isinstance(mate, int) and not isinstance(mate, bool):
        return 10000 - abs(mate) * 10 if mate > 0 else -10000 + abs(mate) * 10
    return cp if isinstance(cp, int) and not isinstance(cp, bool) else None


def _joined_evidence(
    row: GameMoveReview,
    before: EngineAnalysis,
    after: EngineAnalysis,
    reached: Position,
    evidence: list[Evidence],
    analyses: dict[UUID, EngineAnalysis],
) -> list[Evidence]:
    joined = []
    for item in evidence:
        payload = item.payload
        try:
            focused = analyses.get(UUID(item.engine_analysis_id))
        except ValueError:
            continue
        if (
            focused is None
            or focused.position_id != reached.id
            or not focused.raw_result
            or payload.get("focused_analysis_id") != str(focused.id)
            or payload.get("focused_multipv") != focused.raw_result
            or payload.get("fast_analysis_id") != str(before.id)
            or payload.get("reply_analysis_id") != str(after.id)
            or payload.get("after_fen") != reached.fen
            or payload.get("played_move") != row.san
            or payload.get("analysis_depth") != "deep"
        ):
            continue
        # Allowed-mate payloads additionally claim a source search: join it too.
        mate_id = payload.get("mate_source_analysis_id")
        if mate_id is not None:
            try:
                mate = analyses.get(UUID(mate_id))
            except (TypeError, ValueError):
                continue
            if (
                mate is None
                or mate.position_id != row.source_position_id
                or mate.config != payload.get("mate_source_config")
                or mate.raw_result != payload.get("mate_source_multipv")
            ):
                continue
        joined.append(item)
    return joined


async def _game_occurrences(
    game: Game,
    rows: list[GameMoveReview],
    positions: dict[UUID, Position],
    analyses: dict[UUID, EngineAnalysis],
    evidence: dict[str, list[Evidence]],
    mate_sources: dict[UUID, EngineAnalysis],
    detectors: tuple[PatternDetector, ...],
) -> tuple[int, dict[str, int], set[str], str | None]:
    board = board_from(game.headers.get("FEN"))
    reviews = {row.ply: row for row in rows}
    if len(reviews) != len(game.moves):
        return 0, {}, set(), "incomplete_analysis"
    owner_moves = 0
    occurrences: Counter[str] = Counter()
    history: list[str] = []
    supported = set(TRUSTED_CODES)
    for ply, uci in enumerate(game.moves, 1):
        row = reviews.get(ply)
        if row is None:
            return 0, {}, set(), "incomplete_analysis"
        source = positions.get(row.source_position_id)
        reached = positions.get(row.reached_position_id)
        before = analyses.get(row.before_analysis_id)
        after = analyses.get(row.after_analysis_id)
        mover = "white" if board.turn else "black"
        move = board.parse_uci(uci)
        san = board.san(move)
        history.append(san)
        source_fen = board.fen()
        board.push(move)
        if (
            source is None
            or reached is None
            or before is None
            or after is None
            or source.game_id != game.id
            or reached.game_id != game.id
            or source.fen != source_fen
            or reached.fen != board.fen()
            or before.position_id != source.id
            or after.position_id != reached.id
            or row.move_uci != uci
            or row.san != san
            or row.mover != mover
        ):
            return 0, {}, set(), "invalid_review_provenance"
        before_cp, after_cp = _score(before), _score(after)
        if before_cp is None or after_cp is None:
            return 0, {}, set(), "unscored_analysis"
        if mover != game.owner_color:
            continue
        owner_moves += 1
        swing = before_cp + after_cp
        if swing <= 150:
            continue
        inputs = [
            item
            for item in evidence.get(str(reached.id), [])
            if item.payload.get("fast_analysis_id") == str(before.id)
            or item.payload.get("reply_analysis_id") == str(after.id)
        ]
        saved = _joined_evidence(row, before, after, reached, inputs, analyses)
        if len(saved) != len(inputs) or any(
            item.payload.get("swing_cp") != swing for item in saved
        ):
            supported.clear()
        supported.intersection_update(
            code
            for code in TRUSTED_CODES
            if any(item.kind == "engine_analysis" for item in saved)
            and all(supports_saved_diagnosis(code, item.payload) for item in saved)
        )
        opportunity = LearningOpportunity(
            opportunity_id=f"recurrence:{game.id}:{ply}",
            position_id=str(reached.id),
            player_id=game.owner_player_id or "",
            game_id=str(game.id),
            played_move=san,
            engine_eval_before=0.0,
            engine_eval_after=-swing / 100.0,
        )
        candidates: list[DiagnosisCandidate] = []
        for detector in detectors:
            candidates.extend(
                await detector.detect(
                    opportunity, saved, PlayerContext(player_id=opportunity.player_id)
                )
            )
        # Recompose only from saved searches, never an engine. Detectors require
        # specific witness groups, not just a format-capable engine row. A
        # missing positive witness must not silently become verified abstention.
        engine_proof = next((item for item in saved if item.kind == "engine_analysis"), None)
        mate_source = mate_sources.get(source.id)
        if needs_mate_source(after) and mate_source is None:
            supported.clear()
        if engine_proof is not None:
            expected = compose_candidate_evidence(
                before_board=chess.Board(source_fen),
                after_board=board.copy(stack=False),
                history_san=history,
                initial_fen=game.headers.get("FEN"),
                position_id=str(reached.id),
                fast_analysis=before,
                reply_analysis=after,
                focused_analysis=analyses[UUID(engine_proof.engine_analysis_id)],
                mate_source_analysis=mate_source,
                played_move=san,
                swing_cp=swing,
            )
            required_codes = {
                candidate.skill_id
                for detector in detectors
                for candidate in await detector.detect(
                    opportunity, expected, PlayerContext(player_id=opportunity.player_id)
                )
                if candidate.skill_id in TRUSTED_CODES
            }
            if required_codes - {candidate.skill_id for candidate in candidates}:
                # Missing a competing cause can also change primary arbitration.
                supported.clear()
        # One primary cause per occurrence, exactly as in the producer. The
        # evidence join and per-code capability checks remain independent gates.
        selection = arbitrate_diagnoses(candidates, saved)
        if selection is not None:
            primary = selection[0]
            refs = {item.evidence_id: item for item in saved}
            if all(
                ref in refs and supports_saved_diagnosis(primary.skill_id, refs[ref].payload)
                for ref in primary.evidence_ids
            ):
                occurrences[primary.skill_id] += 1
            else:
                supported.discard(primary.skill_id)
    return owner_moves, dict(occurrences), supported, None


def _rates(period: RecurrencePeriod) -> None:
    n, count = period.owner_moves, period.occurrences
    assert count is not None and n > 0
    proportion = count / n
    z = 1.959963984540054
    denominator = 1 + z * z / n
    center = (proportion + z * z / (2 * n)) / denominator
    half = z * math.sqrt(proportion * (1 - proportion) / n + z * z / (4 * n * n)) / denominator
    period.per_100_moves = proportion * 100
    period.wilson_95_per_100 = (max(0.0, center - half) * 100, min(1.0, center + half) * 100)


async def read_pattern_recurrence(player_id: str, session: Session) -> PatternRecurrenceReport:
    """No engines, autoflush, attempts, study sessions, awards or learning writes."""
    with session.no_autoflush:
        return await _read_pattern_recurrence(player_id, session)


async def _read_pattern_recurrence(player_id: str, session: Session) -> PatternRecurrenceReport:
    anchors = _first_training(player_id, session)
    patterns = {
        code: PatternRecurrence(
            code=code,
            name=SEED_CODES[code].name,
            first_training_at=anchors.get(code),
            status="not_enough_games",
            reason="No completed server-verified mapped training since the learning reset.",
            before=RecurrencePeriod(),
            after=RecurrencePeriod(),
        )
        for code in sorted(TRUSTED_CODES)
    }
    games = session.exec(select(Game).where(Game.owner_player_id == player_id)).all()
    coverage: Counter[str] = Counter({"owned_saved_games": len(games)})
    if not games:
        return PatternRecurrenceReport(
            player_id=player_id, coverage=dict(coverage), patterns=list(patterns.values())
        )
    play_ids = set(
        session.exec(
            select(PlaySession.game_id)
            .join(Game, col(Game.id) == col(PlaySession.game_id))
            .where(Game.owner_player_id == player_id)
        ).all()
    )
    complete = set(
        session.exec(
            select(AnalysisJob.game_id)
            .join(Game, col(Game.id) == col(AnalysisJob.game_id))
            .where(
                Game.owner_player_id == player_id,
                AnalysisJob.status == "completed",
                col(AnalysisJob.completed_at).is_not(None),
            )
        ).all()
    )
    positions = {
        row.id: row
        for row in session.exec(
            select(Position)
            .join(Game, col(Game.id) == col(Position.game_id))
            .where(Game.owner_player_id == player_id)
        ).all()
    }
    analyses = {
        row.id: row
        for row in session.exec(
            select(EngineAnalysis)
            .join(Position, col(Position.id) == col(EngineAnalysis.position_id))
            .join(Game, col(Game.id) == col(Position.game_id))
            .where(Game.owner_player_id == player_id)
        ).all()
    }
    mate_sources = {
        row.position_id: row
        for row in analyses.values()
        if row.config.get("nodes") == 1_000_000 and row.config.get("multipv") == 4
    }
    reviews: dict[UUID, list[GameMoveReview]] = defaultdict(list)
    for row in session.exec(
        select(GameMoveReview)
        .join(Game, col(Game.id) == col(GameMoveReview.game_id))
        .where(Game.owner_player_id == player_id)
    ).all():
        reviews[row.game_id].append(row)
    evidence: dict[str, list[Evidence]] = defaultdict(list)
    # Evidence stores hyphenated UUID text, unlike SQLite's UUID columns.
    # Bound each text lookup rather than creating a library-sized bind array.
    for position_ids in batched(positions, 64):
        for item in session.exec(
            select(Evidence).where(
                col(Evidence.position_id).in_([str(key) for key in position_ids])
            )
        ).all():
            evidence[item.position_id].append(item)
    registry = get_host_registry()
    detectors = tuple(
        detector
        for name in registry.names(kind=PluginKind.PATTERN_DETECTOR)
        if isinstance(
            detector := registry.get(kind=PluginKind.PATTERN_DETECTOR, name=name), PatternDetector
        )
    )
    online_games: dict[str, list[tuple[Game, datetime]]] = defaultdict(list)
    for game in sorted(games, key=lambda value: str(value.id)):
        when, reason = _online_played_at(game)
        identity = source_identity(game.headers)
        if game.id in play_ids:
            reason = "not_imported"
        elif game.owner_color not in {"white", "black"}:
            reason = "unknown_owner_colour"
        if reason is not None or when is None:
            coverage[reason or "ambiguous_played_time"] += 1
            continue
        assert identity is not None
        online_games[identity].append((game, when))
    for copies in online_games.values():
        if len(copies) > 1:
            coverage["duplicate_online_game"] += len(copies) - 1
        coverage["dated_finished_online_games"] += 1
        when = copies[0][1]
        owner_moves = 0
        counts: dict[str, int] = {}
        supported: set[str] = set()
        invalid: str | None = "incomplete_analysis"
        best_rank = (False, -1)
        for game, played_at in copies:
            result: tuple[int, dict[str, int], set[str], str | None] = (
                0,
                {},
                set(),
                "incomplete_analysis",
            )
            if game.id in complete:
                result = await _game_occurrences(
                    game, reviews[game.id], positions, analyses, evidence, mate_sources, detectors
                )
            moves, found, supported_codes, exclusion = result
            if exclusion is None and moves < 20:
                exclusion = "fewer_than_20_owner_moves"
            rank = (exclusion is None, len(supported_codes))
            if rank > best_rank:
                when = played_at
                owner_moves, counts, supported, invalid = moves, found, supported_codes, exclusion
                best_rank = rank
            if exclusion is None and supported_codes == TRUSTED_CODES:
                break
        for code, pattern in patterns.items():
            anchor = anchors.get(code)
            if anchor is None:
                continue
            if when == anchor:
                coverage["at_training_boundary"] += 1
                continue
            period = pattern.before if when < anchor else pattern.after
            period.candidate_games += 1
            exclusion = invalid or (None if code in supported else "unsupported_candidate_evidence")
            if exclusion is not None:
                period.excluded[exclusion] = period.excluded.get(exclusion, 0) + 1
                continue
            period.games += 1
            period.owner_moves += owner_moves
            period.occurrences = (period.occurrences or 0) + counts.get(code, 0)
    for pattern in patterns.values():
        if pattern.first_training_at is None:
            continue
        if pattern.before.games >= 3 and pattern.after.games >= 3:
            pattern.status = "comparison"
            pattern.reason = (
                "At least three fully analysed games with at least 20 owner moves "
                "per game on each side."
            )
            _rates(pattern.before)
            _rates(pattern.after)
        else:
            pattern.reason = (
                "Not enough games: need three fully analysed games with at least "
                "20 owner moves each on EACH side; missing evidence is not zero."
            )
    return PatternRecurrenceReport(
        player_id=player_id, coverage=dict(coverage), patterns=list(patterns.values())
    )

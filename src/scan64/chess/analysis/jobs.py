import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

import chess
from chess_lesson_spec import Diagnosis, LessonSpec
from sqlalchemy import update
from sqlmodel import Session, col, select

from scan64.api.models import PlayerProfile
from scan64.chess.analysis.admission import admission_controller
from scan64.chess.analysis.credit import apply_positive_move_credits
from scan64.chess.analysis.line_scoring import score_verified_lines
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    GameMoveReview,
    PersistedLessonOpportunity,
)
from scan64.chess.analysis.notifications import notify_analysis_job
from scan64.chess.analysis.orchestration import (
    FastPassConfig,
    FastPassOrchestrator,
    FocusedPassConfig,
    FocusedPassOrchestrator,
)
from scan64.chess.analysis.reuse import BatchAnalysisAdapter
from scan64.chess.analysis.review import (
    REVIEW_NODES,
    stage_game_review,
    stage_position_analysis,
)
from scan64.chess.boards import board_from, uci_moves_to_san
from scan64.chess.games.models import Game, PlaySession
from scan64.chess.positions.models import Position
from scan64.content.puzzle_practice import lock_puzzle_player
from scan64.explanations.assembly import resolve_explanation
from scan64.explanations.settings import (
    local_explanation_owner_generation,
    read_local_explanation_provider,
)
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.diagnosis.ranking import OpportunityRanker, RankingFactors
from scan64.learning.diagnosis.visibility import visible_game_lesson
from scan64.learning.evidence.composer import compose_candidate_evidence, needs_mate_source
from scan64.learning.evidence.models import Evidence
from scan64.learning.exercises.exact_replay import generate_exact_replay_exercise
from scan64.learning.exercises.hints import fill_lesson_hints
from scan64.learning.exercises.sound_moves import analyze_lesson_source, sound_moves
from scan64.learning.habit import advance_targets
from scan64.learning.plugins.host_registry import get_host_registry
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry
from scan64.learning.profiling.profile_update import apply_analysis_observation
from scan64.learning.progress import game_counts_for_progress
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule
from scan64.learning.tolerance import win_probability
from scan64.learning.verification.verifier import LessonVerificationError, verify_lesson
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager


def _resolve_pattern_detectors(
    registry: PluginRegistry | None = None,
) -> tuple[PatternDetector, ...]:
    active_registry = registry if registry is not None else get_host_registry()
    detectors: list[PatternDetector] = []
    for name in active_registry.names(kind=PluginKind.PATTERN_DETECTOR):
        detector = active_registry.get(kind=PluginKind.PATTERN_DETECTOR, name=name)
        if not isinstance(detector, PatternDetector):
            raise RuntimeError(f"Registered detector {name!r} violates the plugin contract")
        detectors.append(detector)
    if not detectors:
        raise RuntimeError("The host plugin registry has no pattern detectors")
    return tuple(detectors)


@dataclass
class _LessonCandidate:
    source: Position
    diagnosis: DiagnosisCandidate
    secondary: tuple[DiagnosisCandidate, ...]
    evidence: list[Evidence]
    severity: float
    played_move: chess.Move


async def run_analysis_for_game(
    game: Game,
    session: Session,
    registry: PluginRegistry | None = None,
    pool_manager: EnginePoolManager | None = None,
    *,
    owner_generation: int | None = None,
) -> None:
    if game.owner_player_id is None:
        raise ValueError("Cannot analyse a game without an owner")

    if game.owner_color is None:
        raise ValueError("Cannot analyse a legacy game without owner_color")
    analyzed_moves = list(game.moves)
    analyzed_headers = dict(game.headers)
    analyzed_color = game.owner_color

    # Keep historical rows, but reserve sources and slots only for visible lessons.
    existing_lessons = [
        lesson
        for lesson in session.exec(
            select(PersistedLessonOpportunity).where(
                PersistedLessonOpportunity.game_id == game.id,
                PersistedLessonOpportunity.player_id == game.owner_player_id,
            )
        ).all()
        if visible_game_lesson(lesson, session) is not None
    ]
    existing_sources = {lesson.source_position_id for lesson in existing_lessons}
    remaining_slots = max(0, 5 - len(existing_lessons))
    positions_by_fen = {
        position.fen: position
        for position in session.exec(select(Position).where(Position.game_id == game.id)).all()
    }
    profile = session.get(PlayerProfile, game.owner_player_id)
    rating = profile.rating if profile is not None else 1500

    adapter = BatchAnalysisAdapter(
        pool_manager.batch_adapter
        if pool_manager is not None
        else StockfishAdapter(StockfishConfig()),
        game,
        session,
    )
    orchestrator = FastPassOrchestrator(
        adapter,
        FastPassConfig(
            nodes=REVIEW_NODES,
            swing_threshold_cp=150,
            concurrency=pool_manager.batch_pool.concurrency if pool_manager is not None else 1,
        ),
    )
    detectors = _resolve_pattern_detectors(registry)
    ctx = PlayerContext(player_id=game.owner_player_id)

    initial_fen = game.headers.get("FEN")
    san_moves = uci_moves_to_san(game.moves, initial_fen)
    fast_pass = await orchestrator.run_fast_pass(san_moves, initial_fen)
    candidates = fast_pass.candidates
    focused_orchestrator = FocusedPassOrchestrator(
        adapter,
        FocusedPassConfig(
            nodes=1_000_000,
            multipv=4,
            concurrency=pool_manager.batch_pool.concurrency if pool_manager is not None else 1,
        ),
    )
    focused_analyses = await focused_orchestrator.run_focused_pass(candidates)
    if len(focused_analyses) != len(candidates):
        raise RuntimeError("Focused pass did not return one analysis for every candidate")

    board = board_from(initial_fen)
    fens_before = [board.fen()]
    for san in san_moves:
        board.push_san(san)
        fens_before.append(board.fen())

    # All diagnosis, engine searches and verification happen on detached rows.
    # No add/flush (including query-triggered autoflush) may precede the final
    # engine call: interactive moves and attempts must remain able to write.
    analyses: dict[UUID, EngineAnalysis] = {}
    existing_plies = set(
        session.exec(select(GameMoveReview.ply).where(GameMoveReview.game_id == game.id)).all()
    )
    review_rows = stage_game_review(
        game, fast_pass.evaluations, positions_by_fen, analyses, existing_plies
    )
    all_evidence: list[Evidence] = []
    observations: list[tuple[UUID, str]] = []
    lesson_candidates: list[_LessonCandidate] = []
    mate_sources: dict[str, EngineAnalysis] = {}
    for candidate, focused_analysis in zip(candidates, focused_analyses, strict=True):
        fen_before = fens_before[candidate.move_index]
        existing_position = positions_by_fen.get(fen_before)
        if existing_position is not None and existing_position.id in existing_sources:
            continue
        source_position = stage_position_analysis(
            game, fen_before, candidate.before_analysis, positions_by_fen, analyses
        )
        after_position = stage_position_analysis(
            game, candidate.fen, candidate.after_analysis, positions_by_fen, analyses
        )
        stage_position_analysis(game, candidate.fen, focused_analysis, positions_by_fen, analyses)
        mate_source_analysis = None
        if needs_mate_source(candidate.after_analysis):
            mate_source_analysis = await adapter.analyze_position(
                fen_before, nodes=1_000_000, multipv=4
            )
            mate_sources[fen_before] = mate_source_analysis
            stage_position_analysis(
                game, fen_before, mate_source_analysis, positions_by_fen, analyses
            )
        evidence = compose_candidate_evidence(
            before_board=board_from(fen_before),
            after_board=board_from(candidate.fen),
            history_san=san_moves[: candidate.move_index + 1],
            initial_fen=initial_fen,
            position_id=str(after_position.id),
            fast_analysis=candidate.before_analysis,
            reply_analysis=candidate.after_analysis,
            focused_analysis=focused_analysis,
            mate_source_analysis=mate_source_analysis,
            played_move=san_moves[candidate.move_index],
            swing_cp=candidate.swing_cp,
        )
        all_evidence.extend(evidence)

        if board_from(fen_before).turn != (game.owner_color == "white"):
            continue

        opportunity = LearningOpportunity(
            opportunity_id=f"opp_{uuid4()}",
            position_id=str(after_position.id),
            player_id=game.owner_player_id,
            game_id=str(game.id),
            played_move=san_moves[candidate.move_index],
            engine_eval_before=0.0,
            engine_eval_after=-(candidate.swing_cp / 100.0),
        )

        diagnosis_candidates = []
        for detector in detectors:
            diagnosis_candidates.extend(await detector.detect(opportunity, evidence, ctx))
        selection = arbitrate_diagnoses(diagnosis_candidates, evidence)
        if selection is None:
            continue
        best, secondary = selection

        observations.append((after_position.id, best.skill_id))
        before_probability = (
            win_probability(candidate.before_analysis.raw_result[0])
            if candidate.before_analysis.raw_result
            else None
        )
        after_probability = (
            win_probability(candidate.after_analysis.raw_result[0])
            if candidate.after_analysis.raw_result
            else None
        )
        # The after score is the opponent's POV. Compare the owner's winning
        # chances rather than over-weighting centipawn swings in decided games.
        loss = (
            max(0.0, before_probability + after_probability - 1.0)
            if before_probability is not None and after_probability is not None
            else 0.0
        )
        lesson_candidates.append(
            _LessonCandidate(
                source_position,
                best,
                secondary,
                evidence,
                loss,
                board_from(fen_before).parse_san(san_moves[candidate.move_index]),
            )
        )

    # A source position can recur in a game; teach it once, using its worst
    # diagnosed occurrence. Evidence and profile observations remain uncapped.
    distinct: dict[UUID, _LessonCandidate] = {}
    for item in lesson_candidates:
        previous = distinct.get(item.source.id)
        if previous is None or item.severity * item.diagnosis.confidence > (
            previous.severity * previous.diagnosis.confidence
        ):
            distinct[item.source.id] = item
    ranker = OpportunityRanker(overcoaching_cap=1)
    chosen_skills: Counter[str] = Counter()
    for existing_lesson in existing_lessons:
        skill = existing_lesson.lesson_spec.get("diagnosis", {}).get("primary")
        if isinstance(skill, str):
            chosen_skills[skill] += 1
    prepared: list[tuple[_LessonCandidate, EngineAnalysis, LessonSpec]] = []
    while distinct and len(prepared) < remaining_slots:
        # Only durable/verified choices consume redundancy. A discarded engine
        # candidate must not demote another teachable mistake of the same skill.
        choice = ranker.rank(
            [
                (
                    item.diagnosis,
                    RankingFactors(
                        severity=item.severity,
                        confidence=item.diagnosis.confidence,
                        redundancy=chosen_skills[item.diagnosis.skill_id],
                    ),
                )
                for item in distinct.values()
            ]
        )[0]
        item = next(item for item in distinct.values() if item.diagnosis is choice.candidate)
        distinct.pop(item.source.id)
        source_position = item.source
        fen_before = source_position.fen
        board_before = chess.Board(fen_before)
        try:
            source_analysis = await analyze_lesson_source(
                adapter, fen_before, initial_analysis=mate_sources.get(fen_before)
            )
            accepted = sound_moves(
                board_before,
                source_analysis,
                avoid_mate=item.diagnosis.skill_id == "threat_processing.allowed_mate",
            )
        except ValueError:
            continue
        if item.played_move in accepted:
            # A shallow swing is not a teachable mistake if the deeper source
            # search proves the actual move sound under the shared A2 policy.
            continue
        diagnosis = Diagnosis(
            primary=item.diagnosis.skill_id,
            secondary=[candidate.skill_id for candidate in item.secondary],
            confidence=item.diagnosis.confidence,
            evidence_refs=item.diagnosis.evidence_ids,
        )
        lesson = await generate_exact_replay_exercise(
            diagnosis=diagnosis,
            fen=fen_before,
            lesson_id=f"les_{uuid4()}",
            accepted_moves_san=[board_before.san(move) for move in accepted],
        )
        # Every position along the lesson's verified lines gets an engine score
        # (#367), through the batch pool only: the per-call adapter would start
        # one engine process per position.
        if pool_manager is not None:
            await score_verified_lines(
                adapter,
                item.evidence,
                diagnosis.evidence_refs,
                fen_before,
                concurrency=pool_manager.batch_pool.concurrency,
                prefetch=lambda fens: adapter.prefetch(session, list(fens)),
            )
        local_provider = await read_local_explanation_provider(
            session, game.owner_player_id, owner_generation
        )
        lesson.explanation = await resolve_explanation(
            diagnosis, item.evidence, fen_before, local_provider=local_provider
        )

        try:
            fill_lesson_hints(lesson, source_analysis)
            verify_lesson(lesson, source_analysis)
        except (LessonVerificationError, ValueError):
            continue
        stage_position_analysis(game, fen_before, source_analysis, positions_by_fen, analyses)
        prepared.append((item, source_analysis, lesson))
        chosen_skills[item.diagnosis.skill_id] += 1

    # Include every completed call, even searches discarded by diagnosis,
    # source widening/verification, sound-move filtering or an existing lesson.
    for fen, analysis in adapter.completed:
        stage_position_analysis(game, fen, analysis, positions_by_fen, analyses)

    # Only now begin the atomic write phase (_JobSession defers this commit
    # together with the job's completed marker during normal/recovered runs).
    # Match play's session-before-game lock order. A takeback or move during
    # engine work invalidates the entire staged transaction, not only credit.
    with session.no_autoflush:
        session.execute(
            update(PlaySession)
            .where(col(PlaySession.game_id) == game.id)
            .values(status=col(PlaySession.status))
            .execution_options(synchronize_session=False)
        )
        unchanged = session.execute(
            update(Game)
            .where(
                col(Game.id) == game.id,
                col(Game.moves) == analyzed_moves,
                col(Game.headers) == analyzed_headers,
                col(Game.owner_color) == analyzed_color,
            )
            .values(result=col(Game.result))
            .returning(col(Game.id))
            .execution_options(synchronize_session=False)
        ).first()
    if unchanged is None:
        session.rollback()
        raise ValueError("Game changed during analysis; analyse the finished game again")
    lock_puzzle_player(game.owner_player_id, session)
    counts_for_progress = game_counts_for_progress(game, game.owner_player_id, session)
    session.add_all(positions_by_fen.values())
    session.add_all(analyses.values())
    session.add_all(review_rows)
    session.add_all(all_evidence)
    for position_id, skill_id in observations if counts_for_progress else ():
        apply_analysis_observation(
            session=session,
            player_id=game.owner_player_id,
            game_id=str(game.id),
            position_id=str(position_id),
            skill_id=skill_id,
            rating=rating,
            observed_at=datetime.now(UTC),
        )
    for item, source_analysis, lesson in prepared:
        persisted = PersistedLessonOpportunity(
            game_id=game.id,
            source_position_id=item.source.id,
            player_id=game.owner_player_id,
            verification_analysis_id=source_analysis.id,
            verification_status="verified",
            lesson_spec=lesson.model_dump(mode="json"),
        )
        session.add(persisted)
        if counts_for_progress:
            session.add(
                ReviewSchedule(
                    player_id=game.owner_player_id,
                    item_id=str(persisted.id),
                    skill_id=item.diagnosis.skill_id,
                    next_review_at=datetime.now(UTC),
                )
            )

    if counts_for_progress:
        apply_positive_move_credits(game, session, rating=rating, staged_observations=observations)

    session.commit()


class _JobSession(Session):
    """Keep analysis writes and completion in one durable transaction.

    With defer_commit set, run_analysis_for_game's own commit becomes a flush,
    so analysis writes and the job's completed marker commit together.
    """

    defer_commit = False

    def commit(self) -> None:
        if self.defer_commit:
            self.flush()
        else:
            super().commit()


_game_locks: WeakValueDictionary[tuple[asyncio.AbstractEventLoop, UUID], asyncio.Lock] = (
    WeakValueDictionary()
)

# A running job holds one pooled database connection across its engine awaits. SQLAlchemy's
# default pool holds 15 (5 + 10 overflow): a 16th concurrent job blocked the whole event
# loop until the pool timeout and then failed without updating its row. Only slot holders
# keep a connection across awaits; the job lookup checks one out and returns it with no
# await in between. Jobs queue on their game's lock before taking a slot, so duplicates
# for one game wait without starving other games; a review backfill for that game also
# waits behind its queued job. One job's fast and focused passes already fan out to the
# full batch engine pool width.
MAX_RUNNING_ANALYSIS_JOBS = 4
_job_slots: WeakValueDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    WeakValueDictionary()
)


async def backfill_game_review(
    game: Game, session: Session, pool_manager: EnginePoolManager | None = None
) -> None:
    """One explicit same-budget pass; never call the learning pipeline."""
    key = (asyncio.get_running_loop(), game.id)
    lock = _game_locks.setdefault(key, asyncio.Lock())
    async with lock:
        existing_plies = set(
            session.exec(select(GameMoveReview.ply).where(GameMoveReview.game_id == game.id)).all()
        )
        if existing_plies == set(range(1, len(game.moves) + 1)):
            return
        adapter = BatchAnalysisAdapter(
            pool_manager.batch_adapter
            if pool_manager is not None
            else StockfishAdapter(StockfishConfig()),
            game,
            session,
        )
        fast_pass = await FastPassOrchestrator(
            adapter,
            FastPassConfig(
                nodes=REVIEW_NODES,
                concurrency=pool_manager.batch_pool.concurrency if pool_manager is not None else 1,
            ),
        ).run_fast_pass(
            uci_moves_to_san(game.moves, game.headers.get("FEN")), game.headers.get("FEN")
        )
        positions = {
            position.fen: position
            for position in session.exec(select(Position).where(Position.game_id == game.id)).all()
        }
        analyses: dict[UUID, EngineAnalysis] = {}
        rows = stage_game_review(game, fast_pass.evaluations, positions, analyses, existing_plies)
        for fen, analysis in adapter.completed:
            stage_position_analysis(game, fen, analysis, positions, analyses)
        session.add_all(positions.values())
        session.add_all(analyses.values())
        session.add_all(rows)
        session.commit()


async def execute_analysis_job_async(
    job_id: UUID, pool_manager: EnginePoolManager | None = None
) -> None:
    """Run one analysis job to completion.

    This is the entrypoint the production path must schedule directly onto
    the FastAPI app's event loop (e.g. via `BackgroundTasks.add_task`, which
    awaits async callables on that loop rather than a worker thread) when a
    `pool_manager` is supplied: `EnginePool`'s queue and the pooled UCI
    subprocess transports are bound to whichever loop first uses them, so
    running this inside a fresh `asyncio.run()` loop per call — as
    `execute_analysis_job` below does — would hang on the pooled path after
    the first invocation. Direct/offline callers (the CLI, tests) that never
    pass a `pool_manager` are unaffected and should keep using the sync
    `execute_analysis_job` wrapper.
    """
    from scan64.persistence.database import engine

    with Session(engine) as session:
        job = session.get(AnalysisJob, job_id)
        if job is None:
            return
        key = (asyncio.get_running_loop(), job.game_id)
    lock = _game_locks.setdefault(key, asyncio.Lock())
    async with lock:
        slots = _job_slots.setdefault(
            asyncio.get_running_loop(), asyncio.Semaphore(MAX_RUNNING_ANALYSIS_JOBS)
        )
        async with slots:
            await _execute_analysis_job_async(job_id, pool_manager)


async def _execute_analysis_job_async(
    job_id: UUID, pool_manager: EnginePoolManager | None = None
) -> None:
    """Call through execute_analysis_job_async; this skips per-game serialization."""
    from datetime import UTC, datetime

    from scan64.persistence.database import engine

    with _JobSession(engine) as session:
        job = session.get(AnalysisJob, job_id)
        if not job or job.status == "completed":
            return

        game_id = job.game_id
        job.status = "running"
        session.add(job)
        session.commit()
        notify_analysis_job(engine, job_id)

        try:
            row = session.exec(
                select(Game, local_explanation_owner_generation(col(Game.owner_player_id))).where(
                    col(Game.id) == game_id
                )
            ).first()
            if row is None:
                raise ValueError("Game not found")
            game, owner_generation = row
            session.defer_commit = True
            completed = session.exec(
                select(AnalysisJob.id).where(
                    AnalysisJob.game_id == game.id,
                    AnalysisJob.status == "completed",
                )
            ).first()
            if completed is None:
                await run_analysis_for_game(
                    game, session, pool_manager=pool_manager, owner_generation=owner_generation
                )
            session.defer_commit = False
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            session.add(job)
            session.flush()
            if game.owner_player_id is not None:
                advance_targets(game.owner_player_id, session)
            session.add(job)
            session.commit()
        except Exception as error:
            session.defer_commit = False
            session.rollback()
            failed_job = session.get(AnalysisJob, job_id)
            if failed_job is None:
                return
            failed_job.status = "failed"
            failed_job.error = str(error)
            session.add(failed_job)
            session.commit()
            notify_analysis_job(engine, job_id)
            return

        notify_analysis_job(engine, job_id)


def execute_analysis_job(job_id: UUID, pool_manager: EnginePoolManager | None = None) -> None:
    """Sync entrypoint for offline/direct callers (the CLI, tests) that run
    outside any existing event loop. Never call this with a `pool_manager`
    from inside a running loop — use `execute_analysis_job_async` there."""
    asyncio.run(execute_analysis_job_async(job_id, pool_manager=pool_manager))


async def submit_analysis_job(
    player_id: str, job_id: UUID, pool_manager: EnginePoolManager | None = None
) -> None:
    """Admit an analysis job at submission time under the per-player daily
    quota, fair-share queueing work beyond it rather than rejecting or
    dropping it (M41). Must be scheduled directly onto the app's event loop
    (e.g. `BackgroundTasks.add_task`) so `AdmissionController.submit`'s
    `asyncio.create_task` calls see a running loop, and so a queued job's
    eventual pooled execution shares that loop with the pools it acquires
    engines from.
    """
    admission_controller.submit(
        player_id, lambda: execute_analysis_job_async(job_id, pool_manager=pool_manager)
    )

"""Game analysis scores every move of each lesson's verified lines (#367).

Real Stockfish through the batch pool (rule 25): the job stores the line
searches as engine rows and in the evidence payload, the narrator facts built
from the persisted evidence have every ply scored, and a second analysis of
the same game reuses the rows of the same engine instead of searching again.
A line search that fails (the engine exits, overruns, lacks a capability or
errs) leaves its plies unknown; the job still completes with its lessons.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import chess
import chess.engine
import pytest
from chess_lesson_spec import Diagnosis, LessonSpec
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import AnalysisJob, EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.explanations.facts import LINE_EVALUATIONS_FIELD, LINE_NODES, build_narrator_facts
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.registry import PluginRegistry
from scan64.providers.engine import ENGINE_CONFIG_ENV
from scan64.providers.engine.uci import EngineCapabilityError
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager, PooledStockfishAdapter

# Scholar's-mate pattern with a missed defence: 3...Nf6?? allows Qxf7#.
MOVES = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]


@pytest.mark.asyncio
async def test_lesson_lines_are_scored_stored_and_reused(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []
    original = jobs.score_verified_lines

    async def counted(*args: object, **kwargs: object) -> int:
        count = await original(*args, **kwargs)  # type: ignore[arg-type]
        calls.append(count)
        return count

    monkeypatch.setattr(jobs, "score_verified_lines", counted)
    token, token_hash = issue_player_token()
    db_session.add(Player(id="line-player"))
    db_session.add(PlayerCredential(player_id="line-player", token_hash=token_hash))
    game = Game(pgn="", moves=MOVES, owner_color="black", owner_player_id="line-player")
    db_session.add(game)
    db_session.commit()
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    manager = EnginePoolManager(StockfishConfig(), interactive_concurrency=1, batch_concurrency=2)
    # A profile selected after the pool started: the pool's Stockfish still
    # searches, and the lessons name it.
    profile = tmp_path / "engine.toml"
    profile.write_text("[engine]\nname = 'second'\nbinary_path = 'second'\n", encoding="utf-8")
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(profile))
    try:
        await jobs.run_analysis_for_game(game, db_session, registry, pool_manager=manager)
        lessons = db_session.exec(select(PersistedLessonOpportunity)).all()
        assert lessons, "the fixture game must produce a lesson"
        assert {lesson.lesson_spec["verification"]["engine"] for lesson in lessons} == {"stockfish"}
        assert calls and all(count > 0 for count in calls)
        evidence = db_session.exec(select(Evidence)).all()
        line_rows = [
            row
            for row in db_session.exec(select(EngineAnalysis)).all()
            if row.config.get("nodes") == LINE_NODES and row.config.get("multipv") == 1
        ]
        for lesson in lessons:
            spec = LessonSpec.model_validate(lesson.lesson_spec)
            diagnosis = Diagnosis.model_validate(spec.diagnosis.model_dump())
            referenced = [e for e in evidence if e.evidence_id in diagnosis.evidence_refs]
            assert any(LINE_EVALUATIONS_FIELD in e.payload for e in referenced)
            facts = build_narrator_facts(diagnosis, referenced, spec.source.fen)
            for line in facts.lines:
                for move in line.moves[:-1]:
                    assert move.evaluation_after.status != "unknown", (line.uci, move.ply)
            stored_ids = {
                entry["analysis_id"]
                for e in referenced
                for entry in e.payload[LINE_EVALUATIONS_FIELD]
            }
            assert stored_ids <= {str(row.id) for row in line_rows}

        # The line searches are positions of the game in storage, but the
        # game's positions endpoint lists only the positions the game reached.
        board = chess.Board()
        game_fens = [board.fen()]
        for move in MOVES:
            board.push_uci(move)
            game_fens.append(board.fen())
        stored = db_session.exec(select(Position).where(Position.game_id == game.id)).all()
        assert {row.fen for row in stored} - set(game_fens), "line positions are stored"
        response = client.get(
            f"/v1/games/{game.id}/positions", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert [row["fen"] for row in response.json()] == game_fens

        # Second analysis of the same game (a new game row, same owner and moves):
        # every line search is a same-engine reuse.
        rerun = Game(pgn="", moves=MOVES, owner_color="black", owner_player_id="line-player")
        db_session.add(rerun)
        db_session.commit()
        first_run_calls = len(calls)
        searched: list[str] = []
        batch_search = manager.batch_adapter.__class__.analyze_position

        async def spy(self: object, fen: str, **limits: object) -> EngineAnalysis:
            if limits.get("nodes") == LINE_NODES and limits.get("multipv") == 1:
                searched.append(fen)
            return await batch_search(self, fen, **limits)  # type: ignore[arg-type]

        monkeypatch.setattr(manager.batch_adapter.__class__, "analyze_position", spy)
        await jobs.run_analysis_for_game(rerun, db_session, registry, pool_manager=manager)
        line_fens = {
            entry["fen"] for e in evidence for entry in e.payload.get(LINE_EVALUATIONS_FIELD, [])
        }
        assert len(calls) > first_run_calls
        assert line_fens and not line_fens & set(searched)
    finally:
        await manager.close()


async def _exit(self: PooledStockfishAdapter) -> None:
    """The real Stockfish process exits before the search: EngineTerminatedError."""
    assert self._engine is not None
    self._engine.transport.kill()
    await self._engine.returncode


async def _exit_unnoticed(self: PooledStockfishAdapter) -> None:
    """The process dies as the search starts: python-chess may raise
    EngineTerminatedError or leave the search to time out on a dead engine."""
    assert self._engine is not None
    self._engine.transport.kill()


# A line search on an engine that died unnoticed ends at this deadline.
LINE_DEADLINE_S = 5.0

type Fault = Callable[[PooledStockfishAdapter], Awaitable[None]]


async def _raise(error: Exception) -> None:
    raise error


def _timed_out() -> EngineCapabilityError:
    # The adapter's ``bounded`` reports an overrun search this way.
    error = EngineCapabilityError("did not finish")
    error.__cause__ = TimeoutError()
    return error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "reasons"),
    [
        (_exit, {"engine_exited"}),
        # Each such search waits for the deadline: fail only the first two.
        ((_exit_unnoticed, 2), {"engine_exited", "timeout"}),
        (lambda self: _raise(_timed_out()), {"timeout"}),
        (lambda self: _raise(TimeoutError()), {"timeout"}),
        (lambda self: _raise(EngineCapabilityError("no MultiPV")), {"missing_capability"}),
        (lambda self: _raise(chess.engine.EngineError("bad info")), {"engine_error"}),
    ],
    ids=[
        "engine-exits",
        "engine-exits-unnoticed",
        "search-overruns",
        "timeout",
        "capability",
        "engine-error",
    ],
)
async def test_a_failed_line_search_does_not_fail_the_analysis_job(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fault: Fault | tuple[Fault, int],
    reasons: set[str],
) -> None:
    """The job path with real Stockfish; only the line-scoring searches fail."""
    fault, limit = fault if isinstance(fault, tuple) else (fault, 1_000_000)
    checked = 0
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    monkeypatch.setattr(jobs, "get_host_registry", lambda: registry)
    scoring = False
    failed: list[str] = []
    original_scoring = jobs.score_verified_lines
    # The fast, focused and source passes keep the profile's deadline: a
    # 1,000,000-node MultiPV 4 search can take longer than the line deadline on
    # a loaded runner (#444). Only the line searches, whose faults are under
    # test, get the short deadline that ends a search on an engine that died
    # unnoticed; a healthy LINE_NODES search finishes far inside it.
    config = StockfishConfig()
    production_deadline_s = config.search_deadline_s
    assert production_deadline_s > LINE_DEADLINE_S

    async def flagged(*args: Any, **kwargs: Any) -> int:
        nonlocal scoring
        scoring = True
        config.search_deadline_s = LINE_DEADLINE_S
        try:
            return await original_scoring(*args, **kwargs)
        finally:
            scoring = False
            config.search_deadline_s = production_deadline_s

    search = PooledStockfishAdapter.analyze_position

    async def faulty(self: PooledStockfishAdapter, fen: str, **limits: Any) -> EngineAnalysis:
        if scoring and len(failed) < limit:
            failed.append(fen)
            await self.ensure_started()
            await fault(self)
        return await search(self, fen, **limits)

    monkeypatch.setattr(jobs, "score_verified_lines", flagged)
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", faulty)
    db_session.add(Player(id="fault-player"))
    game = Game(pgn="", moves=MOVES, owner_color="black", owner_player_id="fault-player")
    db_session.add(game)
    db_session.commit()
    job = AnalysisJob(game_id=game.id, status="pending")
    db_session.add(job)
    db_session.commit()
    manager = EnginePoolManager(config, interactive_concurrency=1, batch_concurrency=2)
    try:
        with caplog.at_level("WARNING", logger="scan64.chess.analysis.line_scoring"):
            await jobs.execute_analysis_job_async(job.id, pool_manager=manager)
        db_session.expire_all()
        finished = db_session.get(AnalysisJob, job.id)
        assert finished is not None and (finished.status, finished.error) == ("completed", None)
        lessons = db_session.exec(select(PersistedLessonOpportunity)).all()
        assert lessons and failed, "the fixture game must produce a lesson with line searches"
        evidence = db_session.exec(select(Evidence)).all()
        for lesson in lessons:
            spec = LessonSpec.model_validate(lesson.lesson_spec)
            diagnosis = Diagnosis.model_validate(spec.diagnosis.model_dump())
            referenced = [e for e in evidence if e.evidence_id in diagnosis.evidence_refs]
            stored = {
                entry["fen"]
                for e in referenced
                for entry in e.payload.get(LINE_EVALUATIONS_FIELD, [])
            }
            assert not stored & set(failed)
            facts = build_narrator_facts(diagnosis, referenced, spec.source.fen)
            for line in facts.lines:
                board = chess.Board(spec.source.fen)
                for move in line.moves:
                    board.push_uci(move.uci)
                    if board.fen() in failed:
                        assert move.evaluation_after.status == "unknown"
                        checked += 1
        assert checked
        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "scan64.chess.analysis.line_scoring"
        ]
        assert messages
        for message in messages:
            assert set(message.rsplit(": ", 1)[1].split(",")) <= reasons, message
        assert not any(fen.split()[0] in message for fen in failed for message in messages)
        # An engine that exited was discarded: the pool still searches on both engines.
        monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", search)
        after = await asyncio.gather(
            *(manager.analyze_batch(chess.STARTING_FEN, nodes=1_000) for _ in range(2))
        )
        assert all(analysis.raw_result for analysis in after)
    finally:
        await manager.close()

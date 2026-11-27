"""Game analysis scores every move of each lesson's verified lines (#367).

Real Stockfish through the batch pool (rule 25): the job stores the line
searches as engine rows and in the evidence payload, the narrator facts built
from the persisted evidence have every ply scored, and a second analysis of
the same game reuses the rows of the same engine instead of searching again.
"""

from __future__ import annotations

import pytest
from chess_lesson_spec import Diagnosis, LessonSpec
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.games.models import Game
from scan64.explanations.facts import LINE_EVALUATIONS_FIELD, LINE_NODES, build_narrator_facts
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.registry import PluginRegistry
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager

# Scholar's-mate pattern with a missed defence: 3...Nf6?? allows Qxf7#.
MOVES = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]


@pytest.mark.asyncio
async def test_lesson_lines_are_scored_stored_and_reused(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    original = jobs.score_verified_lines

    async def counted(*args: object, **kwargs: object) -> int:
        count = await original(*args, **kwargs)  # type: ignore[arg-type]
        calls.append(count)
        return count

    monkeypatch.setattr(jobs, "score_verified_lines", counted)
    game = Game(pgn="", moves=MOVES, owner_color="black", owner_player_id="line-player")
    db_session.add(game)
    db_session.commit()
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    manager = EnginePoolManager(StockfishConfig(), interactive_concurrency=1, batch_concurrency=2)
    try:
        await jobs.run_analysis_for_game(game, db_session, registry, pool_manager=manager)
        lessons = db_session.exec(select(PersistedLessonOpportunity)).all()
        assert lessons, "the fixture game must produce a lesson"
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

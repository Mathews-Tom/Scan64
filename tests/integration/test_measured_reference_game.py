from __future__ import annotations

import json
from typing import Any

import chess
import pytest
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.analysis.orchestration import CandidatePosition
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.evaluation.real_games import CORPUS, RecordedAdapter
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.registry import PluginRegistry
from scan64.learning.profiling.models import ProfileObservation


@pytest.mark.asyncio
async def test_reference_white_analysis_replays_recorded_engine_only(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = json.loads(CORPUS.read_text())["games"][-1]
    adapter = RecordedAdapter(recorded)

    class SourcesOnly:
        async def analyze_position(
            self,
            fen: str,
            nodes: int | None = None,
            depth: int | None = None,
            time_ms: int | None = None,
            multipv: int = 1,
        ) -> EngineAnalysis:
            if nodes == 10_000:
                return await adapter.analyze_position(fen, nodes=nodes, multipv=multipv)
            for index, source in recorded["sources"].items():
                if adapter.fens[int(index)] == fen:
                    assert multipv <= source["config"]["multipv"]
                    return EngineAnalysis(
                        config=source["config"], raw_result=source["raw_result"][:multipv]
                    )
            raise AssertionError(f"Unrecorded source {fen}")

    class Focused:
        def __init__(self, *args: Any) -> None:
            pass

        async def run_focused_pass(
            self, candidates: list[CandidatePosition]
        ) -> list[EngineAnalysis]:
            return [
                EngineAnalysis(config={}, raw_result=recorded["focused"][str(c.move_index)])
                for c in candidates
            ]

    monkeypatch.setattr(jobs, "StockfishAdapter", lambda config: SourcesOnly())
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", Focused)
    board = chess.Board()
    moves = []
    for san in recorded["moves"]:
        move = board.parse_san(san)
        moves.append(move.uci())
        board.push(move)
    game = Game(
        pgn=(CORPUS.parents[1] / "reference_amateur_game.pgn").read_text(),
        moves=moves,
        owner_player_id="measured-white",
        owner_color="white",
    )
    db_session.add(game)
    db_session.commit()
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    await jobs.run_analysis_for_game(game, db_session, registry=registry)
    lessons = db_session.exec(
        select(PersistedLessonOpportunity).where(PersistedLessonOpportunity.game_id == game.id)
    ).all()
    assert 1 <= len(lessons) <= 5
    assert len({lesson.source_position_id for lesson in lessons}) == len(lessons)
    for lesson in lessons:
        source = db_session.get(Position, lesson.source_position_id)
        assert source is not None and chess.Board(source.fen).turn == chess.WHITE
    assert db_session.exec(select(Evidence)).all()
    assert db_session.exec(select(ProfileObservation)).all()

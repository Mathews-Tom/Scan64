from __future__ import annotations

from uuid import uuid4

import chess
import pytest
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import CandidatePosition
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position


class _CandidateOrchestrator:
    def __init__(self, *_: object) -> None:
        pass

    async def run_fast_pass(self, _: list[str]) -> list[CandidatePosition]:
        board = chess.Board()
        board.push_san("e4")
        analysis = EngineAnalysis(
            position_id=uuid4(),
            config={"nodes": 10_000},
            raw_result=[{"pv": ["e2e4"], "score_cp": 20}],
        )
        return [
            CandidatePosition(
                fen=board.fen(),
                move_index=0,
                before_analysis=analysis,
                after_analysis=EngineAnalysis(
                    position_id=uuid4(),
                    config={"nodes": 10_000},
                    raw_result=[{"pv": ["e7e5"], "score_cp": 10}],
                ),
                swing_cp=200,
            )
        ]



class _ConsecutiveCandidateOrchestrator:
    def __init__(self, *_: object) -> None:
        pass

    async def run_fast_pass(self, _: list[str]) -> list[CandidatePosition]:
        board = chess.Board()
        before_analysis = EngineAnalysis(
            position_id=uuid4(),
            config={"nodes": 10_000},
            raw_result=[{"pv": ["e2e4"], "score_cp": 20}],
        )
        board.push_san("e4")
        shared_analysis = EngineAnalysis(
            position_id=uuid4(),
            config={"nodes": 10_000},
            raw_result=[{"pv": ["e7e5"], "score_cp": 10}],
        )
        first_fen = board.fen()
        board.push_san("e5")
        after_analysis = EngineAnalysis(
            position_id=uuid4(),
            config={"nodes": 10_000},
            raw_result=[{"pv": ["g1f3"], "score_cp": 5}],
        )
        return [
            CandidatePosition(
                fen=first_fen,
                move_index=0,
                before_analysis=before_analysis,
                after_analysis=shared_analysis,
                swing_cp=200,
            ),
            CandidatePosition(
                fen=board.fen(),
                move_index=1,
                before_analysis=shared_analysis,
                after_analysis=after_analysis,
                swing_cp=200,
            ),
        ]

@pytest.mark.asyncio
async def test_candidate_positions_and_analyses_are_persisted(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _CandidateOrchestrator)
    game = Game(pgn="", moves=["e2e4"], owner_player_id="player-1")
    db_session.add(game)
    db_session.commit()

    await jobs.run_analysis_for_game(game, db_session)

    positions = db_session.exec(select(Position).where(Position.game_id == game.id)).all()
    analyses = db_session.exec(select(EngineAnalysis)).all()

    analysis_position_ids = {analysis.position_id for analysis in analyses}
    persisted_position_ids = {position.id for position in positions}

    assert len(positions) == 2
    assert analysis_position_ids == persisted_position_ids


@pytest.mark.asyncio
async def test_consecutive_candidates_share_one_persisted_position(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _ConsecutiveCandidateOrchestrator)
    game = Game(pgn="", moves=["e2e4", "e7e5"], owner_player_id="player-1")
    db_session.add(game)
    db_session.commit()

    await jobs.run_analysis_for_game(game, db_session)

    positions = db_session.exec(select(Position).where(Position.game_id == game.id)).all()
    analyses = db_session.exec(select(EngineAnalysis)).all()

    analysis_position_ids = {analysis.position_id for analysis in analyses}
    persisted_position_ids = {position.id for position in positions}

    assert len(positions) == 3
    assert analysis_position_ids == persisted_position_ids

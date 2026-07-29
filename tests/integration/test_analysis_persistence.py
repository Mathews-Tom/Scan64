from __future__ import annotations

from typing import Any
from uuid import uuid4

import chess
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
import scan64.learning.evidence.composer as evidence_composer
from scan64.api.models import Player, PlayerCredential, issue_player_token
from scan64.chess.analysis.models import (
    AnalysisJob,
    EngineAnalysis,
    PersistedLessonOpportunity,
)
from scan64.chess.analysis.orchestration import CandidatePosition
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.registry import PluginRegistry
from scan64.learning.scheduling.spaced_repetition import ReviewSchedule

HANGING_PIECE_FEN = "4r1k1/8/8/8/8/8/4Q3/K7 b - - 0 1"


def _seeded_detector_registry() -> PluginRegistry:
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    return registry


class _CandidateOrchestrator:
    def __init__(self, *_: object) -> None:
        pass

    async def run_fast_pass(
        self, _: list[str], initial_fen: str | None = None
    ) -> list[CandidatePosition]:
        board = chess.Board()
        board.push_san("e4")
        analysis = EngineAnalysis(
            position_id=uuid4(),
            config={"nodes": 10_000},
            raw_result=[{"pv": ["e2e4"], "score_cp": 20}],
        )
        return [
            CandidatePosition(
                fen=HANGING_PIECE_FEN,
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


class _FocusedOrchestrator:
    def __init__(self, *_: object) -> None:
        pass

    async def run_focused_pass(self, candidates: list[CandidatePosition]) -> list[EngineAnalysis]:
        return [
            EngineAnalysis(
                position_id=uuid4(),
                config={"nodes": 1_000_000, "multipv": 4},
                raw_result=[
                    {"pv": ["e8e2"], "score_cp": -250},
                    {"pv": ["g8h7"], "score_cp": -220},
                ],
            )
            for _ in candidates
        ]


class _ConsecutiveCandidateOrchestrator:
    def __init__(self, *_: object) -> None:
        pass

    async def run_fast_pass(
        self, _: list[str], initial_fen: str | None = None
    ) -> list[CandidatePosition]:
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
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FocusedOrchestrator)
    game = Game(pgn="", moves=["e2e4"], owner_player_id="player-1")
    db_session.add(game)
    db_session.commit()

    await jobs.run_analysis_for_game(game, db_session, _seeded_detector_registry())

    positions = db_session.exec(select(Position).where(Position.game_id == game.id)).all()
    analyses = db_session.exec(select(EngineAnalysis)).all()

    analysis_position_ids = {analysis.position_id for analysis in analyses}
    persisted_position_ids = {position.id for position in positions}

    assert len(positions) == 2
    assert analysis_position_ids == persisted_position_ids

    evidence = db_session.exec(select(Evidence)).all()
    position_ids = {str(position.id) for position in positions}
    analysis_ids = {str(analysis.id) for analysis in analyses}

    assert evidence
    assert all(item.position_id in position_ids for item in evidence)
    assert all(item.engine_analysis_id in analysis_ids for item in evidence)
    evidence_position = next(
        position for position in positions if str(position.id) == evidence[0].position_id
    )
    assert evidence_position.fen == HANGING_PIECE_FEN


@pytest.mark.asyncio
async def test_candidate_evidence_references_persisted_focused_multipv(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _CandidateOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FocusedOrchestrator)
    game = Game(pgn="", moves=["e2e4"], owner_player_id="player-1")
    db_session.add(game)
    db_session.commit()

    await jobs.run_analysis_for_game(game, db_session, _seeded_detector_registry())

    focused_analysis = next(
        analysis
        for analysis in db_session.exec(select(EngineAnalysis)).all()
        if analysis.config.get("multipv") == 4
    )
    evidence = next(
        item for item in db_session.exec(select(Evidence)).all() if item.kind == "engine_analysis"
    )

    assert evidence.engine_analysis_id == str(focused_analysis.id)
    assert evidence.payload["focused_multipv"] == focused_analysis.raw_result


@pytest.mark.asyncio
async def test_production_job_persists_arbitrated_secondary_diagnoses(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _CandidateOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FocusedOrchestrator)
    game = Game(pgn="", moves=["e2e4"], owner_player_id="player-1")
    db_session.add(game)
    db_session.commit()

    await jobs.run_analysis_for_game(game, db_session, _seeded_detector_registry())

    persisted = db_session.exec(select(PersistedLessonOpportunity)).one()
    diagnosis = persisted.lesson_spec["diagnosis"]
    assert diagnosis["primary"] == "board_awareness.hanging_piece"
    assert diagnosis["secondary"] == ["threat_processing.missed_direct_threat"]
    assert persisted.source_position_id is not None
    source_position = db_session.get(Position, persisted.source_position_id)
    assert source_position is not None
    assert source_position.fen == chess.Board().fen()
    schedule = db_session.get(ReviewSchedule, ("player-1", str(persisted.id)))
    assert schedule is not None
    assert schedule.skill_id == diagnosis["primary"]
    assert schedule.next_review_at is not None


@pytest.mark.asyncio
async def test_consecutive_candidates_share_one_persisted_position(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _ConsecutiveCandidateOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FocusedOrchestrator)
    game = Game(pgn="", moves=["e2e4", "e7e5"], owner_player_id="player-1")
    db_session.add(game)
    db_session.commit()

    await jobs.run_analysis_for_game(game, db_session, _seeded_detector_registry())

    positions = db_session.exec(select(Position).where(Position.game_id == game.id)).all()
    analyses = db_session.exec(select(EngineAnalysis)).all()

    analysis_position_ids = {analysis.position_id for analysis in analyses}
    persisted_position_ids = {position.id for position in positions}

    assert len(positions) == 3
    assert analysis_position_ids == persisted_position_ids


def test_failed_analysis_job_rolls_back_partial_artifacts(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    game = Game(pgn="", owner_player_id="player-1")
    db_session.add(game)
    db_session.flush()
    job = AnalysisJob(game_id=game.id)
    db_session.add(job)
    db_session.commit()

    async def partially_persist(_game: Game, session: Session) -> None:
        session.add(
            Position(
                game_id=game.id,
                fen="8/8/8/8/8/8/8/K6k w - - 0 1",
                canonical_id="partial-artifact",
                side_to_move="w",
            )
        )
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(jobs, "run_analysis_for_game", partially_persist)

    jobs.execute_analysis_job(job.id)

    db_session.expire_all()
    stored_job = db_session.get(AnalysisJob, job.id)

    assert stored_job is not None
    assert stored_job.status == "failed"
    assert stored_job.error == "analysis failed"
    assert db_session.exec(select(Position).where(Position.game_id == game.id)).all() == []


@pytest.mark.asyncio
async def test_completed_analysis_populates_positions_and_evidence_endpoints(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, token_hash = issue_player_token()
    player = Player(id="endpoint-player")
    db_session.add(player)
    db_session.add(PlayerCredential(player_id=player.id, token_hash=token_hash))
    game = Game(pgn="", moves=["e2e4"], owner_player_id=player.id)
    db_session.add(game)
    db_session.commit()
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _CandidateOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FocusedOrchestrator)

    await jobs.run_analysis_for_game(game, db_session)

    positions_response = client.get(
        f"/v1/games/{game.id}/positions", headers={"Authorization": f"Bearer {token}"}
    )
    evidence_response = client.get(
        f"/v1/players/{player.id}/evidence",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert positions_response.status_code == 200
    assert positions_response.json()
    assert all(position["analysis"] is not None for position in positions_response.json())
    assert evidence_response.status_code == 200
    assert evidence_response.json()["evidence_items"]


@pytest.mark.asyncio
async def test_every_constructed_evidence_is_persisted(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    constructed_evidence_ids: list[str] = []

    def construct_evidence(
        *,
        kind: str,
        position_id: str,
        engine_analysis_id: str,
        claim: str,
        payload: dict[str, Any],
    ) -> Evidence:
        evidence = Evidence(
            evidence_id=f"ev_{uuid4()}",
            kind=kind,
            position_id=position_id,
            engine_analysis_id=engine_analysis_id,
            claim=claim,
            payload=payload,
        )
        constructed_evidence_ids.append(evidence.evidence_id)
        return evidence

    monkeypatch.setattr(evidence_composer, "_evidence", construct_evidence)
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _CandidateOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FocusedOrchestrator)
    game = Game(pgn="", moves=["e2e4"], owner_player_id="guard-player")
    db_session.add(game)
    db_session.commit()

    await jobs.run_analysis_for_game(game, db_session, _seeded_detector_registry())

    persisted_ids = {item.evidence_id for item in db_session.exec(select(Evidence)).all()}

    assert persisted_ids
    assert set(constructed_evidence_ids) == persisted_ids

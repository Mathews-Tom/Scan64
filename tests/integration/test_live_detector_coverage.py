from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict, cast
from uuid import uuid4

import chess
import pytest
from sqlmodel import Session, select

import scan64.chess.analysis.jobs as jobs
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.analysis.orchestration import CandidatePosition
from scan64.chess.boards import board_from
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry


class CandidateFixture(TypedDict):
    move_index: int
    swing_cp: int
    fast_before_pv: list[str]
    fast_after_pv: list[str]
    focused_multipv: list[list[str]]


class LiveFixture(TypedDict):
    id: str
    initial_fen: str
    moves: list[str]
    candidate: CandidateFixture
    expected_labels: list[str]


def _load_fixtures() -> list[LiveFixture]:
    fixture_path = Path(__file__).parents[1] / "fixtures" / "live_detector_corpus.json"
    return cast(list[LiveFixture], json.loads(fixture_path.read_text()))


def _replay_fixture(fixture: LiveFixture) -> tuple[chess.Board, chess.Board, str]:
    board = board_from(fixture["initial_fen"])
    before_candidate = board.copy()
    candidate = fixture["candidate"]
    for index, uci in enumerate(fixture["moves"]):
        if index == candidate["move_index"]:
            before_candidate = board.copy()
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves, f"{fixture['id']}: illegal game move {uci}"
        board.push(move)
    return before_candidate, board, board.fen()


def _assert_legal_pv(board: chess.Board, pv: list[str], fixture_id: str) -> None:
    replay = board.copy()
    for uci in pv:
        move = chess.Move.from_uci(uci)
        assert move in replay.legal_moves, f"{fixture_id}: illegal PV move {uci}"
        replay.push(move)


_FIXTURES = _load_fixtures()
_FIXTURES_BY_INITIAL_FEN = {fixture["initial_fen"]: fixture for fixture in _FIXTURES}
_FIXTURES_BY_CANDIDATE_FEN = {
    _replay_fixture(fixture)[2]: fixture for fixture in _FIXTURES
}


class _FixtureFastPassOrchestrator:
    def __init__(self, *_: object) -> None:
        pass

    async def run_fast_pass(
        self, moves: list[str], initial_fen: str | None = None
    ) -> list[CandidatePosition]:
        if initial_fen is None:
            raise AssertionError("The live corpus always supplies an initial FEN")
        fixture = _FIXTURES_BY_INITIAL_FEN[initial_fen]
        candidate = fixture["candidate"]
        before_board, after_board, after_fen = _replay_fixture(fixture)
        _assert_legal_pv(before_board, candidate["fast_before_pv"], fixture["id"])
        _assert_legal_pv(after_board, candidate["fast_after_pv"], fixture["id"])
        return [
            CandidatePosition(
                fen=after_fen,
                move_index=candidate["move_index"],
                before_analysis=EngineAnalysis(
                    position_id=uuid4(),
                    config={"nodes": 10_000, "multipv": 1},
                    raw_result=[{"pv": candidate["fast_before_pv"], "score_cp": 0}],
                ),
                after_analysis=EngineAnalysis(
                    position_id=uuid4(),
                    config={"nodes": 10_000, "multipv": 1},
                    raw_result=[{"pv": candidate["fast_after_pv"], "score_cp": -300}],
                ),
                swing_cp=candidate["swing_cp"],
            )
        ]


class _FixtureFocusedPassOrchestrator:
    def __init__(self, *_: object) -> None:
        pass

    async def run_focused_pass(
        self, candidates: list[CandidatePosition]
    ) -> list[EngineAnalysis]:
        analyses = []
        for candidate in candidates:
            fixture = _FIXTURES_BY_CANDIDATE_FEN[candidate.fen]
            _, after_board, _ = _replay_fixture(fixture)
            multipv = fixture["candidate"]["focused_multipv"]
            assert len(multipv) >= 2, f"{fixture['id']}: focused result is not MultiPV"
            for line in multipv:
                _assert_legal_pv(after_board, line, fixture["id"])
            analyses.append(
                EngineAnalysis(
                    position_id=uuid4(),
                    config={"nodes": 1_000_000, "multipv": 4},
                    raw_result=[{"pv": line, "score_cp": -300} for line in multipv],
                )
            )
        return analyses


def _registry() -> PluginRegistry:
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    return registry


def _diagnosis_codes(lesson_spec: dict[str, Any]) -> set[str]:
    diagnosis = cast(dict[str, Any], lesson_spec["diagnosis"])
    return {cast(str, diagnosis["primary"]), *cast(list[str], diagnosis.get("secondary", []))}


@pytest.mark.asyncio
async def test_live_detector_coverage_and_precision(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _FixtureFastPassOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FixtureFocusedPassOrchestrator)
    registry = _registry()
    registry_stats = {code: {"tp": 0, "fp": 0, "fn": 0} for code in SEED_CODES}
    isolated_stats = {code: {"tp": 0, "fp": 0, "fn": 0} for code in SEED_CODES}

    for fixture in _FIXTURES:
        game = Game(
            pgn="",
            headers={"FEN": fixture["initial_fen"]},
            moves=fixture["moves"],
            owner_player_id="fixture-player",
        )
        db_session.add(game)
        db_session.commit()

        await jobs.run_analysis_for_game(game, db_session, registry)

        persisted = db_session.exec(
            select(PersistedLessonOpportunity).where(PersistedLessonOpportunity.game_id == game.id)
        ).one()
        registry_codes = _diagnosis_codes(persisted.lesson_spec)
        expected_codes = set(fixture["expected_labels"])
        positions = db_session.exec(
            select(Position).where(Position.game_id == game.id)
        ).all()
        position_ids = {str(position.id) for position in positions}
        game_evidence = [
            item
            for item in db_session.exec(select(Evidence)).all()
            if item.position_id in position_ids
        ]
        assert game_evidence
        opportunity = LearningOpportunity(
            opportunity_id=fixture["id"],
            position_id=game_evidence[0].position_id,
            player_id="fixture-player",
            game_id=str(game.id),
            played_move=fixture["moves"][fixture["candidate"]["move_index"]],
            engine_eval_before=0.0,
            engine_eval_after=-(fixture["candidate"]["swing_cp"] / 100.0),
        )
        isolated_codes: set[str] = set()
        for name in registry.names(kind=PluginKind.PATTERN_DETECTOR):
            detector = registry.get(kind=PluginKind.PATTERN_DETECTOR, name=name)
            assert isinstance(detector, PatternDetector)
            candidates = await detector.detect(
                opportunity, game_evidence, PlayerContext(player_id="fixture-player")
            )
            selected = arbitrate_diagnoses(candidates, game_evidence)
            if selected is not None:
                primary, secondary = selected
                isolated_codes.add(primary.skill_id)
                isolated_codes.update(candidate.skill_id for candidate in secondary)
        assert registry_codes == expected_codes
        assert isolated_codes == expected_codes

        for code in SEED_CODES:
            for predicted, stats in (
                (registry_codes, registry_stats),
                (isolated_codes, isolated_stats),
            ):
                if code in expected_codes and code in predicted:
                    stats[code]["tp"] += 1
                elif code not in expected_codes and code in predicted:
                    stats[code]["fp"] += 1
                elif code in expected_codes:
                    stats[code]["fn"] += 1

    for code in SEED_CODES:
        registry_tp = registry_stats[code]["tp"]
        registry_fp = registry_stats[code]["fp"]
        isolated_tp = isolated_stats[code]["tp"]
        isolated_fp = isolated_stats[code]["fp"]
        registry_precision = registry_tp / (registry_tp + registry_fp)
        isolated_precision = isolated_tp / (isolated_tp + isolated_fp)
        print(
            f"{code}: registry TP={registry_tp} FP={registry_fp} FN={registry_stats[code]['fn']} "
            f"precision={registry_precision:.3f}; isolated TP={isolated_tp} FP={isolated_fp} "
            f"FN={isolated_stats[code]['fn']} precision={isolated_precision:.3f}"
        )
        assert registry_tp >= 1
        assert registry_precision >= isolated_precision

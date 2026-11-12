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
from scan64.chess.analysis.orchestration import CandidatePosition, FastPassResult
from scan64.chess.boards import board_from
from scan64.chess.games.models import Game
from scan64.chess.positions.models import Position
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evaluation.real_games import reference_labels
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
_FIXTURES_BY_CANDIDATE_FEN = {_replay_fixture(fixture)[2]: fixture for fixture in _FIXTURES}


class _FixtureFastPassOrchestrator:
    def __init__(self, *_: object) -> None:
        pass

    async def run_fast_pass(
        self, moves: list[str], initial_fen: str | None = None
    ) -> FastPassResult:
        if initial_fen is None:
            raise AssertionError("The live corpus always supplies an initial FEN")
        fixture = _FIXTURES_BY_INITIAL_FEN[initial_fen]
        candidate = fixture["candidate"]
        before_board, after_board, after_fen = _replay_fixture(fixture)
        _assert_legal_pv(before_board, candidate["fast_before_pv"], fixture["id"])
        _assert_legal_pv(after_board, candidate["fast_after_pv"], fixture["id"])
        candidates = [
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
        evaluations = [EngineAnalysis(raw_result=[]) for _ in range(len(moves) + 1)]
        evaluations[candidate["move_index"]] = candidates[0].before_analysis
        evaluations[candidate["move_index"] + 1] = candidates[0].after_analysis
        return FastPassResult(candidates, evaluations)


class _FixtureFocusedPassOrchestrator:
    def __init__(self, *_: object) -> None:
        pass

    async def run_focused_pass(self, candidates: list[CandidatePosition]) -> list[EngineAnalysis]:
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
async def test_recorded_synthetic_fixtures_obey_trusted_single_cause_policy(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "FastPassOrchestrator", _FixtureFastPassOrchestrator)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", _FixtureFocusedPassOrchestrator)

    async def source_analysis(_self: object, fen: str, **limits: int) -> EngineAnalysis:
        # Keep the new source pass on the same scripted engine evidence as
        # the existing fast/focused corpus, rather than mixing in live scores.
        fixture = next(item for item in _FIXTURES if _replay_fixture(item)[0].fen() == fen)
        pv = fixture["candidate"]["fast_before_pv"]
        _assert_legal_pv(chess.Board(fen), pv, fixture["id"])
        return EngineAnalysis(
            position_id=uuid4(), config=limits, raw_result=[{"pv": pv, "score_cp": 0}]
        )

    monkeypatch.setattr(jobs.StockfishAdapter, "analyze_position", source_analysis)
    registry = _registry()
    seen_trusted: set[str] = set()
    seen_untrusted: set[str] = set()

    for fixture in _FIXTURES:
        game = Game(
            pgn="",
            headers={"FEN": fixture["initial_fen"]},
            moves=fixture["moves"],
            owner_color="white",
            owner_player_id="fixture-player",
        )
        db_session.add(game)
        db_session.commit()

        await jobs.run_analysis_for_game(game, db_session, registry)

        persisted = db_session.exec(
            select(PersistedLessonOpportunity).where(PersistedLessonOpportunity.game_id == game.id)
        ).all()
        registry_codes = set().union(*(_diagnosis_codes(item.lesson_spec) for item in persisted))
        assert len(persisted) <= 1
        for item in persisted:
            assert item.lesson_spec["diagnosis"]["secondary"] == []
        assert registry_codes <= TRUSTED_CODES
        positions = db_session.exec(select(Position).where(Position.game_id == game.id)).all()
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
            if candidates and name not in TRUSTED_CODES:
                seen_untrusted.add(name)
            selected = arbitrate_diagnoses(candidates, game_evidence)
            if selected is not None:
                primary, secondary = selected
                isolated_codes.add(primary.skill_id)
                assert secondary == ()
        assert registry_codes <= isolated_codes
        assert bool(registry_codes) == bool(isolated_codes)
        seen_trusted.update(registry_codes)
        before, after, _ = _replay_fixture(fixture)
        labels = reference_labels(
            before,
            after,
            [{"pv": fixture["candidate"]["fast_before_pv"], "score_cp": 0}],
            [{"pv": line} for line in fixture["candidate"]["focused_multipv"]],
        )
        assert registry_codes <= labels

    assert seen_trusted
    assert seen_untrusted

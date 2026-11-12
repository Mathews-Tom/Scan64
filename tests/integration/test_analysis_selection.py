import io
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

import chess
import chess.pgn
import pytest
from chess_lesson_spec import Explanation, LessonSpec
from sqlalchemy import event
from sqlmodel import Session, select

from scan64.chess.analysis import jobs
from scan64.chess.analysis.models import EngineAnalysis, PersistedLessonOpportunity
from scan64.chess.analysis.orchestration import CandidatePosition
from scan64.chess.games.ingestion import ingest_fen
from scan64.chess.games.models import Game
from scan64.learning.diagnosis.models import DiagnosisCandidate
from scan64.learning.diagnosis.visibility import visible_game_lesson
from scan64.learning.evidence.composer import DIAGNOSIS_RULE_VERSION
from scan64.learning.evidence.models import Evidence
from scan64.learning.verification.verifier import LessonVerificationError


def _install_candidates(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> tuple[Game, list[CandidatePosition], list[str]]:
    parsed = chess.pgn.read_game(
        io.StringIO(
            (Path(__file__).parents[1] / "fixtures" / "reference_amateur_game.pgn").read_text()
        )
    )
    assert parsed is not None
    moves = list(parsed.mainline_moves())[:16]
    game = Game(
        pgn="",
        moves=[move.uci() for move in moves],
        owner_player_id="selection-owner",
        owner_color="white",
    )
    db.add(game)
    db.commit()
    board = chess.Board()
    candidates = []
    sources = []
    for index, move in enumerate(moves):
        fen = board.fen()
        alternative = next(legal for legal in board.legal_moves if legal != move)
        board.push(move)
        if index % 2 == 0:
            sources.append(fen)
            candidates.append(
                CandidatePosition(
                    fen=board.fen(),
                    move_index=index,
                    swing_cp=300,
                    before_analysis=EngineAnalysis(
                        position_id=uuid4(),
                        raw_result=[{"pv": [alternative.uci()], "score_cp": 100}],
                    ),
                    after_analysis=EngineAnalysis(
                        position_id=uuid4(),
                        raw_result=[{"pv": [next(iter(board.legal_moves)).uci()], "score_cp": 100}],
                    ),
                )
            )

    class FastPass:
        def __init__(self, *_: object) -> None:
            pass

        async def run_fast_pass(self, *_: object) -> list[CandidatePosition]:
            assert not db.new and not db.dirty
            return candidates

    class FocusedPass:
        def __init__(self, *_: object) -> None:
            pass

        async def run_focused_pass(self, *_: object) -> list[EngineAnalysis]:
            assert not db.new and not db.dirty
            return [
                EngineAnalysis(position_id=uuid4(), raw_result=candidate.after_analysis.raw_result)
                for candidate in candidates
            ]

    class Detector:
        async def detect(
            self, _opportunity: object, evidence: list[Any], _ctx: object
        ) -> list[DiagnosisCandidate]:
            evidence[0].payload["missed_type"] = "capture"
            return [
                DiagnosisCandidate(
                    skill_id="threat_processing.missed_capture",
                    confidence=1.0,
                    evidence_ids=[evidence[0].evidence_id],
                )
            ]

    async def explanation(*_: object) -> Explanation:
        return Explanation(text="Selection test evidence.")

    monkeypatch.setattr(jobs, "FastPassOrchestrator", FastPass)
    monkeypatch.setattr(jobs, "FocusedPassOrchestrator", FocusedPass)
    monkeypatch.setattr(jobs, "_resolve_pattern_detectors", lambda _: (Detector(),))
    monkeypatch.setattr(jobs, "resolve_explanation", explanation)
    return game, candidates, sources


def _visible_existing_lesson(db: Session, game: Game, fen: str) -> PersistedLessonOpportunity:
    position = ingest_fen(fen, game.id)
    db.add(position)
    evidence_id = f"reserved-proof-{position.id}"
    db.add(
        Evidence(
            evidence_id=evidence_id,
            kind="missed_opportunity",
            position_id=str(position.id),
            engine_analysis_id="reserved-analysis",
            claim="Existing measured slot fixture",
            payload={"diagnosis_rule_version": DIAGNOSIS_RULE_VERSION},
        )
    )
    board = chess.Board(fen)
    lesson = PersistedLessonOpportunity(
        game_id=game.id,
        source_position_id=position.id,
        player_id=game.owner_player_id,
        lesson_spec={
            "schema_version": "1.0",
            "lesson_id": str(position.id),
            "source": {"kind": "custom", "fen": fen},
            "diagnosis": {
                "primary": "threat_processing.missed_direct_threat",
                "confidence": 1.0,
                "evidence_refs": [evidence_id],
            },
            "objective": {"type": "find_best_move", "instruction": "Choose a move."},
            "interaction": {
                "input": "click",
                "maximum_attempts": 3,
                "accepted_moves": [{"san": board.san(next(iter(board.legal_moves)))}],
            },
            "verification": {"status": "verified", "engine": "test"},
            "hints": [],
        },
    )
    db.add(lesson)
    return lesson


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_pv", [[], [{"pv": ["a1a8"], "score_cp": 100}]])
async def test_sound_and_invalid_candidates_are_replaced_before_any_database_writes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, invalid_pv: list[dict[str, Any]]
) -> None:
    game, candidates, sources = _install_candidates(db_session, monkeypatch)
    calls = []
    flushes = []
    last_engine_call_finished = False

    def before_flush(*_: object) -> None:
        assert last_engine_call_finished, "analysis acquired the write lock during engine work"
        flushes.append(True)

    async def source_analysis(_self: object, fen: str, **_: object) -> EngineAnalysis:
        nonlocal last_engine_call_finished
        assert not db_session.new and not db_session.dirty
        assert not flushes
        calls.append(fen)
        index = sources.index(fen)
        if index == 0:
            # The actual move is sound despite the fast-pass candidate flag.
            raw = [{"pv": [game.moves[candidates[index].move_index]], "score_cp": 100}]
        elif index == 1:
            raw = invalid_pv
        else:
            raw = candidates[index].before_analysis.raw_result
        if index == 7:
            last_engine_call_finished = True
        return EngineAnalysis(position_id=uuid4(), raw_result=raw)

    original_verify = jobs.verify_lesson

    def verify(spec: LessonSpec, analysis: EngineAnalysis) -> None:
        if spec.source.fen == sources[2]:
            raise LessonVerificationError("Rejected test verification evidence")
        original_verify(spec, analysis)

    monkeypatch.setattr(jobs.StockfishAdapter, "analyze_position", source_analysis)
    monkeypatch.setattr(jobs, "verify_lesson", verify)
    event.listen(db_session, "before_flush", before_flush)
    try:
        await jobs.run_analysis_for_game(game, db_session)
    finally:
        event.remove(db_session, "before_flush", before_flush)
    assert calls == sources
    assert flushes
    lessons = db_session.exec(select(PersistedLessonOpportunity)).all()
    assert len(lessons) == 5
    assert {lesson.lesson_spec["source"]["fen"] for lesson in lessons} == set(sources[3:])
    for opportunity in lessons:
        spec = LessonSpec.model_validate(opportunity.lesson_spec)
        assert [hint.level for hint in spec.hints] == [1, 2, 3]
        assert spec.explanation is not None
        assert any(drawing.command == "draw_arrow" for drawing in spec.explanation.visualizations)
        board = chess.Board(spec.source.fen)
        for drawing in spec.explanation.visualizations:
            if drawing.command == "animate_line":
                for uci in drawing.moves:
                    board.push_uci(uci)


@pytest.mark.asyncio
async def test_probability_loss_ranks_real_blunder_above_decided_and_mating_swings(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    game, candidates, sources = _install_candidates(db_session, monkeypatch)
    # Reserve four slots with durable coach history, leaving one lesson to choose.
    for fen in sources[3:7]:
        _visible_existing_lesson(db_session, game, fen)
    db_session.commit()
    candidates[0].swing_cp = 500
    candidates[0].before_analysis.raw_result[0]["score_cp"] = 1000
    candidates[0].after_analysis.raw_result[0]["score_cp"] = -500
    candidates[1].swing_cp = 200
    candidates[2].swing_cp = 1200
    candidates[2].before_analysis.raw_result[0].pop("score_cp")
    candidates[2].before_analysis.raw_result[0]["score_mate"] = 3
    candidates[2].after_analysis.raw_result[0].pop("score_cp")
    candidates[2].after_analysis.raw_result[0]["score_mate"] = -20
    calls = []

    async def source_analysis(_self: object, fen: str, **_: object) -> EngineAnalysis:
        calls.append(fen)
        return EngineAnalysis(
            position_id=uuid4(),
            raw_result=candidates[sources.index(fen)].before_analysis.raw_result,
        )

    monkeypatch.setattr(jobs.StockfishAdapter, "analyze_position", source_analysis)
    await jobs.run_analysis_for_game(game, db_session)
    assert calls == [sources[1]]
    assert len(db_session.exec(select(PersistedLessonOpportunity)).all()) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("skip_reason", ["sound", "invalid_pv", "verification", "kept"])
async def test_only_kept_lessons_count_toward_skill_redundancy(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, skip_reason: str
) -> None:
    game, candidates, sources = _install_candidates(db_session, monkeypatch)
    del candidates[3:]
    # A: skill X, loss .30, skipped; B: X, .25; C: Y, .12.
    # Penalizing X for A would incorrectly let C displace B in the last slot.
    # With two slots and A kept, its penalty must instead let C displace B.
    for candidate, loss in zip(candidates, (0.30, 0.25, 0.12), strict=True):
        candidate.before_analysis.raw_result[0]["score_cp"] = 0
        candidate.after_analysis.raw_result[0]["score_cp"] = (
            math.log((0.5 + loss) / (0.5 - loss)) / 0.00368208
        )
    existing_ids = set()
    existing_count = 3 if skip_reason == "kept" else 4
    for fen in sources[3 : 3 + existing_count]:
        lesson = _visible_existing_lesson(db_session, game, fen)
        existing_ids.add(lesson.id)
    db_session.commit()

    class Detector:
        async def detect(
            self, opportunity: Any, evidence: list[Any], _ctx: object
        ) -> list[DiagnosisCandidate]:
            if opportunity.played_move == "a3":
                evidence[0].payload["missed_type"] = "capture"
            return [
                DiagnosisCandidate(
                    skill_id=(
                        "threat_processing.missed_capture"
                        if opportunity.played_move == "a3"
                        else "board_awareness.hanging_piece"
                    ),
                    confidence=1.0,
                    evidence_ids=[evidence[0].evidence_id],
                )
            ]

    calls = []

    async def source_analysis(_self: object, fen: str, **_: object) -> EngineAnalysis:
        calls.append(fen)
        index = sources.index(fen)
        raw = candidates[index].before_analysis.raw_result
        if index == 0 and skip_reason == "sound":
            raw = [{"pv": [game.moves[0]], "score_cp": 100}]
        elif index == 0 and skip_reason == "invalid_pv":
            raw = []
        return EngineAnalysis(position_id=uuid4(), raw_result=raw)

    original_verify = jobs.verify_lesson

    def verify(spec: LessonSpec, analysis: EngineAnalysis) -> None:
        if spec.source.fen == sources[0] and skip_reason == "verification":
            raise LessonVerificationError("Rejected test verification evidence")
        original_verify(spec, analysis)

    monkeypatch.setattr(jobs, "_resolve_pattern_detectors", lambda _: (Detector(),))
    monkeypatch.setattr(jobs.StockfishAdapter, "analyze_position", source_analysis)
    monkeypatch.setattr(jobs, "verify_lesson", verify)
    await jobs.run_analysis_for_game(game, db_session)
    expected = [sources[0], sources[2]] if skip_reason == "kept" else sources[:2]
    assert calls == expected
    new_lessons = [
        lesson
        for lesson in db_session.exec(select(PersistedLessonOpportunity)).all()
        if lesson.id not in existing_ids
    ]
    expected_kept = {sources[0], sources[2]} if skip_reason == "kept" else {sources[1]}
    assert {lesson.lesson_spec["source"]["fen"] for lesson in new_lessons} == expected_kept


@pytest.mark.asyncio
async def test_hidden_legacy_lessons_reserve_neither_slots_nor_sources(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    game, candidates, sources = _install_candidates(db_session, monkeypatch)
    legacy = []
    for fen in sources[:5]:
        position = ingest_fen(fen, game.id)
        db_session.add(position)
        lesson = PersistedLessonOpportunity(
            game_id=game.id,
            source_position_id=position.id,
            player_id=game.owner_player_id,
            lesson_spec={"diagnosis": {"primary": "threat_processing.missed_capture"}},
        )
        db_session.add(lesson)
        legacy.append(lesson)
    db_session.commit()
    calls = []

    async def source_analysis(_self: object, fen: str, **_: object) -> EngineAnalysis:
        calls.append(fen)
        return EngineAnalysis(
            position_id=uuid4(),
            raw_result=candidates[sources.index(fen)].before_analysis.raw_result,
        )

    monkeypatch.setattr(jobs.StockfishAdapter, "analyze_position", source_analysis)
    await jobs.run_analysis_for_game(game, db_session)
    rows = db_session.exec(select(PersistedLessonOpportunity)).all()
    visible = [row for row in rows if visible_game_lesson(row, db_session) is not None]
    assert len(calls) == len(visible) == 5
    assert len(rows) == 10
    assert all(db_session.get(PersistedLessonOpportunity, row.id) is not None for row in legacy)
    assert all(visible_game_lesson(row, db_session) is None for row in legacy)

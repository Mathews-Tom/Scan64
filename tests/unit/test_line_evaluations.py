"""Engine evaluations after every move of each verified line (#367, #368).

Lessons: ``score_verified_lines`` replays the recorded Stockfish 18 searches of
``tests/fixtures/narrator_line_evaluations.json`` for the 50 narrator benchmark
lessons. Analysis Board: the recorded searches of
``tests/fixtures/analysis_board_line_evaluations.json`` along the recorded
lines. The pool tests use fake engines: no engine runs here.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import chess
import chess.engine
import chess.variant
import pytest
from chess_lesson_spec import Diagnosis

from scan64.chess.analysis.line_scoring import score_verified_lines
from scan64.chess.analysis.models import EngineAnalysis
from scan64.explanations.board_lines import (
    board_line_positions,
    board_lines_grounding,
    build_board_lines_facts,
    line_scores,
    scored_legal_lines,
    template_board_claims,
)
from scan64.explanations.facts import (
    LINE_EVALUATIONS_FIELD,
    LINE_NODES,
    MAX_LINE_PLIES,
    CentipawnEvaluation,
    UnknownEvaluation,
    build_narrator_facts,
)
from scan64.explanations.validator import validate_generated_explanation
from scan64.learning.evidence.models import Evidence
from scan64.providers.engine.uci import EngineCapabilityError
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager, PooledStockfishAdapter

_ROOT = Path(__file__).parents[2]
_LESSONS = json.loads((_ROOT / "benchmarks/narrator/positions.json").read_text())["positions"]
_LESSON_SEARCHES = json.loads((_ROOT / "tests/fixtures/narrator_line_evaluations.json").read_text())
_BOARD = json.loads((_ROOT / "tests/fixtures/analysis_board_lines.json").read_text())["positions"]
_BOARD_SEARCHES = json.loads(
    (_ROOT / "tests/fixtures/analysis_board_line_evaluations.json").read_text()
)


class _RecordedAdapter:
    """Answers each FEN with its recorded search; fails on any other FEN."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, int]] = []

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        self.calls.append((fen, nodes, multipv))
        return EngineAnalysis(
            config=_LESSON_SEARCHES["config"], raw_result=_LESSON_SEARCHES["searches"][fen]
        )


async def _scored_lesson(lesson: dict[str, Any]) -> tuple[list[Evidence], _RecordedAdapter]:
    evidence = [Evidence.model_validate(item) for item in lesson["evidence"]]
    adapter = _RecordedAdapter()
    await score_verified_lines(
        adapter, evidence, lesson["diagnosis"]["evidence_refs"], lesson["fen"], concurrency=2
    )
    return evidence, adapter


def _ends_game(fen: str, uci: tuple[str, ...]) -> bool:
    board = chess.Board(fen)
    for move in uci:
        board.push_uci(move)
    return board.is_game_over(claim_draw=False)


# --- Lessons ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("lesson", _LESSONS, ids=[lesson["position_id"] for lesson in _LESSONS])
async def test_every_ply_of_every_lesson_line_is_scored(lesson: dict[str, Any]) -> None:
    evidence, adapter = await _scored_lesson(lesson)
    assert all(call[1:] == (LINE_NODES, 1) for call in adapter.calls)
    assert len({call[0] for call in adapter.calls}) == len(adapter.calls)
    facts = build_narrator_facts(
        Diagnosis.model_validate(lesson["diagnosis"]), evidence, lesson["fen"]
    )
    assert facts.lines
    for line in facts.lines:
        assert len(line.moves) <= MAX_LINE_PLIES
        for move in line.moves:
            if isinstance(move.evaluation_after, UnknownEvaluation):
                # Only a game-over position is left unscored; its move states the result.
                assert move.ply == len(line.moves) and _ends_game(lesson["fen"], line.uci)
        if not isinstance(facts.position.evaluation_before, UnknownEvaluation):
            change = line.evaluation_change
            assert change is not None
            assert change.start == facts.position.evaluation_before
            assert change.end == line.moves[change.end_ply - 1].evaluation_after
            if isinstance(change.start, CentipawnEvaluation) and isinstance(
                change.end, CentipawnEvaluation
            ):
                assert change.change_cp == change.end.centipawns - change.start.centipawns


@pytest.mark.asyncio
async def test_line_scores_are_stored_in_the_payload_from_the_side_to_move() -> None:
    lesson = _LESSONS[0]
    evidence, _ = await _scored_lesson(lesson)
    referenced = [e for e in evidence if e.evidence_id in lesson["diagnosis"]["evidence_refs"]]
    entries = referenced[0].payload[LINE_EVALUATIONS_FIELD]
    assert entries and all(
        set(entry) == {"fen", "score_cp", "score_mate", "analysis_id"} for entry in entries
    )
    facts = build_narrator_facts(
        Diagnosis.model_validate(lesson["diagnosis"]), evidence, lesson["fen"]
    )
    learner = chess.Board(lesson["fen"]).turn
    by_fen = {entry["fen"]: entry for entry in entries}
    checked = 0
    for line in facts.lines:
        board = chess.Board(lesson["fen"])
        for move in line.moves:
            board.push_uci(move.uci)
            entry = by_fen.get(board.fen())
            evaluation = move.evaluation_after
            if entry is None or not isinstance(evaluation, CentipawnEvaluation):
                continue
            if evaluation.source != "line_pass":
                continue
            sign = 1 if board.turn == learner else -1
            assert evaluation.centipawns == sign * entry["score_cp"]
            checked += 1
    assert checked


@pytest.mark.asyncio
async def test_scoring_twice_searches_nothing_new() -> None:
    lesson = _LESSONS[0]
    evidence, first = await _scored_lesson(lesson)
    again = _RecordedAdapter()
    count = await score_verified_lines(
        again, evidence, lesson["diagnosis"]["evidence_refs"], lesson["fen"]
    )
    assert first.calls and count == 0 and again.calls == []


def _timed_out_capability() -> EngineCapabilityError:
    # ``bounded`` reports a search that overran its deadline this way.
    error = EngineCapabilityError("did not finish")
    error.__cause__ = TimeoutError()
    return error


_SEARCH_FAILURES = [
    (chess.engine.EngineTerminatedError("engine exited"), "engine_exited"),
    (chess.engine.EngineError("bad info"), "engine_error"),
    (EngineCapabilityError("no MultiPV"), "missing_capability"),
    (TimeoutError(), "timeout"),
    (_timed_out_capability(), "timeout"),
    (BrokenPipeError(), "os_error"),
]


class _FailingAdapter(_RecordedAdapter):
    """Fails every other search with ``error``; answers the rest from the recording."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error
        self.failed: set[str] = set()

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        if len(self.calls) % 2 == 0:
            self.calls.append((fen, nodes, multipv))
            self.failed.add(fen)
            raise self.error
        return await super().analyze_position(fen, nodes, depth, time_ms, multipv)


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "reason"), _SEARCH_FAILURES, ids=lambda value: str(value))
async def test_a_failed_line_search_leaves_its_plies_unknown(
    error: Exception, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    lesson = _LESSONS[0]
    evidence = [Evidence.model_validate(item) for item in lesson["evidence"]]
    adapter = _FailingAdapter(error)
    with caplog.at_level("WARNING", logger="scan64.chess.analysis.line_scoring"):
        count = await score_verified_lines(
            adapter, evidence, lesson["diagnosis"]["evidence_refs"], lesson["fen"], concurrency=2
        )
    assert count == len(adapter.calls) > len(adapter.failed) > 0
    facts = build_narrator_facts(
        Diagnosis.model_validate(lesson["diagnosis"]), evidence, lesson["fen"]
    )
    unknown = scored = 0
    for line in facts.lines:
        board = chess.Board(lesson["fen"])
        for move in line.moves:
            board.push_uci(move.uci)
            if board.fen() in adapter.failed:
                assert isinstance(move.evaluation_after, UnknownEvaluation)
                unknown += 1
            elif not isinstance(move.evaluation_after, UnknownEvaluation):
                scored += 1
    assert unknown and scored
    [record] = caplog.records
    assert record.getMessage().endswith(f": {reason}")
    # A reason code only: no position, move or engine message reaches the log.
    assert not any(fen.split()[0] in record.getMessage() for fen in adapter.failed)
    assert str(error) == "" or str(error) not in record.getMessage()


@pytest.mark.asyncio
async def test_a_programming_error_in_line_scoring_is_not_hidden() -> None:
    lesson = _LESSONS[0]
    evidence = [Evidence.model_validate(item) for item in lesson["evidence"]]
    with pytest.raises(KeyError):
        await score_verified_lines(
            _FailingAdapter(KeyError("bug")),
            evidence,
            lesson["diagnosis"]["evidence_refs"],
            lesson["fen"],
        )


@pytest.mark.asyncio
async def test_lesson_factor_rule_uses_the_scores_at_the_measured_points() -> None:
    used_points = 0
    for lesson in _LESSONS:
        evidence, _ = await _scored_lesson(lesson)
        facts = build_narrator_facts(
            Diagnosis.model_validate(lesson["diagnosis"]), evidence, lesson["fen"]
        )
        if facts.factors is None:
            continue
        for swing in facts.factors.swings:
            assert all(
                change.change_pawns > 0 if swing.swing_cp > 0 else change.change_pawns < 0
                for change in facts.factors.changes
                if change.compared_with == swing.compared_with
            )
            used_points += swing.source == "points"
    assert used_points >= 50


# --- Analysis Board -------------------------------------------------------------------


def _board_case(name: str, multipv: int) -> tuple[chess.Board, list[Any]]:
    entry = _BOARD[name]
    board = chess.Board(entry["fen"])
    analysis = EngineAnalysis(config=entry["config"], raw_result=entry["raw_result"])
    return board, scored_legal_lines(board, analysis)[:multipv]


def _recorded_board_searches(
    name: str, positions: list[chess.Board]
) -> list[EngineAnalysis | None]:
    recorded = {
        tuple(item["moves"]): item["raw_result"] for item in _BOARD_SEARCHES["positions"][name]
    }
    return [
        EngineAnalysis(
            config=_BOARD_SEARCHES["config"],
            raw_result=recorded[tuple(move.uci() for move in position.move_stack)],
        )
        for position in positions
    ]


@pytest.mark.parametrize("multipv", [1, 3, 4])
@pytest.mark.parametrize("name", sorted(_BOARD))
def test_every_ply_of_every_board_line_is_scored(name: str, multipv: int) -> None:
    board, lines = _board_case(name, multipv)
    positions = board_line_positions(board, lines)
    facts = build_board_lines_facts(
        board, lines, line_scores(positions, _recorded_board_searches(name, positions))
    )
    for line in facts.lines:
        for move in line.moves:
            known = not isinstance(move.evaluation_after, UnknownEvaluation)
            if move.ply > MAX_LINE_PLIES:
                assert not known
            elif not known:
                assert move.ply == len(line.moves) and _ends_game(board.fen(), line.uci)
        change = line.evaluation_change
        assert change is not None and change.start == facts.position.evaluation
        assert change.end_ply == max(
            m.ply for m in line.moves if not isinstance(m.evaluation_after, UnknownEvaluation)
        )
    validate_generated_explanation(template_board_claims(facts), board_lines_grounding(facts))


def test_board_positions_are_ordered_by_ply_and_skip_multipv_scores() -> None:
    board, lines = _board_case("italian_black", 3)
    positions = board_line_positions(board, lines)
    plies = [len(position.move_stack) for position in positions]
    assert plies == sorted(plies) and min(plies) == 2 and max(plies) <= MAX_LINE_PLIES
    keys = {" ".join(position.fen().split()[:4]) for position in positions}
    assert len(keys) == len(positions)


def test_unscored_board_plies_stay_unknown() -> None:
    board, lines = _board_case("open_centre", 3)
    positions = board_line_positions(board, lines)
    searches = _recorded_board_searches("open_centre", positions)
    limited = searches[:3] + [None] * (len(searches) - 3)
    facts = build_board_lines_facts(board, lines, line_scores(positions, limited))
    scored = {
        (line.line_id, move.ply)
        for line in facts.lines
        for move in line.moves
        if not isinstance(move.evaluation_after, UnknownEvaluation)
    }
    assert {ply for _, ply in scored} == {1, 2}
    assert len([key for key in scored if key[1] == 2]) == 3
    validate_generated_explanation(template_board_claims(facts), board_lines_grounding(facts))


def test_board_factor_rule_uses_the_scores_at_the_measured_points() -> None:
    sources = []
    for name in sorted(_BOARD):
        board, lines = _board_case(name, 4)
        positions = board_line_positions(board, lines)
        facts = build_board_lines_facts(
            board, lines, line_scores(positions, _recorded_board_searches(name, positions))
        )
        for line in facts.lines[1:]:
            assert line.factors is not None
            sources += [swing.source for swing in line.factors.swings]
    assert sources.count("points") > sources.count("mistake")


# --- Pool: deadline, admission, engine identity ------------------------------------------


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> EnginePoolManager:
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    return EnginePoolManager(StockfishConfig())


def _boards(count: int) -> list[chess.Board]:
    boards = []
    board = chess.Board()
    for move in ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "d2d3", "f8c5")[:count]:
        board.push_uci(move)
        boards.append(board.copy())
    return boards


def _identity(digest: str) -> AsyncMock:
    return AsyncMock(return_value={"engine_name": "fake", "engine_version": digest})


@pytest.mark.asyncio
async def test_deadline_limits_scoring_to_the_first_positions(
    manager: EnginePoolManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def slow(*args: Any, **kwargs: Any) -> EngineAnalysis:
        assert kwargs == {"nodes": LINE_NODES}
        await asyncio.sleep(0.1)
        return EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 10}])

    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", slow)
    monkeypatch.setattr(PooledStockfishAdapter, "analysis_identity", _identity("sha256:a"))
    loop = asyncio.get_running_loop()
    results = await manager.analyze_hint_positions(_boards(8), deadline=loop.time() + 0.35)
    done = [result is not None for result in results]
    assert done[0] and not done[-1]
    assert done == sorted(done, reverse=True), "scored positions are a prefix"
    assert manager.interactive_pool._queue.qsize() == 2
    assert not manager._hint_in_progress
    expired = await manager.analyze_hint_positions(_boards(2), deadline=loop.time() - 1)
    assert expired == [None, None]


@pytest.mark.asyncio
async def test_busy_hint_capacity_scores_nothing(
    manager: EnginePoolManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyze = AsyncMock()
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    manager._hint_in_progress = True
    loop = asyncio.get_running_loop()
    assert await manager.analyze_hint_positions(_boards(2), deadline=loop.time() + 1) == [
        None,
        None,
    ]
    analyze.assert_not_awaited()


@pytest.mark.asyncio
async def test_line_cache_is_keyed_by_engine_identity(
    manager: EnginePoolManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["e7e5"], "score_cp": 5}]))
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    loop = asyncio.get_running_loop()
    boards = _boards(2)
    monkeypatch.setattr(PooledStockfishAdapter, "analysis_identity", _identity("sha256:a"))
    await manager.analyze_hint_positions(boards, deadline=loop.time() + 1)
    await manager.analyze_hint_positions(boards, deadline=loop.time() + 1)
    assert analyze.await_count == 2
    monkeypatch.setattr(PooledStockfishAdapter, "analysis_identity", _identity("sha256:b"))
    await manager.analyze_hint_positions(boards, deadline=loop.time() + 1)
    assert analyze.await_count == 4


@pytest.mark.asyncio
async def test_line_cache_is_keyed_by_variant(
    manager: EnginePoolManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 5}]))
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    monkeypatch.setattr(PooledStockfishAdapter, "analysis_identity", _identity("sha256:a"))
    loop = asyncio.get_running_loop()
    # The same starting FEN, standard then atomic: the second is not the first's cache entry.
    await manager.analyze_hint_positions([chess.Board()], deadline=loop.time() + 1)
    await manager.analyze_hint_positions([chess.variant.AtomicBoard()], deadline=loop.time() + 1)
    assert analyze.await_count == 2
    assert isinstance(analyze.await_args_list[1].args[0], chess.variant.AtomicBoard)


@pytest.mark.asyncio
async def test_an_engine_that_exits_during_line_scoring_is_discarded(
    manager: EnginePoolManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyze = AsyncMock(side_effect=chess.engine.EngineTerminatedError("engine exited"))
    discard: list[PooledStockfishAdapter] = []
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    monkeypatch.setattr(PooledStockfishAdapter, "analysis_identity", _identity("sha256:a"))
    monkeypatch.setattr(PooledStockfishAdapter, "discard", lambda self: discard.append(self))
    loop = asyncio.get_running_loop()
    results = await manager.analyze_hint_positions(_boards(2), deadline=loop.time() + 1)
    assert results == [None, None]
    assert len(discard) == 1
    assert manager.interactive_pool._queue.qsize() == 2
    assert not manager._hint_in_progress


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "reason"), _SEARCH_FAILURES, ids=lambda value: str(value))
async def test_a_failed_board_line_search_answers_with_the_scores_it_has(
    manager: EnginePoolManager,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    reason: str,
) -> None:
    scored = EngineAnalysis(raw_result=[{"pv": ["e7e5"], "score_cp": 5}])
    analyze = AsyncMock(side_effect=[scored, error])
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    monkeypatch.setattr(PooledStockfishAdapter, "analysis_identity", _identity("sha256:a"))
    monkeypatch.setattr(PooledStockfishAdapter, "discard", lambda self: None)
    loop = asyncio.get_running_loop()
    with caplog.at_level("INFO", logger="scan64.providers.stockfish.pool"):
        results = await manager.analyze_hint_positions(_boards(3), deadline=loop.time() + 1)
    assert results[0] is not None and results[1:] == [None, None]
    assert manager.interactive_pool._queue.qsize() == 2
    assert not manager._hint_in_progress
    [record] = caplog.records
    assert record.getMessage() == (
        f"Analysis Board line scoring left 2 of 3 positions unscored: {reason}"
    )


class _ExitedEngine:
    def __init__(self, exited: bool) -> None:
        self.returncode: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        if exited:
            self.returncode.set_result(-9)


@pytest.mark.asyncio
async def test_an_engine_that_exited_unnoticed_is_replaced_at_checkout(
    manager: EnginePoolManager,
) -> None:
    # python-chess can leave a search to time out on a dead engine instead of
    # raising EngineTerminatedError; the next checkout must not get that engine.
    pool = manager.batch_pool
    await pool.initialize()
    dead, alive = pool._adapters
    dead._engine = _ExitedEngine(exited=True)  # type: ignore[assignment]
    alive._engine = _ExitedEngine(exited=False)  # type: ignore[assignment]
    for _ in range(2):
        async with pool.acquire():
            pass
    assert dead._engine is None
    assert alive._engine is not None

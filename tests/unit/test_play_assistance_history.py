"""Replay-aware grading through the bounded pool and the real UCI protocol."""

from pathlib import Path
from unittest.mock import AsyncMock

import chess
import chess.engine
import pytest

from scan64.learning.play_assistance import grade_play_moves
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager

INITIAL_FEN = "r7/8/8/8/2b1P3/N7/3P4/K1k1B2Q b - - 0 1"
REPLAY = ["a8b8", "a3c2", "b8a8", "c2a3"] * 3 + ["a8b8", "a3c2"]


def _expect_search(
    transport: chess.engine.MockTransport,
    board: chess.Board,
    score: str,
    pv: str,
    *,
    first: bool = False,
) -> None:
    if first:
        transport.expect("setoption name MultiPV value 4")
    transport.expect("ucinewgame")
    transport.expect("isready", ["readyok"])
    position = f"position fen {board.root().fen(en_passant='fen')}"
    if board.move_stack:
        position += " moves " + " ".join(move.uci() for move in board.move_stack)
    transport.expect(position)
    transport.expect(
        "go nodes 200000 movetime 1500",
        [f"info depth 2 score {score} pv {pv}", f"bestmove {pv.split()[0]}"],
    )


@pytest.mark.asyncio
async def test_forced_future_fivefold_uses_history_on_wire_and_in_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    board = chess.Board(INITIAL_FEN)
    for uci in REPLAY:
        board.push_uci(uci)
    move = chess.Move.from_uci("b8a8")
    branch = board.copy()
    branch.push(move)
    assert not branch.is_game_over(claim_draw=False)
    assert [reply.uci() for reply in branch.legal_moves] == ["c2a3"]
    future = branch.copy()
    future.push_uci("c2a3")
    assert future.outcome(claim_draw=False) == chess.Outcome(
        chess.Termination.FIVEFOLD_REPETITION, None
    )

    # Equal current FENs do not imply equal future outcomes. Without the replay,
    # White's forced reply lets Black mate instead of ending automatically.
    plain = chess.Board(board.fen())
    plain_branch = plain.copy()
    plain_branch.push(move)
    assert branch.fen() == plain_branch.fen()
    plain_future = plain_branch.copy()
    plain_future.push_uci("c2a3")
    assert not plain_future.is_game_over(claim_draw=False)
    for uci in ("a8b8", "a3c2", "b8b1"):
        plain_future.push_uci(uci)
    assert plain_future.is_checkmate()
    best = board.copy()
    best.push_uci("b8b1")
    assert best.is_checkmate()

    binary = tmp_path / "deterministic-uci"
    binary.write_bytes(b"deterministic history-aware UCI transcript")
    transports: list[chess.engine.MockTransport] = []

    async def start(_command: str):
        engine = chess.engine.UciProtocol()
        transport = chess.engine.MockTransport(engine)
        transport.expect(
            "uci",
            [
                "id name Deterministic history witness",
                "option name Threads type spin default 1 min 1 max 1",
                "option name Hash type spin default 16 min 1 max 16",
                "option name Skill Level type spin default 20 min 0 max 20",
                "option name UCI_LimitStrength type check default false",
                "option name UCI_Elo type spin default 1320 min 1320 max 3190",
                "option name MultiPV type spin default 1 min 1 max 4",
                "uciok",
            ],
        )
        await engine.initialize()
        # There is no subprocess behind MockTransport; only shutdown is mocked.
        monkeypatch.setattr(engine, "quit", AsyncMock())
        if not transports:
            _expect_search(transport, plain, "mate 1", "b8b1", first=True)
            _expect_search(transport, board, "mate 1", "b8b1")
        else:
            _expect_search(transport, plain_branch, "mate -2", "c2a3 a8b8 a3c2 b8b1", first=True)
            _expect_search(transport, branch, "cp 0", "c2a3")
        transports.append(transport)
        return transport, engine

    monkeypatch.setattr(chess.engine, "popen_uci", start)
    manager = EnginePoolManager(StockfishConfig(binary_path=str(binary)))
    original_fen, original_moves = board.fen(), list(board.move_stack)
    try:
        # Prewarm both history-free entries before grading the same FENs with
        # replay: either source or branch reuse would suppress a required search.
        plain_grade = (await grade_play_moves(plain, [move], manager))[0]
        assert plain_grade.band == "sound"
        assert plain_grade.win_probability_loss == 0.0
        grade = (await grade_play_moves(board, [move], manager))[0]
        assert grade.band == "blunder"
        assert grade.win_probability_loss == 0.5
        assert len(transports) == 2
        for transport in transports:
            transport.assert_done()
        assert not manager.batch_pool._initialized
        assert manager.interactive_pool._queue.qsize() == 2

        # Results are copied on cache reads, and neither history can poison the
        # other. No protocol search is available until explicit invalidation.
        cached = await manager.analyze_hint(branch)
        cached.raw_result[0]["score_cp"] = 1000
        assert (await manager.analyze_hint(branch)).raw_result[0]["score_cp"] == 0
        assert (await grade_play_moves(plain, [move], manager))[0] == plain_grade
        assert (await grade_play_moves(board, [move], manager))[0] == grade
        manager.invalidate_hint(branch)
        assert (await grade_play_moves(plain, [move], manager))[0] == plain_grade
        _expect_search(transports[0], branch, "cp 0", "c2a3")
        assert (await grade_play_moves(board, [move], manager))[0] == grade
        for transport in transports:
            transport.assert_done()
        assert board.fen() == original_fen
        assert board.move_stack == original_moves
    finally:
        await manager.close()

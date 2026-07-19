from __future__ import annotations

import asyncio
import json
import math
import os
import re
from pathlib import Path

import chess
import chess.engine
import pytest

POLICY_LINE = re.compile(
    r"^info string\s+([a-h][1-8][a-h][1-8][qrbn]?).*?\(P:\s+([0-9.]+)%\)",
    re.MULTILINE,
)
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "maia_human_1500.json"


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(f"{name} is required for the real-model Maia benchmark")
    return value


async def read_until(reader: asyncio.StreamReader, expected: str) -> str:
    output: list[str] = []
    async with asyncio.timeout(30):
        while line := await reader.readline():
            decoded_line = line.decode()
            output.append(decoded_line)
            if expected in decoded_line:
                return "".join(output)
    raise RuntimeError(f"Maia Lc0 terminated before {expected!r}")


async def maia_policy(binary_path: str, weights_path: str, fen: str) -> dict[str, float]:
    process = await asyncio.create_subprocess_exec(
        binary_path,
        f"--weights={weights_path}",
        "--threads=1",
        "--verbose-move-stats",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        process.stdin.write(b"uci\n")
        await process.stdin.drain()
        await read_until(process.stdout, "uciok")
        process.stdin.write(b"isready\n")
        await process.stdin.drain()
        await read_until(process.stdout, "readyok")
        process.stdin.write(f"position fen {fen}\ngo nodes 1\n".encode())
        await process.stdin.drain()
        output = await read_until(process.stdout, "bestmove")
    finally:
        process.stdin.write(b"quit\n")
        await process.stdin.drain()
        await process.wait()

    probabilities = {
        move: float(probability) / 100 for move, probability in POLICY_LINE.findall(output)
    }
    if not probabilities:
        raise RuntimeError("Maia Lc0 output did not include root policy probabilities")
    total = sum(probabilities.values())
    return {move: probability / total for move, probability in probabilities.items()}


async def stockfish_policy(engine: chess.engine.Protocol, fen: str) -> dict[str, float]:
    board = chess.Board(fen)
    results = await engine.analyse(
        board,
        chess.engine.Limit(time=0.1),
        multipv=board.legal_moves.count(),
    )
    infos = results if isinstance(results, list) else [results]
    scores = {
        info["pv"][0].uci(): info["score"].pov(board.turn).score(mate_score=100_000)
        for info in infos
        if info.get("pv") and info.get("score")
    }
    if not scores:
        raise RuntimeError("Stockfish did not return root move scores")
    highest_score = max(scores.values())
    weights = {
        move: math.exp((score - highest_score) / 100) for move, score in scores.items()
    }
    total = sum(weights.values())
    return {move: weight / total for move, weight in weights.items()}


def cross_entropy(human_move_counts: dict[str, int], policy: dict[str, float]) -> float:
    total_count = sum(human_move_counts.values())
    return -sum(
        (count / total_count) * math.log(max(policy.get(move, 0.0), 1e-12))
        for move, count in human_move_counts.items()
    )


@pytest.mark.real_model
@pytest.mark.asyncio
async def test_maia_policy_is_closer_to_human_reference_than_elo_matched_stockfish() -> None:
    maia_binary = required_environment("SCAN64_MAIA_BINARY")
    maia_weights = required_environment("SCAN64_MAIA_1500_WEIGHTS")
    stockfish_binary = required_environment("SCAN64_STOCKFISH_BINARY")
    fixture = json.loads(FIXTURE_PATH.read_text())

    _, stockfish = await chess.engine.popen_uci(stockfish_binary)
    try:
        await stockfish.configure({"Threads": 1, "UCI_LimitStrength": True, "UCI_Elo": 1500})
        maia_scores: list[float] = []
        stockfish_scores: list[float] = []
        for case in fixture["cases"]:
            human_move_counts = case["human_move_counts"]
            maia_policy_output = await maia_policy(maia_binary, maia_weights, case["fen"])
            maia_scores.append(cross_entropy(human_move_counts, maia_policy_output))
            stockfish_scores.append(
                cross_entropy(human_move_counts, await stockfish_policy(stockfish, case["fen"]))
            )
    finally:
        await stockfish.quit()

    maia_mean = sum(maia_scores) / len(maia_scores)
    stockfish_mean = sum(stockfish_scores) / len(stockfish_scores)
    assert maia_mean <= stockfish_mean - 0.15, (
        f"expected Maia cross-entropy to beat Elo-matched Stockfish by at least 0.15; "
        f"got Maia={maia_mean:.3f}, Stockfish={stockfish_mean:.3f}"
    )

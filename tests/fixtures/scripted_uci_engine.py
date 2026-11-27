#!/usr/bin/env python3
"""A small scripted UCI engine for tests/integration/test_second_engine.py.

It declares only ``Threads``, ``Hash`` and ``MultiPV``. It plays legal moves and
scores them with a two-ply material search, so every ``info`` line has depth,
seldepth, multipv, score, nodes and pv. It is not a chess engine for play: its
only job is to be a UCI engine that is not Stockfish.
"""

from __future__ import annotations

import sys

import chess

NAME = "Scan64 Scripted UCI Fixture"
VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 300,
    chess.BISHOP: 300,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}
MATE = 100_000


def _material(board: chess.Board) -> int:
    """Material from the side to move, in centipawns."""
    total = 0
    for piece in board.piece_map().values():
        value = VALUES[piece.piece_type]
        total += value if piece.color == board.turn else -value
    return total


def _reply(board: chess.Board) -> tuple[int, chess.Move | None, int]:
    """Best reply score from the side to move, its move, and the nodes searched."""
    if board.is_checkmate():
        return -MATE, None, 1
    if board.is_game_over():
        return 0, None, 1
    best, best_move, nodes = -MATE - 1, None, 0
    for move in board.legal_moves:
        board.push(move)
        nodes += 1
        score = MATE if board.is_checkmate() else -_material(board)
        board.pop()
        if score > best:
            best, best_move = score, move
    return best, best_move, nodes


def _lines(board: chess.Board, multipv: int) -> tuple[list[str], chess.Move | None]:
    scored: list[tuple[int, str, list[chess.Move], int]] = []
    for move in board.legal_moves:
        board.push(move)
        reply_score, reply, nodes = _reply(board)
        board.pop()
        pv = [move] if reply is None else [move, reply]
        scored.append((-reply_score, move.uci(), pv, nodes + 1))
    scored.sort(key=lambda item: (-item[0], item[1]))
    total_nodes = sum(item[3] for item in scored) or 1
    lines = []
    for index, (score, _, pv, _) in enumerate(scored[:multipv], start=1):
        if score >= MATE:
            value = "mate 1"
        elif score <= -MATE:
            value = "mate -1"
        else:
            value = f"cp {score}"
        moves = " ".join(move.uci() for move in pv)
        lines.append(
            f"info depth 2 seldepth {len(pv) + 1} multipv {index} score {value} "
            f"nodes {total_nodes} pv {moves}"
        )
    return lines, scored[0][2][0] if scored else None


def _position(tokens: list[str]) -> chess.Board:
    if tokens[1:2] == ["startpos"]:
        board, rest = chess.Board(), tokens[2:]
    else:
        end = tokens.index("moves") if "moves" in tokens else len(tokens)
        board, rest = chess.Board(" ".join(tokens[2:end])), tokens[end:]
    for move in rest[1:] if rest[:1] == ["moves"] else []:
        board.push_uci(move)
    return board


def main() -> None:
    board = chess.Board()
    multipv = 1
    for raw in sys.stdin:
        tokens = raw.split()
        if not tokens:
            continue
        command = tokens[0]
        out: list[str] = []
        if command == "uci":
            out = [
                f"id name {NAME}",
                "id author Scan64 tests",
                "option name Threads type spin default 1 min 1 max 1",
                "option name Hash type spin default 16 min 1 max 1024",
                "option name MultiPV type spin default 1 min 1 max 256",
                "uciok",
            ]
        elif command == "isready":
            out = ["readyok"]
        elif command == "setoption" and tokens[1:3] == ["name", "MultiPV"]:
            multipv = max(1, int(tokens[-1]))
        elif command == "ucinewgame":
            board = chess.Board()
        elif command == "position":
            board = _position(tokens)
        elif command == "go":
            out, best = _lines(board, multipv)
            out.append(f"bestmove {best.uci() if best else '(none)'}")
        elif command == "quit":
            return
        for line in out:
            sys.stdout.write(line + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

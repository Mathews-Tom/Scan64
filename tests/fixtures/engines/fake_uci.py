"""Minimal UCI engine for tests that declares no MultiPV, like Alexandria 9.1.0.

Declares only ``Hash`` and ``Threads``. Every ``go`` answers one ``info`` line
for the first legal move (score 0) and ``bestmove``. Logs every command to the
path in FAKE_UCI_LOG.
"""

import os
import sys

import chess

log_path = os.environ["FAKE_UCI_LOG"]
board = chess.Board()


def send(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


with open(log_path, "a", encoding="utf-8") as log:
    for raw in sys.stdin:
        line = raw.strip()
        log.write(line + "\n")
        log.flush()
        command, _, rest = line.partition(" ")
        if command == "uci":
            send("id name NoMultiPV 1")
            send("id author Scan64 tests")
            send("option name Hash type spin default 16 min 1 max 1024")
            send("option name Threads type spin default 1 min 1 max 64")
            send("uciok")
        elif command == "isready":
            send("readyok")
        elif command == "position":
            words = rest.split()
            if words[0] == "startpos":
                board = chess.Board()
                moves = words[2:] if len(words) > 1 else []
            else:
                moves_at = words.index("moves") if "moves" in words else len(words)
                board = chess.Board(" ".join(words[1:moves_at]))
                moves = words[moves_at + 1 :]
            for move in moves:
                board.push_uci(move)
        elif command == "go":
            move = next(iter(board.legal_moves)).uci()
            send(f"info depth 1 seldepth 1 nodes 1 score cp 0 pv {move}")
            send(f"bestmove {move}")
        elif command == "quit":
            break

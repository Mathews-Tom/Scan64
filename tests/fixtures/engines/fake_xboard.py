"""Minimal XBoard (protover 2) engine for tests: one legal move per analysis.

Logs every command it receives to the path in FAKE_XBOARD_LOG so a test can
assert what Scan64 sent. FAKE_XBOARD_SETBOARD=0 declares ``setboard=0``, as
Fairy-Max 5.0b does. Posts one line whose node count exceeds any search
budget, so python-chess stops the analysis after the first post.
"""

import os
import sys

import chess

log_path = os.environ["FAKE_XBOARD_LOG"]
setboard = os.environ.get("FAKE_XBOARD_SETBOARD", "1")
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
        if command == "protover":
            send(
                f"feature ping=1 setboard={setboard} sigint=0 "
                'myname="FakeXBoard 1" memory=1 smp=1 done=1'
            )
        elif command == "ping":
            send(f"pong {rest}")
        elif command == "new":
            board = chess.Board()
        elif command == "setboard":
            board = chess.Board(rest)
        elif command == "analyze":
            move = next(iter(board.legal_moves))
            # depth, score (cp, side to move), time (cs), nodes, pv
            send(f"5 37 1 1000000000 {move.uci()}")
        elif command == "quit":
            break

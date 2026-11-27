"""Minimal UCI engine for tests that declares no MultiPV, like Alexandria 9.1.0.

Declares only ``Hash`` and ``Threads``. Every ``go`` answers one ``info`` line
for the first legal move (score 0) and ``bestmove``. Logs every command to the
path in FAKE_UCI_LOG.

With FAKE_UCI_VARIANTS (comma-separated, ``chess`` first) it also declares
``UCI_Variant`` as a combo of those names, like Fairy-Stockfish, and plays each
position under the variant last set. With FAKE_UCI_MULTIPV set it declares
``MultiPV`` and answers one line per legal move up to the value last set.
"""

import os
import sys

import chess
import chess.variant

log_path = os.environ["FAKE_UCI_LOG"]
board = chess.Board()
variants = [name for name in os.environ.get("FAKE_UCI_VARIANTS", "").split(",") if name]
variant = chess.Board
multipv_declared = bool(os.environ.get("FAKE_UCI_MULTIPV"))
multipv = 1


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
            if multipv_declared:
                send("option name MultiPV type spin default 1 min 1 max 500")
            if variants:
                choices = " ".join(f"var {name}" for name in variants)
                send(f"option name UCI_Variant type combo default {variants[0]} {choices}")
            send("uciok")
        elif command == "setoption" and rest.startswith("name UCI_Variant value "):
            variant = chess.variant.find_variant(rest.rsplit(" ", 1)[1])
        elif command == "setoption" and rest.startswith("name MultiPV value "):
            multipv = int(rest.rsplit(" ", 1)[1])
        elif command == "isready":
            send("readyok")
        elif command == "position":
            words = rest.split()
            if words[0] == "startpos":
                board = variant()
                moves = words[2:] if len(words) > 1 else []
            else:
                moves_at = words.index("moves") if "moves" in words else len(words)
                board = variant(" ".join(words[1:moves_at]))
                moves = words[moves_at + 1 :]
            for move in moves:
                board.push_uci(move)
        elif command == "go":
            moves = [move.uci() for move in board.legal_moves][:multipv]
            for rank, move in enumerate(moves, start=1):
                prefix = f"info depth 1 seldepth 1 multipv {rank}" if multipv_declared else "info depth 1 seldepth 1"
                send(f"{prefix} nodes 1 score cp 0 pv {move}")
            send(f"bestmove {moves[0]}")
        elif command == "quit":
            break

"""Scripted UCI engine for the variant probe tests.

``FAKE_VARIANTS`` selects what it declares: unset or empty, no ``UCI_Variant``
option; otherwise a comma-separated list of ``UCI_Variant`` values, the first
being the default. It also declares ``MultiPV``, ``Threads`` and ``Hash``.
Each ``go`` sends one ``info`` line per MultiPV index for the index-th legal
move in UCI order, followed by a legal reply, under the selected variant's
python-chess rules. With
``FAKE_VARIANT_ILLEGAL=1`` line 2 repeats its first move instead, which the
side to move then cannot play.
"""

import os
import sys

import chess
import chess.variant

declared = [name for name in os.environ.get("FAKE_VARIANTS", "").split(",") if name]
illegal = os.environ.get("FAKE_VARIANT_ILLEGAL") == "1"
variant = declared[0] if declared else "chess"
multipv = 1
board: chess.Board = chess.Board()


def send(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    command, _, rest = raw.strip().partition(" ")
    if command == "uci":
        send("id name FakeVariant 1")
        send("id author Scan64 tests")
        send("option name Threads type spin default 1 min 1 max 64")
        send("option name Hash type spin default 16 min 1 max 1024")
        send("option name MultiPV type spin default 1 min 1 max 500")
        if declared:
            values = " var ".join(declared)
            send(f"option name UCI_Variant type combo default {declared[0]} var {values}")
        send("uciok")
    elif command == "isready":
        send("readyok")
    elif command == "setoption":
        name, _, value = rest.removeprefix("name ").partition(" value ")
        if name == "UCI_Variant":
            variant = value
        elif name == "MultiPV":
            multipv = int(value)
    elif command == "position":
        board_class = chess.variant.find_variant(variant)
        words = rest.split()
        moves_at = words.index("moves") if "moves" in words else len(words)
        fen = board_class.starting_fen if words[0] == "startpos" else " ".join(words[1:moves_at])
        board = board_class(fen)
        for move in words[moves_at + 1 :]:
            board.push_uci(move)
    elif command == "go":
        moves = sorted(move.uci() for move in board.legal_moves)
        for index, first in enumerate(moves[:multipv], start=1):
            after = board.copy(stack=False)
            after.push_uci(first)
            replies = sorted(move.uci() for move in after.legal_moves)
            second = first if illegal and index == 2 else (replies[0] if replies else None)
            pv = first if second is None else f"{first} {second}"
            send(f"info depth 2 seldepth 2 multipv {index} score cp {10 - index} nodes 40 pv {pv}")
        send(f"bestmove {moves[0]}")
    elif command == "quit":
        break

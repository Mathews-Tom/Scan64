"""Build the fixed position set for the factor-module parity tests.

Run from the repository root:

    uv run python tests/helpers/factors/build_positions.py

The output, tests/fixtures/factors/positions.json, is deterministic: the
sources are repository fixtures and a fixed list of hand-made positions, and
the samples use a seeded generator. Every FEN is normalised by python-chess, so
the Node harness and the Python port read the same castling and en-passant
fields.
"""

from __future__ import annotations

import csv
import io
import json
import random
import re
from pathlib import Path

import chess
import chess.pgn

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "tests" / "fixtures" / "factors" / "positions.json"
SEED = 407

FEN_RE = re.compile(
    r"([pnbrqkPNBRQK1-8]{1,8}(?:/[pnbrqkPNBRQK1-8]{1,8}){7} [wb] "
    r"[KQkq-]+ (?:[a-h][36]|-)(?: \d+ \d+)?)"
)

# Hand-made positions: scale-factor branches, pins and x-rays, edge passers
# (the guide reads attacks on squares off the a- and h-files), castling rights
# and pawnless or queenless endings.
CURATED: tuple[tuple[str, str], ...] = (
    ("start", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"),
    ("krpkr_one_flank", "8/8/4k3/8/5P2/4K3/6r1/5R2 w - - 0 1"),
    ("krpkr_king_shelters", "8/5k2/8/5P2/6K1/8/r7/5R2 b - - 3 50"),
    ("krkr_pawns_both_flanks", "8/p4k2/8/6p1/P7/6P1/r4K2/4R3 w - - 0 40"),
    ("opposite_bishops_pawns", "8/5k2/3b4/1p3p2/1P3P2/4B3/5K2/8 w - - 0 45"),
    ("opposite_bishops_rooks", "4r1k1/5ppp/3b4/8/8/4B3/5PPP/4R1K1 w - - 0 30"),
    ("queen_vs_minors", "4k3/8/3nb3/8/8/8/3Q4/4K3 w - - 0 60"),
    ("queen_vs_rook", "8/8/4k3/8/3r4/8/4Q3/4K3 b - - 0 70"),
    ("pawnless_rook_vs_bishop", "8/8/3bk3/8/8/4K3/8/5R2 w - - 0 80"),
    ("bare_minor_vs_pawns", "8/8/4k3/2p5/2P5/3N4/4K3/8 w - - 0 50"),
    ("kpk", "8/8/8/4k3/8/4P3/4K3/8 w - - 0 1"),
    ("passer_a_file", "6k1/8/P7/8/8/8/5PPP/6K1 w - - 0 40"),
    ("passer_h_file_black", "6k1/5ppp/8/8/8/7p/8/1R4K1 b - - 0 40"),
    ("passers_edges_rooks", "r5k1/P7/8/8/8/8/7p/R5K1 w - - 0 50"),
    ("pin_bishop", "4k3/8/8/1b6/8/3N4/8/5K2 w - - 0 1"),
    ("pin_rook_xray", "4r1k1/8/8/8/4R3/8/4R3/4K3 w - - 0 1"),
    ("pin_queen_line", "3qk3/8/8/8/3Q4/8/8/3K4 w - - 0 1"),
    ("queens_battery", "k7/8/8/3q4/8/1Q6/Q7/7K w - - 0 1"),
    (
        "castling_rights_only_white",
        "r3k2r/pppq1ppp/2npbn2/4p3/4P3/2NPBN2/PPPQ1PPP/R3K2R w KQ - 4 9",
    ),
    (
        "castling_rights_only_black",
        "r3k2r/pppq1ppp/2npbn2/4p3/4P3/2NPBN2/PPPQ1PPP/R3K2R b kq - 4 9",
    ),
    ("trapped_rook", "rnbq1bnr/ppppkppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQ - 2 3"),
    ("trapped_rook_no_castling", "6k1/5ppp/8/8/8/8/5PPP/5KBR w - - 0 30"),
    ("outposts", "r1bq1rk1/pp3ppp/2n5/3pN3/3P4/2PB4/PP3PPP/R2Q1RK1 w - - 0 14"),
    ("weak_queen", "4k3/8/8/1b6/8/3Q4/8/4K3 w - - 0 1"),
    ("king_attack", "r1bq1rk1/pppp1ppp/2n2n2/2b1p1N1/2B1P3/8/PPPP1PPP/RNBQK2R w KQ - 6 5"),
    ("storm", "r1bq1rk1/pp2ppbp/2np1np1/8/3NP1PP/2N1BP2/PPPQ4/R3KB1R b KQ - 0 10"),
    ("blocked_center", "r1bqk2r/pp1nbppp/2p1p3/3pP3/3P4/2PB1N2/PP3PPP/RNBQK2R w KQkq - 1 8"),
    ("rule50_high", "8/8/4k3/8/2R5/8/4K3/2r5 w - - 87 120"),
    ("many_queens", "1Q4k1/8/8/8/8/8/1q6/1Q4K1 w - - 0 1"),
    ("doubled_isolated", "4k3/8/8/3p4/8/3P4/3P4/4K3 w - - 0 1"),
    ("connected_passers", "6k1/8/8/2PP4/8/8/5ppp/6K1 w - - 0 1"),
    ("pawns_only_both_flanks", "8/pp3kpp/8/8/8/8/PP3KPP/8 w - - 0 1"),
    ("weak_lever", "4k3/8/8/3p1p2/4P3/8/8/4K3 w - - 0 1"),
    ("weak_lever_black", "4k3/8/8/4p3/3P1P2/8/8/4K3 b - - 0 1"),
    ("doubled_isolated_white", "4k3/8/4p3/8/8/4P3/4P3/4K3 w - - 0 1"),
    ("doubled_isolated_black", "4k3/3p4/3p4/8/8/3P4/8/4K3 b - - 0 1"),
)


def normalise(fen: str) -> str:
    return chess.Board(fen).fen()


def usable(board: chess.Board) -> bool:
    """The guide assumes one king per side and no pawn on a back rank."""
    if len(board.pieces(chess.KING, chess.WHITE)) != 1:
        return False
    if len(board.pieces(chess.KING, chess.BLACK)) != 1:
        return False
    back = chess.BB_RANK_1 | chess.BB_RANK_8
    return not board.pawns & back


def benchmark_positions() -> list[dict[str, str]]:
    data = json.loads((ROOT / "benchmarks/narrator/positions.json").read_text())
    out: list[dict[str, str]] = []
    for i, entry in enumerate(data["positions"]):
        board = chess.Board(entry["fen"])
        out.append(
            {
                "id": f"bench-{i:02d}-before",
                "fen": board.fen(),
                "source": "benchmarks/narrator/positions.json",
            }
        )
        board.push_san(entry["played_move"])
        out.append(
            {
                "id": f"bench-{i:02d}-after",
                "fen": board.fen(),
                "source": "benchmarks/narrator/positions.json",
            }
        )
    return out


def fixture_positions(rng: random.Random) -> list[dict[str, str]]:
    """Sample FENs found in tests/fixtures (up to 12 per file)."""
    out: list[dict[str, str]] = []
    for path in sorted((ROOT / "tests" / "fixtures").rglob("*")):
        if not path.is_file() or path.suffix not in (".json", ".csv"):
            continue
        if "factors" in path.relative_to(ROOT).parts:
            continue
        text = path.read_text(errors="ignore")
        if path.suffix == ".csv":
            rows = list(csv.DictReader(io.StringIO(text)))
            fens = sorted({row["FEN"] for row in rows if row.get("FEN")})
        else:
            fens = sorted(set(FEN_RE.findall(text)))
        picked = rng.sample(fens, min(12, len(fens)))
        rel = str(path.relative_to(ROOT))
        for j, fen in enumerate(picked):
            out.append({"id": f"fixture-{path.stem}-{j:02d}", "fen": fen, "source": rel})
    return out


def pgn_positions(rng: random.Random) -> list[dict[str, str]]:
    """Openings, middle games and endgames from the recorded real games."""
    out: list[dict[str, str]] = []
    games: list[chess.pgn.Game] = []
    for path in sorted((ROOT / "tests" / "fixtures" / "m48").glob("*real_games.pgn")):
        with path.open() as handle:
            while (game := chess.pgn.read_game(handle)) is not None:
                games.append(game)
    rng.shuffle(games)
    phases = {"opening": 0, "middlegame": 0, "endgame": 0}
    for game in games:
        boards: list[chess.Board] = []
        board = game.board()
        for move in game.mainline_moves():
            board.push(move)
            boards.append(board.copy(stack=False))
        if len(boards) < 20:
            continue
        candidates = {
            "opening": boards[4:20],
            "middlegame": boards[20:60],
            "endgame": [b for b in boards if len(b.piece_map()) <= 12],
        }
        for phase, pool in candidates.items():
            if pool and phases[phase] < 30:
                b = rng.choice(pool)
                out.append(
                    {
                        "id": f"pgn-{phase}-{phases[phase]:02d}",
                        "fen": b.fen(),
                        "source": "tests/fixtures/m48 real games",
                    }
                )
                phases[phase] += 1
        if all(n >= 30 for n in phases.values()):
            break
    return out


def main() -> None:
    rng = random.Random(SEED)
    raw: list[dict[str, str]] = [
        {"id": f"curated-{name}", "fen": fen, "source": "curated"} for name, fen in CURATED
    ]
    raw += benchmark_positions()
    raw += fixture_positions(rng)
    raw += pgn_positions(rng)
    seen: set[str] = set()
    positions: list[dict[str, str]] = []
    for entry in raw:
        board = chess.Board(entry["fen"])
        fen = normalise(entry["fen"])
        if fen in seen or not usable(board):
            continue
        seen.add(fen)
        positions.append({**entry, "fen": fen})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"seed": SEED, "positions": positions}, indent=1) + "\n")
    print(f"{len(positions)} positions -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

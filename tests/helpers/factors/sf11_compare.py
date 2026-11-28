"""Compare the factor module's group totals with the Stockfish 11 ``eval`` trace.

Two steps, both run from the repository root:

    # 1. Record the Stockfish 11 traces (development machine only).
    uv run python tests/helpers/factors/sf11_compare.py collect \\
        ~/.local/opt/engines/stockfish11/stockfish11
    # 2. Rebuild the comparison fixture and the report from the recorded traces.
    uv run python tests/helpers/factors/sf11_compare.py report

Step 1 writes tests/fixtures/factors/sf11_trace.json. Step 2 needs no engine:
it writes sf11_comparison.json and sf11_comparison.md next to it. The unit
tests rebuild the comparison in memory and check it against the fixture.

Units: both sides compare in internal units. A Stockfish 11 trace value is in
pawns of PawnValueEg = 213, so ``units = round(pawns * 213)``. Guide values
are in its own internal units (PawnValueEg = 206). The values compared are
the net (White minus Black) middle-game and end-game values of each group.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import chess

from scan64.explanations.factors import evaluate_factors, terms
from scan64.explanations.factors.board import GuidePosition, positions_from_board

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "factors"
POSITIONS = FIXTURES / "positions.json"
TRACE = FIXTURES / "sf11_trace.json"
COMPARISON = FIXTURES / "sf11_comparison.json"
REPORT = FIXTURES / "sf11_comparison.md"

SF11_PAWN_EG = 213
# Tolerance per group and phase, in internal units (about 0.25 pawn).
TOLERANCE = 50

ROW_RE = re.compile(
    r"^\s*([A-Za-z ]+?) \|\s*(\S+)\s+(\S+) \|\s*(\S+)\s+(\S+) \|\s*(\S+)\s+(\S+)\s*$"
)
TOTAL_RE = re.compile(r"Total evaluation: (\S+)")

# Guide group -> Stockfish 11 trace rows.
GROUP_ROWS: dict[str, tuple[str, ...]] = {
    "Material": ("Material",),
    "Imbalance": ("Imbalance",),
    "Pawns": ("Pawns",),
    "Pieces": ("Knights", "Bishops", "Rooks", "Queens"),
    "Mobility": ("Mobility",),
    "Threats": ("Threats",),
    "Passed pawns": ("Passed",),
    "Space": ("Space",),
    "King": ("King safety",),
    "Winnable": ("Initiative",),
}

# Why the guide (Stockfish 12-era classical evaluation) and Stockfish 11 differ.
# Each text was checked against Stockfish 11 (commit c3483fa: src/evaluate.cpp,
# pawns.cpp, psqt.cpp, material.cpp) and the guide's term code. The classifier
# in ``_causes`` assigns one or more of these codes to every row over tolerance.
CAUSES: dict[str, str] = {
    "material_pawn_value": (
        "Pawn value is S(128, 213) in SF11 and S(124, 206) in the guide; some pawn "
        "piece-square entries changed. Piece values, piece PSQT and imbalance tables are equal."
    ),
    "pawns_new_terms": (
        "The guide adds Doubled isolated S(11, 56) and Blocked pawns on ranks 5-6; "
        "the other pawn weights are equal to SF11."
    ),
    "pieces_rules": (
        "Piece terms differ: knight outpost S(56, 36) vs 2 x S(30, 21), reachable outpost "
        "S(31, 22) vs S(32, 10), new edge-knight outpost S(-7, 36), King protector "
        "S(8 or 6, 9) vs S(7, 8), Bishop pawns scale (blocked + undefended) vs (1 + blocked), "
        "Rook on file S(19, 7)/S(48, 29) vs S(21, 4)/S(47, 25), Rook on queen file S(6, 11) vs "
        "S(7, 6), Trapped rook S(55, 13) vs S(52, 10), Weak queen S(56, 15) vs S(49, 15); "
        "Bishop xray pawns, Rook/Bishop on king ring and Queen infiltration exist only in "
        "the guide."
    ),
    "mobility_tables": (
        "Mobility bonus tables differ for knights (end game), rooks and queens; the guide also "
        "excludes squares of the own queen from knight and bishop mobility."
    ),
    "threat_tables": (
        "Threat by minor and by rook tables were retuned (for example rook on queen S(58, 41) vs "
        "S(51, 38)); Weak queen protection S(14, 0) exists only in the guide."
    ),
    "threat_queen_imbalance": (
        "The guide doubles Slider on queen and Knight on queen when the attacking side has no "
        "queen; SF11 counts them once."
    ),
    "passed_rules": (
        "Passed pawns: the guide scores candidate and leverable passers with its own block and "
        "king-proximity rules; PassedRank and PassedFile are equal to SF11."
    ),
    "space_weight": (
        "Space weight is (own pieces - 1)^2 / 16 in SF11 and "
        "(own pieces - 3 + min(blocked pawns, 9))^2 / 16 in the guide."
    ),
    "king_safe_check_count": (
        "SF11 adds a fixed weight per check type that has a safe check (Queen 780, Rook 1080, "
        "Bishop 635, Knight 790); the guide multiplies its weight by the number of safe checks, "
        "capped at 1.45-1.75. King danger enters as danger^2 / 4096, so the gap grows quickly."
    ),
    "king_no_own_pawns": (
        "King pawn distance: with no own pawns SF11 uses distance 0 and the guide 6 "
        "(16 units per step in the end game); the guide also caps the distance at 6."
    ),
    "king_blocked_storm": (
        "Blocked storm: SF11 subtracts S(82, 82) only for an enemy pawn on the third rank; the "
        "guide uses per-rank tables (end game 78, 15, 10, 6, 2 for ranks 3-7)."
    ),
    "king_shelter_rules": (
        "Shelter rules: the guide ignores own shelter pawns attacked by an enemy pawn and uses "
        "a per-rank blocked-storm table; SF11 counts every own pawn and subtracts S(82, 82) only "
        "for a blocked pawn on the third rank. The shelter enters the score and, as "
        "6 x shelter / 8, the king danger."
    ),
    "king_danger_mobility": (
        "The mobility difference (Mobility row) enters king danger with weight 1, and "
        "danger^2 / 4096 amplifies it."
    ),
    "winnable_weights": (
        "SF11 Initiative vs guide Winnable: pawns 11 vs 12, infiltration 12 vs 24, constant "
        "-100 vs -110, and SF11's almost-unwinnable test also needs no passed pawns."
    ),
    "winnable_sign": (
        "The correction takes the sign of the score before it; that score has the opposite "
        "sign in the two engines, so the corrections point in opposite directions."
    ),
}

GROUP_DEFAULT: dict[str, str] = {
    "Material": "material_pawn_value",
    "Imbalance": "material_pawn_value",
    "Pawns": "pawns_new_terms",
    "Pieces": "pieces_rules",
    "Mobility": "mobility_tables",
    "Threats": "threat_tables",
    "Passed pawns": "passed_rules",
    "Space": "space_weight",
    "King": "king_shelter_rules",
    "Winnable": "winnable_weights",
}


_SF11_STRENGTH = (
    (-6, 81, 93, 58, 39, 18, 25, 0),
    (-43, 61, 35, -49, -29, -11, -63, 0),
    (-10, 75, 23, -2, 32, 3, -45, 0),
    (-39, -13, -29, -52, -48, -67, -166, 0),
)
_SF11_STORM = (
    (85, -289, -166, 97, 50, 45, 50, 0),
    (46, -25, 122, 45, 37, -10, 20, 0),
    (-6, 51, 168, 34, -2, -22, -14, 0),
    (-15, -11, 101, 4, 11, -15, -29, 0),
)


def sf11_shelter(board: chess.Board, color: chess.Color) -> tuple[int, int]:
    """Stockfish 11 ``Pawns::Entry::evaluate_shelter`` with the castling choice (mg, eg)."""

    def rel(sq: int) -> int:
        r = chess.square_rank(sq)
        return r if color == chess.WHITE else 7 - r

    def shelter_at(ksq: int) -> tuple[int, int]:
        krank = rel(ksq)
        mg = eg = 5
        center = min(max(chess.square_file(ksq), 1), 6)
        for f in range(center - 1, center + 2):
            ours = [
                rel(sq)
                for sq in board.pieces(chess.PAWN, color)
                if chess.square_file(sq) == f and rel(sq) >= krank
            ]
            theirs = [
                rel(sq)
                for sq in board.pieces(chess.PAWN, not color)
                if chess.square_file(sq) == f and rel(sq) >= krank
            ]
            our_rank = min(ours) if ours else 0
            their_rank = min(theirs) if theirs else 0
            d = min(f, 7 - f)
            mg += _SF11_STRENGTH[d][our_rank]
            if our_rank and our_rank == their_rank - 1:
                if their_rank == 2:
                    mg -= 82
                    eg -= 82
            else:
                mg -= _SF11_STORM[d][their_rank]
        return mg, eg

    king = board.king(color)
    assert king is not None
    best = shelter_at(king)
    back = 0 if color == chess.WHITE else 7
    if board.has_kingside_castling_rights(color):
        best = max(best, shelter_at(chess.square(6, back)), key=lambda v: v[0])
    if board.has_queenside_castling_rights(color):
        best = max(best, shelter_at(chess.square(2, back)), key=lambda v: v[0])
    return best


def guide_shelter(pos: GuidePosition) -> tuple[int, int]:
    """The guide's shelter of the enemy king in ``pos``, as the defender's (mg, eg) score."""
    w, s, e = terms._shelter(pos)
    return w - s, 10 - e


def parse_trace(text: str) -> dict[str, Any]:
    rows: dict[str, list[float | None]] = {}
    for line in text.splitlines():
        m = ROW_RE.match(line)
        if m and m.group(1) not in ("Term",):
            rows[m.group(1).strip()] = [None if v == "----" else float(v) for v in m.groups()[1:]]
    total = TOTAL_RE.search(text)
    return {"rows": rows, "total": total.group(1) if total else None}


def collect(binary: Path) -> None:
    positions = json.loads(POSITIONS.read_text())["positions"]
    commands = "".join(f"position fen {p['fen']}\neval\n" for p in positions) + "quit\n"
    out = subprocess.run(
        [str(binary)], input=commands, capture_output=True, text=True, check=True
    ).stdout
    chunks = out.split("Total evaluation:")
    traces: list[dict[str, Any]] = []
    for p, chunk, tail in zip(positions, chunks[:-1], chunks[1:], strict=True):
        parsed = parse_trace(chunk + "Total evaluation:" + tail.splitlines()[0])
        traces.append({"id": p["id"], "fen": p["fen"], **parsed})
    header = out.splitlines()[0]
    TRACE.write_text(json.dumps({"engine": header, "traces": traces}, indent=1) + "\n")
    print(f"{len(traces)} traces -> {TRACE.relative_to(ROOT)}")


def _sf11_units(rows: dict[str, list[float | None]], names: tuple[str, ...]) -> tuple[int, int]:
    mg = sum(rows[n][4] or 0.0 for n in names)
    eg = sum(rows[n][5] or 0.0 for n in names)
    return round(mg * SF11_PAWN_EG), round(eg * SF11_PAWN_EG)


def _status(trace: dict[str, Any]) -> str:
    """'ok', 'in_check', 'lazy' or 'specialized' (Stockfish 11 skipped its trace)."""
    if trace["total"] == "none":
        return "in_check"
    rows = trace["rows"]
    if all((v or 0.0) == 0.0 for row in rows.values() for v in row):
        board = chess.Board(trace["fen"])
        npm = sum(
            len(board.pieces(pt, c)) * v
            for pt, v in (
                (chess.KNIGHT, 781),
                (chess.BISHOP, 825),
                (chess.ROOK, 1276),
                (chess.QUEEN, 2538),
            )
            for c in chess.COLORS
        )
        if abs(float(trace["total"])) * SF11_PAWN_EG > 1400 + npm / 64:
            return "lazy"
        return "specialized"
    return "ok"


STATUS_TEXT = {
    "in_check": "Stockfish 11 does not evaluate a position in check (trace: 'none (in check)').",
    "lazy": (
        "Stockfish 11 lazy evaluation: material + imbalance + pawns exceed 1400 + npm/64, "
        "so the trace stays empty."
    ),
    "specialized": (
        "Stockfish 11 uses a specialized endgame function for this material, "
        "so the trace stays empty."
    ),
}


def build() -> dict[str, Any]:
    traces = json.loads(TRACE.read_text())["traces"]
    rows_out: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for trace in traces:
        status = _status(trace)
        if status != "ok":
            skipped.append({"id": trace["id"], "fen": trace["fen"], "status": status})
            continue
        report = evaluate_factors(trace["fen"])
        for group, names in GROUP_ROWS.items():
            smg, seg = _sf11_units(trace["rows"], names)
            g = report.groups[group]
            rows_out.append(
                {
                    "id": trace["id"],
                    "group": group,
                    "guide_mg": g.net_mg,
                    "guide_eg": g.net_eg,
                    "sf11_mg": smg,
                    "sf11_eg": seg,
                    "diff_mg": g.net_mg - smg,
                    "diff_eg": g.net_eg - seg,
                }
            )
    by_id = {trace["id"]: trace for trace in traces}
    over = []
    for r in rows_out:
        if abs(r["diff_mg"]) > TOLERANCE or abs(r["diff_eg"]) > TOLERANCE:
            causes, evidence = _causes(r, by_id[r["id"]])
            over.append({**r, "causes": causes, "evidence": evidence})
    return {
        "tolerance_units": TOLERANCE,
        "sf11_pawn_value_eg": SF11_PAWN_EG,
        "compared_positions": len({r["id"] for r in rows_out}),
        "skipped": skipped,
        "rows": rows_out,
        "over_tolerance": over,
    }


def _causes(row: dict[str, Any], trace: dict[str, Any]) -> tuple[list[str], str]:
    """Cause codes for one row over tolerance, with the evidence used."""
    board = chess.Board(trace["fen"])
    white, black = positions_from_board(board)
    group = row["group"]
    causes: list[str] = []
    evidence: list[str] = []
    if group == "King":
        mg_over = abs(row["diff_mg"]) > TOLERANCE
        eg_over = abs(row["diff_eg"]) > TOLERANCE
        for side, pos in (("White", white), ("Black", black)):
            counts = [terms._safe_checks(pos, kind) for kind in range(4)]
            if max(counts) >= 2:
                causes.append("king_safe_check_count")
                evidence.append(f"{side} safe checks N/B/R/Q {counts}")
        no_pawns = [c for c in chess.COLORS if not board.pieces(chess.PAWN, c)]
        if eg_over and len(no_pawns) == 1:
            causes.append("king_no_own_pawns")
            evidence.append(f"{'White' if no_pawns[0] == chess.WHITE else 'Black'} has no pawns")
        # The king in ``black`` (the flipped view) is White's king, and vice versa.
        for name, color, pos in (("White", chess.WHITE, black), ("Black", chess.BLACK, white)):
            g_mg, g_eg = guide_shelter(pos)
            s_mg, s_eg = sf11_shelter(board, color)
            if mg_over and g_mg != s_mg:
                causes.append("king_shelter_rules")
                evidence.append(f"{name} king shelter MG guide {g_mg}, SF11 {s_mg}")
            if eg_over and g_eg != s_eg:
                causes.append("king_blocked_storm")
                evidence.append(f"{name} king shelter EG guide {g_eg}, SF11 {s_eg}")
        rows = trace["rows"]
        guide_mob = terms.mobility_mg(white) - terms.mobility_mg(black)
        sf_mob = round(((rows["Mobility"][0] or 0.0) - (rows["Mobility"][2] or 0.0)) * SF11_PAWN_EG)
        danger = max(terms.king_danger(white), terms.king_danger(black))
        if mg_over and danger > 100 and abs(guide_mob - sf_mob) > 2:
            causes.append("king_danger_mobility")
            evidence.append(
                f"mobility MG guide {guide_mob}, SF11 {sf_mob}; king danger up to {danger}"
            )
    elif group == "Winnable":
        rows = trace["rows"]
        sf_before = (
            round(((rows["Total"][4] or 0.0) - (rows["Initiative"][4] or 0.0)) * SF11_PAWN_EG),
            round(((rows["Total"][5] or 0.0) - (rows["Initiative"][5] or 0.0)) * SF11_PAWN_EG),
        )
        guide_before = (
            terms.middle_game_evaluation(white, True),
            terms.end_game_evaluation(white, True),
        )
        for k, phase in enumerate(("MG", "EG")):
            diff = row["diff_mg"] if k == 0 else row["diff_eg"]
            if abs(diff) > TOLERANCE and terms._sign(guide_before[k]) != terms._sign(sf_before[k]):
                causes.append("winnable_sign")
                evidence.append(
                    f"{phase} before correction: guide {guide_before[k]}, SF11 {sf_before[k]}"
                )
        causes.append("winnable_weights")
        evidence.append(f"guide Winnable {terms.winnable(white)}")
    elif group == "Threats":
        for side, pos, other in (("White", white, black), ("Black", black, white)):
            doubled = terms.queen_count(pos) == 0 and terms.queen_count(other) == 1
            hits = terms.slider_on_queen(pos) + terms.knight_on_queen(pos)
            if doubled and hits:
                causes.append("threat_queen_imbalance")
                evidence.append(f"{side} has no queen; slider/knight on queen {hits}")
        if not causes:
            causes.append("threat_tables")
    else:
        causes.append(GROUP_DEFAULT[group])
    if group in ("Pieces", "Threats"):
        report = evaluate_factors(board)
        evidence.append(
            ", ".join(
                f"{c.name} {c.net_mg}/{c.net_eg}"
                for c in report.contributions
                if c.group == group and (c.net_mg or c.net_eg)
            )
        )
    if group == "Space":
        evidence.append(
            f"own pieces White {board.occupied_co[chess.WHITE].bit_count()}, "
            f"Black {board.occupied_co[chess.BLACK].bit_count()}"
        )
    return list(dict.fromkeys(causes)), "; ".join(evidence)


def _summary(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for group in GROUP_ROWS:
        rows = [r for r in data["rows"] if r["group"] == group]
        over = [r for r in data["over_tolerance"] if r["group"] == group]
        mg = sorted(abs(r["diff_mg"]) for r in rows)
        eg = sorted(abs(r["diff_eg"]) for r in rows)
        out.append(
            {
                "group": group,
                "rows": len(rows),
                "over": len(over),
                "median_mg": mg[len(mg) // 2],
                "median_eg": eg[len(eg) // 2],
                "max_mg": mg[-1],
                "max_eg": eg[-1],
            }
        )
    return out


def render(data: dict[str, Any]) -> str:
    lines = [
        "# Factor module vs Stockfish 11 `eval` trace",
        "",
        "Generated by `tests/helpers/factors/sf11_compare.py report`; do not edit by hand.",
        "",
        "The guide documents the Stockfish 12-era classical evaluation, so it differs from",
        "Stockfish 11 by design. Values are net (White minus Black) internal units:",
        f"Stockfish 11 trace pawns x {SF11_PAWN_EG}, guide units as computed.",
        f"Tolerance: {data['tolerance_units']} units per group and phase (about 0.25 pawn).",
        "",
        f"Positions compared: {data['compared_positions']}; skipped: {len(data['skipped'])}.",
        "",
        "## Summary per group (absolute differences, units)",
        "",
        "| Group | Rows | Over tolerance | Median MG | Median EG | Max MG | Max EG |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in _summary(data):
        lines.append(
            f"| {s['group']} | {s['rows']} | {s['over']} | {s['median_mg']} | {s['median_eg']} "
            f"| {s['max_mg']} | {s['max_eg']} |"
        )
    lines += ["", "## Causes", "", "| Code | Cause |", "|---|---|"]
    lines += [f"| `{code}` | {text} |" for code, text in CAUSES.items()]
    lines += ["", "## Skipped positions", "", "| Position | Reason |", "|---|---|"]
    lines += [f"| {s['id']} | {STATUS_TEXT[s['status']]} |" for s in data["skipped"]]
    lines += [
        "",
        "## Every difference above the tolerance",
        "",
        "| Position | Group | Guide MG | SF11 MG | Diff MG | Guide EG | SF11 EG | Diff EG "
        "| Causes | Evidence |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['id']} | {r['group']} | {r['guide_mg']} | {r['sf11_mg']} | {r['diff_mg']} "
        f"| {r['guide_eg']} | {r['sf11_eg']} | {r['diff_eg']} "
        f"| {', '.join(f'`{c}`' for c in r['causes'])} | {r['evidence']} |"
        for r in data["over_tolerance"]
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("binary", type=Path)
    sub.add_parser("report")
    args = parser.parse_args()
    if args.cmd == "collect":
        collect(args.binary.expanduser())
    else:
        data = build()
        COMPARISON.write_text(json.dumps(data, indent=1) + "\n")
        REPORT.write_text(render(data))
        print(f"{len(data['over_tolerance'])} rows over tolerance -> {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

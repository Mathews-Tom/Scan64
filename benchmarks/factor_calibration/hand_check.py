"""Print the 20 hand-check positions of docs/evaluation/factor-calibration.md (#410).

Usage:
    uv run python -m benchmarks.factor_calibration.hand_check

Selection: from the ``benchmark`` and the ``slow`` set, the positions with at
least one fact under ``FACTOR_CONFIG``, in position-id order; then the positions
at index ``int(i * n / 10)`` for i = 0..9. For each position the script prints
the measured lines in SAN, the board at each point, every fact, and the term's
middle-game value per side (learner L, opponent O) at the three points.
"""

from __future__ import annotations

import chess

from benchmarks.factor_calibration.calibrate import Measurement, _samples, fired, measure
from scan64.explanations.factor_facts import FACTOR_CONFIG
from scan64.explanations.factors import evaluate_factors

PER_SET = 10


def picked() -> list[tuple[Measurement, dict[str, object]]]:
    chosen = []
    for name in ("benchmark", "slow"):
        found = []
        for sample in _samples(name):
            measurement = measure(name, sample)
            if measurement is not None and fired(measurement, FACTOR_CONFIG):
                found.append((measurement, sample))
        found.sort(key=lambda item: item[0].position_id)
        chosen += [found[int(i * len(found) / PER_SET)] for i in range(PER_SET)]
    return chosen


def _board(fen: str, uci: tuple[str, ...]) -> chess.Board:
    board = chess.Board(fen)
    for token in uci:
        board.push(chess.Move.from_uci(token))
    return board


def main() -> None:
    for measurement, sample in picked():
        fen = str(sample["fen"])
        learner = chess.Board(fen).turn
        print(f"== {measurement.set} {measurement.position_id} {measurement.code}")
        print(f"   swing {measurement.swing_cp} cp, FEN {fen}, played {sample['played_move']}")
        reports = {}
        for point in ("before", "played", "best"):
            uci = measurement.points.get(point, ())
            board = _board(fen, uci)
            reports[point] = evaluate_factors(board)
            line = chess.Board(fen).variation_san([chess.Move.from_uci(u) for u in uci])
            print(f"   {point}: {line}")
            print(f"      -> {board.fen()}")
        for change in fired(measurement, FACTOR_CONFIG):
            sides = []
            for point, report in reports.items():
                if change.kind == "group":
                    value = report.groups[change.guide_name]
                    white, black = value.white_mg, value.black_mg
                else:
                    part = next(c for c in report.contributions if c.name == change.guide_name)
                    white, black = part.white_mg, part.black_mg
                own, other = (white, black) if learner == chess.WHITE else (black, white)
                sides.append(f"{point} L{own}/O{other}")
            print(
                f"   fact {change.name} ({change.compared_with}, {change.change_pawns}): "
                + " | ".join(sides)
            )


if __name__ == "__main__":
    main()

"""Fixed-entry, metadata-minimal coach packet from the read-only recurrence cohort."""

from __future__ import annotations

import io
from collections import defaultdict
from zipfile import ZIP_DEFLATED, ZipFile

import chess
import chess.pgn

from scan64.learning.pattern_recurrence import (
    PatternRecurrenceReport,
    RecurrenceEvidence,
    RecurrencePeriod,
)


def _coverage(values: dict[str, int]) -> list[str]:
    return [f"- {key.replace('_', ' ')}: {value}" for key, value in sorted(values.items())]


def _period(name: str, period: RecurrencePeriod) -> list[str]:
    count = "unavailable (not zero)" if period.occurrences is None else str(period.occurrences)
    rate = "unavailable" if period.per_100_moves is None else str(period.per_100_moves)
    interval = (
        "unavailable"
        if period.wilson_95_per_100 is None
        else f"{period.wilson_95_per_100[0]} to {period.wilson_95_per_100[1]}"
    )
    return [
        f"### {name} training",
        "",
        f"- Eligible games: {period.games} / {period.candidate_games} dated finished online games",
        f"- Owner moves (denominator): {period.owner_moves}",
        f"- Occurrences: {count}",
        f"- Occurrences per 100 owner moves: {rate}",
        f"- 95% Wilson interval per 100 owner moves: {interval}",
        "- Exclusions:",
        *(_coverage(period.excluded) or ["  - None"]),
        "",
    ]


def build_coach_packet(
    report: PatternRecurrenceReport, evidence: list[RecurrenceEvidence]
) -> bytes:
    """Render only public cohort positions; never copy PGN headers or stored claims."""
    game_refs = {
        game_id: f"G{index:04d}"
        for index, game_id in enumerate(sorted({item.game_id for item in evidence}), 1)
    }
    patterns = {pattern.code: pattern for pattern in report.patterns}
    crosswalk: dict[str, list[str]] = defaultdict(list)
    pgns: list[str] = []
    for index, item in enumerate(
        sorted(evidence, key=lambda value: (value.code, value.period, value.game_id, value.ply)),
        1,
    ):
        pattern = patterns[item.code]
        reference = f"E{index:04d}"
        game_ref = game_refs[item.game_id]
        board = chess.Board(item.fen)
        move = board.parse_uci(item.move_uci)
        san = board.san(move)
        game = chess.pgn.Game()
        game.setup(board)
        # Explicit full-FEN setup, even if an occurrence starts at the initial board.
        game.headers.update(
            {
                "Event": f"Scan64 evidence {reference}",
                "Site": "Local",
                "White": "Owner" if board.turn else "Opponent",
                "Black": "Opponent" if board.turn else "Owner",
                "SetUp": "1",
                "FEN": board.fen(),
                "EvidenceReference": reference,
                "GameReference": game_ref,
                "SourcePly": str(item.ply),
                "Pattern": item.code,
                "Period": item.period,
            }
        )
        game.comment = (
            f"{reference}; {game_ref}; source ply {item.ply}; {pattern.name} "
            f"({item.code}); {item.period} training. "
            "Included in this pattern's M95 recurrence sample."
        )
        game.add_variation(move).comment = (
            f"Played {san}. Saved trusted primary diagnosis: {pattern.name}. "
            "Evidence annotation only, not generated advice."
        )
        pgns.append(str(game))
        crosswalk[item.code].append(
            f"- {reference}: {game_ref}, source ply {item.ply}, "
            f"{item.period} training, played {san}."
        )

    lines = [
        "# Scan64 coach packet",
        "",
        "## Scope and limitations",
        "",
        report.caveat,
        "",
        f"Each period needs at least {report.minimum_games_per_period} finished, fully analysed "
        f"games with at least {report.minimum_owner_moves_per_game} owner moves per game.",
        "Periods use validated PGN UTC start times around the first completed, server-verified "
        "mapped training attempt since learning reset, correct or incorrect, never import time.",
        "",
        "evidence.pgn contains exactly the verified primary occurrences included in the "
        "before/after samples below, including sparse samples; it is not a full game archive. "
        "Excluded games and patterns without a training anchor contribute no positions. "
        "No rate is inferred for an insufficient sample. Zero occurrences need no PGN entry.",
        "",
        "E references identify PGN entries. G references identify games within this packet "
        "only; source ply is the move's index in the saved game (not the FEN move number). "
        "Names, online links and original game metadata are omitted. The PGN starts at each "
        "full source FEN and contains only the recorded owner move, not a suggested line. "
        "Sharing this file deliberately shares those chess positions and the summary.",
        "",
        "## Library coverage and exclusions",
        "",
        *_coverage(report.coverage),
        "",
    ]
    for pattern in report.patterns:
        training = (
            pattern.first_training_at.isoformat()
            if pattern.first_training_at is not None
            else "unavailable"
        )
        lines.extend(
            [
                f"## {pattern.name} ({pattern.code})",
                "",
                "Before/after comparison" if pattern.status == "comparison" else "Not enough games",
                "",
                pattern.reason,
                "",
                f"First verified training: {training}",
                "",
                *_period("Before", pattern.before),
                *_period("After", pattern.after),
                "### Evidence crosswalk",
                "",
                *(crosswalk[pattern.code] or ["No included evidence positions for this pattern."]),
                "",
            ]
        )
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as packet:
        packet.writestr("summary.md", "\n".join(lines))
        packet.writestr("evidence.pgn", "\n\n".join(pgns) + ("\n" if pgns else ""))
    return buffer.getvalue()

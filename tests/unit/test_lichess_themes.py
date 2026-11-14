from __future__ import annotations

import asyncio
from dataclasses import replace

import chess
import pytest
from pydantic import ValidationError

from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evaluation.lichess_themes import (
    CODE_THEMES,
    CodeScore,
    Outcome,
    Puzzle,
    PuzzleRecording,
    RecordedLine,
    RecordedPuzzleAdapter,
    RecordingProvenance,
    diagnose,
    render_results,
    score,
    wilson,
)
from scan64.learning.plugins.registry import PluginRegistry

HANGING = "board_awareness.hanging_piece"
DIRECT = "threat_processing.missed_direct_threat"
CHECK = "threat_processing.missed_check"
CAPTURE = "threat_processing.missed_capture"


def registry() -> PluginRegistry:
    result = PluginRegistry()
    register_seeded_detectors(result)
    return result


def example() -> tuple[Puzzle, PuzzleRecording]:
    puzzle = Puzzle(
        "synthetic-unit",
        "7k/8/5n2/8/8/3PQ3/8/K7 w - - 0 1",
        "e3e4",
        frozenset({"hangingPiece"}),
    )
    record = PuzzleRecording(
        puzzle_id=puzzle.puzzle_id,
        before=[RecordedLine(pv=["e3f3"], score_cp=0)],
        after=[RecordedLine(pv=["f6e4", "d3e4", "h8g8"], score_cp=300)],
        focused=[RecordedLine(pv=[uci], score_cp=300) for uci in ("f6e4", "f6h7", "f6d7", "f6g4")],
    )
    return puzzle, record


def provenance() -> RecordingProvenance:
    return RecordingProvenance(
        sample_sha256="fc43cc078553522a19fb2cd0089eb5a63ad0518220a93788b530157099970b35",
        preregistration_commit="8c02417",
        engine="Stockfish unit-test metadata",
        threads=1,
        hash_mb=16,
        fast_nodes=10000,
        focused_nodes=1000000,
        focused_multipv=4,
        reset="ucinewgame before every request",
    )


def test_mapping_is_frozen_and_does_not_reverse_ownership() -> None:
    assert set(CODE_THEMES) == TRUSTED_CODES
    assert CODE_THEMES[HANGING] == CODE_THEMES[DIRECT] == {"hangingPiece"}
    assert CODE_THEMES[CHECK] == CODE_THEMES[CAPTURE] == set()
    assert not any("mate" in themes or "advantage" in themes for themes in CODE_THEMES.values())


@pytest.mark.parametrize(
    ("successes", "total", "expected"),
    [
        (0, 0, None),
        (0, 10, (0.0, 0.2775327999)),
        (10, 10, (0.7224672001, 1.0)),
        (8, 10, (0.4901624715, 0.9433178485)),
    ],
)
def test_wilson_known_values(
    successes: int, total: int, expected: tuple[float, float] | None
) -> None:
    actual = wilson(successes, total)
    assert actual == pytest.approx(expected) if expected is not None else actual is None


@pytest.mark.parametrize(("successes", "total"), [(-1, 10), (11, 10), (0, -1)])
def test_wilson_rejects_invalid_counts(successes: int, total: int) -> None:
    with pytest.raises(ValueError, match="successes"):
        wilson(successes, total)


def test_precision_and_recall_include_abstention_and_competing_code() -> None:
    tagged = frozenset({"hangingPiece", "short"})
    outcomes = [
        Outcome("a", tagged, HANGING),
        Outcome("b", frozenset({"fork"}), HANGING),
        Outcome("c", tagged, DIRECT),
        Outcome("d", tagged, "not_flagged"),
        Outcome("e", tagged, CHECK),
        Outcome("f", tagged, "untrusted_only", "tactics.pin"),
        Outcome("g", frozenset(), "no_eligible_diagnosis"),
    ]
    scores = score(outcomes)
    assert scores[HANGING] == CodeScore(support=2, theme_positive=5, overlap=1)
    assert scores[HANGING].precision == 0.5
    assert scores[HANGING].recall == 0.2
    assert scores[DIRECT] == CodeScore(support=1, theme_positive=5, overlap=1)
    assert scores[CHECK].support == 1
    assert CodeScore(0, 0, 0).precision is None
    assert CodeScore(0, 0, 0).recall is None


@pytest.mark.parametrize(
    "outcomes",
    [
        [Outcome("a", frozenset(), "unknown")],
        [Outcome("a", frozenset(), HANGING), Outcome("a", frozenset(), HANGING)],
        [Outcome("a", frozenset(), "untrusted_only")],
        [Outcome("a", frozenset(), "untrusted_only", HANGING)],
        [Outcome("a", frozenset(), HANGING, "tactics.pin")],
    ],
)
def test_score_rejects_missing_or_inconsistent_outcomes(outcomes: list[Outcome]) -> None:
    with pytest.raises(ValueError):
        score(outcomes)


def test_render_is_deterministic_including_ties_unmapped_and_untrusted() -> None:
    outcomes = [
        Outcome("a", frozenset({"zeta", "alpha", "hangingPiece"}), HANGING),
        Outcome("b", frozenset({"hangingPiece"}), CHECK),
        Outcome("c", frozenset({"alpha", "zeta"}), "untrusted_only", "tactics.pin"),
        Outcome("d", frozenset(), "no_eligible_diagnosis"),
        Outcome("e", frozenset(), "not_flagged"),
    ]
    rendered = render_results(outcomes, provenance(), "recording-hash")
    assert rendered == render_results(list(reversed(outcomes)), provenance(), "recording-hash")
    assert "| Disposition | alpha | hangingPiece | zeta |" in rendered
    assert f"| `{CHECK}` | 1 | N/A | N/A | N/A | N/A | N/A | unmapped |" in rendered
    assert "| `tactics.pin` | non-trusted eligible-only | 1 | 20.00% |" in rendered
    assert "| `no_eligible_diagnosis` | 1 | 20.00% |" in rendered
    assert "**3/5 (60.00%)**" in rendered
    assert "descriptive context" in rendered
    assert "defended but lost to a cheaper attacker" in rendered
    assert rendered.endswith("still-pending blind coach review.\n")


def test_a4_is_informational_and_handles_support_boundary() -> None:
    outcomes = [
        Outcome(str(index), frozenset({"hangingPiece"}) if index < 8 else frozenset(), HANGING)
        for index in range(10)
    ]
    output = render_results(outcomes, provenance(), "hash")
    assert "| 10 | 8 | 8 | 0.8000 | [0.4902, 0.9433] | 1.0000 | pass |" in output
    assert "| 0 | 8 | 0 | N/A | N/A | 0.0000 | low support (<10) |" in output
    with pytest.raises(ValueError, match="empty sample"):
        render_results([], provenance(), "hash")


@pytest.mark.asyncio
async def test_normal_pipeline_diagnoses_setup_mover_not_solver_and_ignores_tags() -> None:
    puzzle, record = example()
    assert await diagnose(puzzle, record, registry()) == (HANGING, None)
    assert await diagnose(replace(puzzle, themes=frozenset()), record, registry()) == (
        HANGING,
        None,
    )
    # Joint arbitration emits exactly one cause although the immediate profitable
    # capture can support both hanging-piece and missed-direct-threat detectors.
    results = await asyncio.gather(*(diagnose(puzzle, record, registry()) for _ in range(4)))
    assert results == [(HANGING, None)] * 4


@pytest.mark.asyncio
async def test_black_owner_mirror_keeps_same_diagnosis() -> None:
    puzzle, record = example()

    def mirror_uci(uci: str) -> str:
        move = chess.Move.from_uci(uci)
        return chess.Move(
            chess.square_mirror(move.from_square),
            chess.square_mirror(move.to_square),
            promotion=move.promotion,
        ).uci()

    black_puzzle = replace(
        puzzle, fen=chess.Board(puzzle.fen).mirror().fen(), blunder=mirror_uci(puzzle.blunder)
    )
    mirrored = record.model_copy(deep=True)
    assert mirrored.focused is not None
    for line in [*mirrored.before, *mirrored.after, *mirrored.focused]:
        line.pv = [mirror_uci(uci) for uci in line.pv]
    assert await diagnose(black_puzzle, mirrored, registry()) == (HANGING, None)


@pytest.mark.asyncio
async def test_normal_screening_keeps_unflagged_and_no_diagnosis_cases() -> None:
    puzzle, record = example()
    record.after = [RecordedLine(pv=["f6h7"], score_cp=0)]
    record.focused = None
    assert await diagnose(puzzle, record, registry()) == ("not_flagged", None)
    record.after[0].score_cp = 300
    record.focused = [RecordedLine(pv=[uci], score_cp=300) for uci in ("f6h7",) * 4]
    assert await diagnose(puzzle, record, registry()) == ("no_eligible_diagnosis", None)


@pytest.mark.asyncio
async def test_replay_fails_closed_on_missing_config_pv_or_focused_recording() -> None:
    puzzle, record = example()
    adapter = RecordedPuzzleAdapter(puzzle, record)
    with pytest.raises(ValueError, match="Unrecorded request"):
        await adapter.analyze_position(puzzle.fen, nodes=9999)
    with pytest.raises(ValueError, match="Unrecorded request"):
        await adapter.analyze_position(puzzle.fen, nodes=10000, depth=20)
    with pytest.raises(ValueError, match="ID mismatch"):
        RecordedPuzzleAdapter(replace(puzzle, puzzle_id="different"), record)
    record.focused = None
    with pytest.raises(ValueError, match="Unrecorded request"):
        await diagnose(puzzle, record, registry())
    record.before[0].pv = ["a1a8"]
    with pytest.raises(ValueError, match="illegal move"):
        RecordedPuzzleAdapter(puzzle, record)


def test_recording_scores_and_settings_cannot_silently_change() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        RecordedLine(pv=["e2e4"])
    with pytest.raises(ValidationError, match="exactly one"):
        RecordedLine(pv=["e2e4"], score_cp=1, score_mate=2)
    metadata = provenance().model_dump()
    metadata["fast_nodes"] = 100000
    with pytest.raises(ValidationError):
        RecordingProvenance.model_validate(metadata)

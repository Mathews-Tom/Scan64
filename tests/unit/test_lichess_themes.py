from __future__ import annotations

import asyncio
import runpy
from dataclasses import replace
from pathlib import Path

import chess
import pytest
from pydantic import ValidationError

from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evaluation.lichess_themes import (
    ALLOWED_MATE,
    CODE_THEMES,
    ORIGINAL_RECORDING_SHA256,
    ROOT,
    V1_SEED_CODES,
    V2_CODE_THEMES,
    V3_PREREGISTRATION,
    CodeScore,
    Outcome,
    Puzzle,
    PuzzleRecording,
    RecordedLine,
    RecordedPuzzleAdapter,
    Recording,
    RecordingProvenance,
    SourceAnalysisConfig,
    SourcePuzzleRecording,
    SourceRecording,
    SourceRecordingProvenance,
    code_themes,
    diagnose,
    needs_source_recording,
    render_results,
    render_v2_results,
    render_v3_results,
    score,
    source_analysis_id,
    validate_source_recording,
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
    assert set(CODE_THEMES) == {HANGING, DIRECT, CHECK, CAPTURE}
    assert set(CODE_THEMES) <= TRUSTED_CODES
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


def test_v2_mapping_only_adds_verified_mate_tags() -> None:
    assert code_themes("v1") == CODE_THEMES
    assert code_themes("v2") == V2_CODE_THEMES
    assert code_themes("v3") == V2_CODE_THEMES
    assert {code: tags for code, tags in V2_CODE_THEMES.items() if code != ALLOWED_MATE} == (
        CODE_THEMES
    )
    assert V2_CODE_THEMES[ALLOWED_MATE] == {"mate", "mateIn1", "mateIn2", "mateIn3", "backRankMate"}
    assert ALLOWED_MATE not in V1_SEED_CODES
    with pytest.raises(ValueError, match="version"):
        code_themes("v4")  # type: ignore[arg-type]


def test_v1_render_seed_distribution_excludes_new_detector() -> None:
    output = render_results([Outcome("a", frozenset(), "not_flagged")], provenance(), "hash")
    assert ALLOWED_MATE not in output


def test_v2_render_reports_both_wilson_intervals_and_old_code_deltas() -> None:
    baseline = [
        Outcome("a", frozenset({"hangingPiece", "mate"}), HANGING),
        Outcome("b", frozenset({"hangingPiece"}), HANGING),
        Outcome("c", frozenset({"mateIn2"}), "not_flagged"),
    ]
    outcomes = [
        replace(baseline[0], disposition=ALLOWED_MATE),
        baseline[1],
        baseline[2],
    ]
    output = render_v2_results(outcomes, baseline, provenance(), "hash")
    assert "Recall Wilson 95% CI" in output
    assert (
        f"| `{ALLOWED_MATE}` | 1 | 2 | 1 | 1.0000 | [0.2065, 1.0000] | 0.5000 | [0.0945, 0.9055] |"
    ) in output
    assert f"| `{HANGING}` | -1 | -1 | +0.0000 | -0.5000 |" in output
    assert f"| `{CHECK}` | +0 | N/A | N/A | N/A |" in output
    assert output == render_v2_results(list(reversed(outcomes)), baseline, provenance(), "hash")
    with pytest.raises(ValueError, match="same nonempty"):
        render_v2_results(outcomes, baseline[:-1], provenance(), "hash")


def test_v2_mapped_zero_denominators_remain_undefined() -> None:
    outcomes = [Outcome("a", frozenset(), "not_flagged")]
    output = render_v2_results(outcomes, outcomes, provenance(), "hash")
    assert f"| `{ALLOWED_MATE}` | 0 | 0 | 0 | N/A | N/A | N/A | N/A |" in output
    assert score(outcomes, version="v2")[ALLOWED_MATE] == CodeScore(0, 0, 0)
    with pytest.raises(ValueError, match="disposition"):
        score([Outcome("a", frozenset({"mate"}), ALLOWED_MATE)])


@pytest.mark.asyncio
async def test_v1_never_requests_appended_detectors(monkeypatch: pytest.MonkeyPatch) -> None:
    detectors = registry()
    original_get = detectors.get
    requested = []

    def frozen_get(*, kind, name):
        assert name in V1_SEED_CODES
        requested.append(name)
        return original_get(kind=kind, name=name)

    monkeypatch.setattr(detectors, "get", frozen_get)
    puzzle, record = example()
    assert await diagnose(puzzle, record, detectors, version="v1") == (HANGING, None)
    assert tuple(requested) == V1_SEED_CODES


def test_v2_untrusted_mate_is_an_abstention_not_an_emission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scan64.learning.evaluation import lichess_themes

    monkeypatch.setattr(lichess_themes, "TRUSTED_CODES", frozenset(CODE_THEMES))
    outcomes = [Outcome("a", frozenset({"mate"}), "untrusted_only", ALLOWED_MATE)]
    assert score(outcomes, version="v2")[ALLOWED_MATE] == CodeScore(0, 1, 0)
    output = render_v2_results(
        outcomes,
        [replace(outcomes[0], disposition="not_flagged", untrusted_code=None)],
        provenance(),
        "hash",
    )
    assert "M48 allowed-mate trust decision: **not trusted**" in output
    assert f"| `{ALLOWED_MATE}` | 1 | 100.00% |" in output
    assert "Trusted emissions: **0**; abstentions: **1**." in output


def mate_example() -> tuple[Puzzle, PuzzleRecording, SourcePuzzleRecording, SourceRecording]:
    board = chess.Board()
    board.push_uci("f2f3")
    board.push_uci("e7e5")
    puzzle = Puzzle("unit-mate", board.fen(), "g2g4", frozenset({"mateIn1"}))
    record = PuzzleRecording(
        puzzle_id=puzzle.puzzle_id,
        before=[RecordedLine(pv=["b1c3"], score_cp=0)],
        after=[RecordedLine(pv=["d8h4"], score_mate=1)],
        focused=[
            RecordedLine(pv=["d8h4"], score_mate=1),
            *[RecordedLine(pv=[uci], score_cp=0) for uci in ("b8c6", "g8f6", "a7a6")],
        ],
    )
    source = SourcePuzzleRecording(
        puzzle_id=puzzle.puzzle_id,
        source_fen=board.fen(),
        analysis_id=source_analysis_id(puzzle),
        config=SourceAnalysisConfig(
            engine_name=provenance().engine,
            engine_version="unknown",
            nodes=1000000,
            multipv=4,
        ),
        lines=[
            RecordedLine(pv=[uci], score_cp=0) for uci in ("b1c3", "g1h3", "a2a3", "h2h3")
        ],
    )
    sources = SourceRecording(
        provenance=SourceRecordingProvenance(
            sample_sha256=provenance().sample_sha256,
            original_recording_sha256=ORIGINAL_RECORDING_SHA256,
            preregistration_commit=V3_PREREGISTRATION,
            engine=provenance().engine,
            threads=1,
            hash_mb=16,
            source_nodes=1000000,
            source_multipv=4,
            reset="ucinewgame before every request",
            eligibility="fast-pass candidate with positive reply mate in 1-3",
        ),
        puzzles=[source],
    )
    return puzzle, record, source, sources


def test_source_recording_checks_exact_coverage_ids_fen_engine_and_pvs() -> None:
    puzzle, record, source, sources = mate_example()
    original = Recording(provenance=provenance(), puzzles=[record])
    assert validate_source_recording([puzzle], original, sources) == {puzzle.puzzle_id: source}
    assert needs_source_recording(record)
    for altered in (
        sources.model_copy(update={"puzzles": []}),
        sources.model_copy(update={"puzzles": [source, source]}),
        sources.model_copy(
            update={"puzzles": [source.model_copy(update={"source_fen": chess.STARTING_FEN})]}
        ),
        sources.model_copy(
            update={"puzzles": [source.model_copy(update={"analysis_id": source_analysis_id(
                replace(puzzle, puzzle_id="different")
            )})]}
        ),
    ):
        with pytest.raises(ValueError):
            validate_source_recording([puzzle], original, altered)
    source.lines[0].pv = ["a1a8"]
    with pytest.raises(ValueError, match="illegal"):
        validate_source_recording([puzzle], original, sources)
    source.lines[0].pv = list(source.lines[1].pv)
    with pytest.raises(ValueError, match="duplicate root"):
        validate_source_recording([puzzle], original, sources)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preregistration_commit", "wrong"),
        ("original_recording_sha256", "wrong"),
        ("source_nodes", 10000),
        ("source_multipv", 1),
        ("threads", 2),
        ("unexpected", True),
    ],
)
def test_source_provenance_fails_closed(field: str, value: object) -> None:
    _, _, _, sources = mate_example()
    metadata = sources.provenance.model_dump()
    metadata[field] = value
    with pytest.raises(ValidationError):
        SourceRecordingProvenance.model_validate(metadata)


@pytest.mark.asyncio
async def test_v3_requires_deep_source_and_excludes_prior_forced_mate() -> None:
    puzzle, record, source, _ = mate_example()
    with pytest.raises(ValueError, match="Missing or unexpected"):
        await diagnose(puzzle, record, registry(), version="v3")
    source.lines[0] = RecordedLine(pv=["b1c3"], score_mate=-6)
    assert await diagnose(puzzle, record, registry(), version="v3", source=source) == (
        "no_eligible_diagnosis", None
    )
    # Pre-fix replay deliberately keeps the frozen, genuinely shallow source rule.
    assert await diagnose(puzzle, record, registry(), version="v2") == (
        "untrusted_only", ALLOWED_MATE
    )
    with pytest.raises(ValueError, match="Historical"):
        await diagnose(puzzle, record, registry(), version="v2", source=source)
    ordinary, ordinary_record = example()
    with pytest.raises(ValueError, match="unexpected"):
        await diagnose(ordinary, ordinary_record, registry(), version="v3", source=source)


@pytest.mark.asyncio
async def test_historical_replay_is_isolated_from_future_production_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scan64.learning.diagnosis import arbitration
    from scan64.learning.evaluation import lichess_themes

    future_trust = frozenset({*CODE_THEMES, ALLOWED_MATE})
    monkeypatch.setattr(arbitration, "TRUSTED_CODES", future_trust)
    monkeypatch.setattr(lichess_themes, "TRUSTED_CODES", future_trust)
    puzzle, record, source, _ = mate_example()
    assert await diagnose(puzzle, record, registry(), version="v1") == (
        "no_eligible_diagnosis", None
    )
    assert await diagnose(puzzle, record, registry(), version="v2") == (
        "untrusted_only", ALLOWED_MATE
    )
    assert await diagnose(puzzle, record, registry(), version="v3", source=source) == (
        ALLOWED_MATE, None
    )
    frozen = [Outcome("a", frozenset({"mate"}), "untrusted_only", ALLOWED_MATE)]
    assert score(frozen, version="v2")[ALLOWED_MATE] == CodeScore(0, 1, 0)
    assert "M48 allowed-mate trust decision: **not trusted**" in render_v2_results(
        frozen, frozen, provenance(), "hash"
    )


def test_v3_render_keeps_same_mapping_intervals_and_both_historical_comparisons() -> None:
    _, _, _, sources = mate_example()
    baseline = [
        Outcome("a", frozenset({"hangingPiece", "mate"}), HANGING),
        Outcome("b", frozenset({"hangingPiece"}), HANGING),
        Outcome("c", frozenset({"mateIn2"}), "not_flagged"),
    ]
    outcomes = [replace(baseline[0], disposition=ALLOWED_MATE), *baseline[1:]]
    output = render_v3_results(outcomes, baseline, baseline, provenance(), sources, "source-hash")
    assert "Recall Wilson 95% CI" in output
    assert "### V3 minus v1" in output
    assert "### V3 minus v2 (pre-fix)" in output
    assert f"| `{ALLOWED_MATE}` | N/A | N/A | N/A | N/A |" in output
    assert f"| `{HANGING}` | -1 | -1 | +0.0000 | -0.5000 |" in output
    assert (
        f"| `{ALLOWED_MATE}` | 1 | 2 | 1 | 1.0000 | [0.2065, 1.0000] | "
        "0.5000 | [0.0945, 0.9055] |"
    ) in output
    assert output == render_v3_results(
        list(reversed(outcomes)), baseline, baseline, provenance(), sources, "source-hash"
    )
    assert "source-hash" in output
    with pytest.raises(ValueError, match="same nonempty"):
        render_v3_results(outcomes, baseline[:-1], baseline, provenance(), sources, "hash")


@pytest.mark.asyncio
async def test_v3_untrusted_mate_suppresses_material_but_historical_versions_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scan64.learning.diagnosis import arbitration
    from scan64.learning.evaluation import lichess_themes

    monkeypatch.setattr(arbitration, "TRUSTED_CODES", frozenset(CODE_THEMES))
    monkeypatch.setattr(lichess_themes, "TRUSTED_CODES", frozenset(CODE_THEMES))
    puzzle, record, source, _ = mate_example()
    board = chess.Board(puzzle.fen)
    board.remove_piece_at(chess.D1)
    board.set_piece_at(chess.H4, chess.Piece(chess.QUEEN, chess.WHITE))
    puzzle = replace(puzzle, fen=board.fen())
    source.source_fen = board.fen()
    source.analysis_id = source_analysis_id(puzzle)
    assert await diagnose(puzzle, record, registry(), version="v1") == (HANGING, None)
    assert await diagnose(puzzle, record, registry(), version="v2") == (HANGING, None)
    assert await diagnose(puzzle, record, registry(), version="v3", source=source) == (
        "untrusted_only", ALLOWED_MATE
    )
    source.lines[0] = RecordedLine(pv=["b1c3"], score_mate=-6)
    assert await diagnose(puzzle, record, registry(), version="v3", source=source) == (
        HANGING, None
    )


@pytest.mark.asyncio
async def test_historical_composer_calls_use_none_without_fake_source_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scan64.learning.evaluation import lichess_themes

    original_compose = lichess_themes.compose_candidate_evidence
    observed = []

    def capture(**kwargs):
        assert "mate_source_analysis" in kwargs
        observed.append(kwargs["mate_source_analysis"])
        return original_compose(**kwargs)

    monkeypatch.setattr(lichess_themes, "compose_candidate_evidence", capture)
    puzzle, record, source, _ = mate_example()
    await diagnose(puzzle, record, registry(), version="v1")
    await diagnose(puzzle, record, registry(), version="v2")
    await diagnose(puzzle, record, registry(), version="v3", source=source)
    assert observed[:2] == [None, None]
    assert observed[2].id == source.analysis_id
    assert observed[2].config["nodes"] == 1000000
    assert observed[2].config["multipv"] == 4


def test_source_analysis_configuration_is_strict_and_actual_engine_must_match() -> None:
    puzzle, record, source, sources = mate_example()
    original = Recording(provenance=provenance(), puzzles=[record])
    for field, value in (("nodes", 10000), ("multipv", 1), ("time_ms", 1000)):
        config = source.config.model_dump()
        config[field] = value
        with pytest.raises(ValidationError):
            SourceAnalysisConfig.model_validate(config)
    source.config.engine_name = "different"
    with pytest.raises(ValueError, match="engine"):
        validate_source_recording([puzzle], original, sources)
    source.config.engine_name = provenance().engine
    sources.provenance.engine = "different"
    with pytest.raises(ValueError, match="engine"):
        validate_source_recording([puzzle], original, sources)


def test_source_save_serializes_stable_uuid_and_refuses_overwrite(tmp_path: Path) -> None:
    _, _, source, recording = mate_example()
    save = runpy.run_path(str(ROOT / "scripts/record_lichess_theme_agreement.py"))["save"]
    destination = tmp_path / "source.json"
    save(recording, destination)
    restored = SourceRecording.model_validate_json(destination.read_bytes())
    assert restored.puzzles[0].analysis_id == source.analysis_id
    assert restored == recording
    with pytest.raises(FileExistsError):
        save(recording, destination)

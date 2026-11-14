"""Offline Lichess-theme agreement proxy; never coach validation or a trust gate.

Run: uv run python -m scan64.learning.evaluation.lichess_themes [--check]
Stdout is the report's complete Results section. No engine or network is used.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

import chess
from pydantic import BaseModel, ConfigDict, model_validator

from scan64.chess.analysis.models import EngineAnalysis
from scan64.chess.analysis.orchestration import FastPassConfig, FastPassOrchestrator
from scan64.learning.diagnosis.arbitration import arbitrate_diagnoses
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.models import DiagnosisCandidate, LearningOpportunity, PlayerContext
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.diagnosis.trust import TRUSTED_CODES
from scan64.learning.evidence.composer import (
    compose_candidate_evidence,
    exchange_material_gain,
    line_material_gain,
    mating_line_san,
)
from scan64.learning.evidence.models import Evidence
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginKind, PluginRegistry

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "tests/fixtures/m77"
REPORT = ROOT / "docs/evaluation/lichess-theme-agreement.md"
SAMPLE_SHA256 = "fc43cc078553522a19fb2cd0089eb5a63ad0518220a93788b530157099970b35"
CODE_THEMES: dict[str, frozenset[str]] = {
    "board_awareness.hanging_piece": frozenset({"hangingPiece"}),
    "threat_processing.missed_capture": frozenset(),
    "threat_processing.missed_check": frozenset(),
    "threat_processing.missed_direct_threat": frozenset({"hangingPiece"}),
}
ABSTENTIONS = ("not_flagged", "no_eligible_diagnosis", "untrusted_only")
DISPOSITIONS = (*CODE_THEMES, *ABSTENTIONS)
VERSION_TWO_REPORT = ROOT / "docs/evaluation/lichess-theme-agreement-v2.md"
V2_PREREGISTRATION = "66eb859"
ALLOWED_MATE = "threat_processing.allowed_mate"
VERSION_THREE_REPORT = ROOT / "docs/evaluation/lichess-theme-agreement-v3.md"
V3_PREREGISTRATION = "597627f"
ORIGINAL_RECORDING_SHA256 = "9a13d33ef51ea906f833e0d51092850652c59e988708d35225ccef79fbab71af"
SOURCE_RECORDING_NAME = "recorded-source-v3.json"
HISTORICAL_TRUSTED_CODES = frozenset(CODE_THEMES)
MATERIAL_CODES = frozenset(
    {
        "board_awareness.hanging_piece",
        "threat_processing.missed_direct_threat",
        "threat_processing.missed_capture",
    }
)
V2_CODE_THEMES = {
    **CODE_THEMES,
    ALLOWED_MATE: frozenset({"mate", "mateIn1", "mateIn2", "mateIn3", "backRankMate"}),
}
V1_SEED_CODES = (
    "board_awareness.hanging_piece",
    "threat_processing.missed_check",
    "threat_processing.missed_capture",
    "threat_processing.missed_direct_threat",
    "tactics.fork.knight",
    "tactics.pin",
    "tactics.overloaded_defender",
    "calculation.stopped_too_early",
    "opening.delayed_development",
    "positional.king_safety_neglect",
)
Version = Literal["v1", "v2", "v3"]


def code_themes(version: Version) -> dict[str, frozenset[str]]:
    if version not in {"v1", "v2", "v3"}:
        raise ValueError(f"Unknown experiment version: {version}")
    return CODE_THEMES if version == "v1" else V2_CODE_THEMES


class RecordedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pv: list[str]
    score_cp: int | None = None
    score_mate: int | None = None

    @model_validator(mode="after")
    def require_score(self) -> RecordedLine:
        if (self.score_cp is None) == (self.score_mate is None):
            raise ValueError("A recorded PV must have exactly one cp or mate score")
        return self


class PuzzleRecording(BaseModel):
    model_config = ConfigDict(extra="forbid")
    puzzle_id: str
    before: list[RecordedLine]
    after: list[RecordedLine]
    focused: list[RecordedLine] | None = None


class RecordingProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_sha256: Literal["fc43cc078553522a19fb2cd0089eb5a63ad0518220a93788b530157099970b35"]
    preregistration_commit: str
    engine: str
    threads: Literal[1]
    hash_mb: Literal[16]
    fast_nodes: Literal[10000]
    focused_nodes: Literal[1000000]
    focused_multipv: Literal[4]
    reset: Literal["ucinewgame before every request"]


class Recording(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provenance: RecordingProvenance
    puzzles: list[PuzzleRecording]


class SourceAnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine_name: str
    engine_version: Literal["unknown"]
    nodes: Literal[1000000]
    depth: None = None
    time_ms: None = None
    multipv: Literal[4]


class SourcePuzzleRecording(BaseModel):
    model_config = ConfigDict(extra="forbid")
    puzzle_id: str
    source_fen: str
    analysis_id: UUID
    config: SourceAnalysisConfig
    lines: list[RecordedLine]


class SourceRecordingProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_sha256: Literal["fc43cc078553522a19fb2cd0089eb5a63ad0518220a93788b530157099970b35"]
    original_recording_sha256: Literal[
        "9a13d33ef51ea906f833e0d51092850652c59e988708d35225ccef79fbab71af"
    ]
    preregistration_commit: Literal["597627f"]
    engine: str
    threads: Literal[1]
    hash_mb: Literal[16]
    source_nodes: Literal[1000000]
    source_multipv: Literal[4]
    reset: Literal["ucinewgame before every request"]
    eligibility: Literal["fast-pass candidate with positive reply mate in 1-3"]


class SourceRecording(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provenance: SourceRecordingProvenance
    puzzles: list[SourcePuzzleRecording]


@dataclass(frozen=True)
class Puzzle:
    puzzle_id: str
    fen: str
    blunder: str
    themes: frozenset[str]

    def boards_and_san(self) -> tuple[chess.Board, chess.Board, str]:
        before = chess.Board(self.fen)
        move = chess.Move.from_uci(self.blunder)
        san = before.san(move)
        after = before.copy()
        after.push(move)
        return before, after, san


@dataclass(frozen=True)
class Outcome:
    puzzle_id: str
    themes: frozenset[str]
    disposition: str
    untrusted_code: str | None = None


@dataclass(frozen=True)
class CodeScore:
    support: int
    theme_positive: int
    overlap: int

    @property
    def precision(self) -> float | None:
        return self.overlap / self.support if self.support else None

    @property
    def recall(self) -> float | None:
        return self.overlap / self.theme_positive if self.theme_positive else None


def wilson(successes: int, total: int) -> tuple[float, float] | None:
    """Two-sided Wilson 95% interval, undefined when no code was emitted."""
    if total < 0 or not 0 <= successes <= total:
        raise ValueError("Expected 0 <= successes <= total")
    if total == 0:
        return None
    z = 1.959963984540054
    p = successes / total
    divisor = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / divisor
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / divisor
    return max(0.0, centre - radius), min(1.0, centre + radius)


def load_sample(directory: Path = FIXTURES) -> list[Puzzle]:
    path = directory / "puzzles.csv"
    if hashlib.sha256(path.read_bytes()).hexdigest() != SAMPLE_SHA256:
        raise ValueError("Frozen sample checksum mismatch")
    with path.open(newline="") as stream:
        puzzles = [
            Puzzle(row["PuzzleId"], row["FEN"], row["Blunder"], frozenset(row["Themes"].split()))
            for row in csv.DictReader(stream)
        ]
    if len(puzzles) != 2000 or len({p.puzzle_id for p in puzzles}) != len(puzzles):
        raise ValueError("Frozen sample count or unique IDs differ")
    return puzzles


def needs_source_recording(record: PuzzleRecording) -> bool:
    mate = record.after[0].score_mate if record.after else None
    return record.focused is not None and mate is not None and 1 <= mate <= 3


def source_analysis_id(puzzle: Puzzle) -> UUID:
    before, _, _ = puzzle.boards_and_san()
    return uuid5(
        NAMESPACE_URL,
        f"scan64:m77:v3:{V3_PREREGISTRATION}:{puzzle.puzzle_id}:{before.fen()}",
    )


def validate_source_recording(
    puzzles: list[Puzzle], recording: Recording, sources: SourceRecording
) -> dict[str, SourcePuzzleRecording]:
    expected = [
        puzzle
        for puzzle, record in zip(puzzles, recording.puzzles, strict=True)
        if needs_source_recording(record)
    ]
    if [item.puzzle_id for item in sources.puzzles] != [p.puzzle_id for p in expected]:
        raise ValueError("Source recording must cover exactly the eligible IDs in sample order")
    if sources.provenance.engine != recording.provenance.engine:
        raise ValueError("Source engine differs from the frozen original engine")
    for puzzle, source in zip(expected, sources.puzzles, strict=True):
        before, _, _ = puzzle.boards_and_san()
        if source.source_fen != before.fen() or source.analysis_id != source_analysis_id(puzzle):
            raise ValueError("Source FEN or stable analysis ID mismatch")
        if source.config.engine_name != sources.provenance.engine:
            raise ValueError("Source analysis engine differs from its provenance")
        RecordedPuzzleAdapter._validate_lines(
            before, source.lines, expected=max(1, min(4, before.legal_moves.count()))
        )
        if len({tuple(line.pv[:1]) for line in source.lines}) != len(source.lines):
            raise ValueError("Source MultiPV contains duplicate root moves")
    return {item.puzzle_id: item for item in sources.puzzles}


def _analysis(lines: list[RecordedLine]) -> EngineAnalysis:
    return EngineAnalysis(raw_result=[line.model_dump(exclude_none=True) for line in lines])


class RecordedPuzzleAdapter:
    """Exact FEN/config replay; no fallback or live engine is permitted."""

    def __init__(self, puzzle: Puzzle, record: PuzzleRecording) -> None:
        if puzzle.puzzle_id != record.puzzle_id:
            raise ValueError("Puzzle/recording ID mismatch")
        before, after, _ = puzzle.boards_and_san()
        self.before_fen, self.after_fen = before.fen(), after.fen()
        self.record = record
        self._validate_lines(before, record.before, expected=1)
        self._validate_lines(after, record.after, expected=1)
        if record.focused is not None:
            self._validate_lines(
                after, record.focused, expected=max(1, min(4, after.legal_moves.count()))
            )

    @staticmethod
    def _validate_lines(board: chess.Board, lines: list[RecordedLine], *, expected: int) -> None:
        if len(lines) != expected:
            raise ValueError("Recorded MultiPV cardinality differs from requested legal moves")
        for line in lines:
            replay = board.copy()
            if not line.pv and not board.is_game_over():
                raise ValueError("Nonterminal recorded analysis has an empty PV")
            for uci in line.pv:
                move = chess.Move.from_uci(uci)
                if move not in replay.legal_moves:
                    raise ValueError("Recorded PV contains an illegal move")
                replay.push(move)

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        if depth is None and time_ms is None:
            if nodes == 10000 and multipv == 1:
                if fen == self.before_fen:
                    return _analysis(self.record.before)
                if fen == self.after_fen:
                    return _analysis(self.record.after)
            if (
                nodes == 1000000
                and multipv == 4
                and fen == self.after_fen
                and self.record.focused is not None
            ):
                return _analysis(self.record.focused)
        raise ValueError(f"Unrecorded request: {fen}, nodes={nodes}, multipv={multipv}")


def _historical_played_capture(
    before: chess.Board, san: str, fast: EngineAnalysis, base: Evidence
) -> Evidence | None:
    """Restore M77's frozen pre-M79 identity defect in offline replay only."""
    line = fast.raw_result[0].get("pv", []) if fast.raw_result else []
    played = before.parse_san(san)
    if not line or line[0] != played.uci() or not before.is_capture(played):
        return None
    gain = line_material_gain(before, line)
    local_gain = exchange_material_gain(before, line)
    if base.payload["swing_cp"] < 200 or gain is None or gain < 1:
        return None
    if local_gain is None or local_gain < 1:
        return None
    square = played.to_square
    if before.is_en_passant(played):
        square += -8 if before.turn else 8
    victim = before.piece_at(square)
    return Evidence(
        evidence_id=f"m77-pre-m79-capture:{base.position_id}",
        kind="missed_opportunity",
        position_id=base.position_id,
        engine_analysis_id=base.engine_analysis_id,
        claim="historical pre-M79 capture identity rule (offline replay only)",
        payload={
            **base.payload,
            "missed_type": "capture",
            "best_move": played.uci(),
            "captured_square": chess.square_name(square),
            "captured_piece": victim.symbol() if victim is not None else None,
            "exchange_gain": local_gain,
            "material_gain": gain,
        },
    )


def _legacy_allowed_mate_evidence(
    puzzle: Puzzle,
    fast: EngineAnalysis,
    reply: EngineAnalysis,
    base: Evidence,
) -> Evidence | None:
    """Reconstruct v2's pre-fix fast-source rule only; never production evidence."""
    before_score = fast.raw_result[0] if fast.raw_result else {}
    after_score = reply.raw_result[0] if reply.raw_result else {}
    before_mate = before_score.get("score_mate")
    after_mate = after_score.get("score_mate")
    before_not_mated = (
        isinstance(before_mate, int)
        and before_mate > 0
        or before_mate is None
        and isinstance(before_score.get("score_cp"), int)
    )
    _, after, _ = puzzle.boards_and_san()
    line = after_score.get("pv")
    if not before_not_mated or mating_line_san(after, line, after_mate) is None:
        return None
    return Evidence(
        evidence_id=f"m77-v2-legacy:{puzzle.puzzle_id}",
        kind="blunder_analysis",
        position_id=puzzle.puzzle_id,
        engine_analysis_id=base.engine_analysis_id,
        claim="historical v2 fast-source allowed-mate rule (pre-fix replay only)",
        payload={
            **base.payload,
            "blunder_type": "allowed_mate",
            "before_score_mate": before_mate,
            "before_score_cp": before_score.get("score_cp"),
            "after_score_mate": after_mate,
            "mate_in": after_mate,
            "mating_line": line,
        },
    )


def _historical_arbitrate(
    candidates: list[DiagnosisCandidate], evidence: list[Evidence], *, trusted_only: bool
) -> tuple[DiagnosisCandidate, tuple[DiagnosisCandidate, ...]] | None:
    """Freeze v1/v2 trust-dependent precedence without weakening production rules."""
    legacy_ids = {
        item.evidence_id for item in evidence if item.evidence_id.startswith("m77-v2-legacy:")
    }
    mates = [
        candidate
        for candidate in candidates
        if not trusted_only
        and candidate.skill_id == ALLOWED_MATE
        and candidate.evidence_ids
        and all(identifier in legacy_ids for identifier in candidate.evidence_ids)
    ]
    old_candidates = [
        candidate
        for candidate in candidates
        if candidate.skill_id != ALLOWED_MATE
        and (not trusted_only or candidate.skill_id in HISTORICAL_TRUSTED_CODES)
        and (not mates or candidate.skill_id not in MATERIAL_CODES)
    ]
    old_winner = arbitrate_diagnoses(
        old_candidates,
        [item for item in evidence if item.payload.get("blunder_type") != "allowed_mate"],
        trusted_only=False,
    )
    ranked = sorted(
        [*mates, *([old_winner[0]] if old_winner is not None else [])],
        key=lambda candidate: (-candidate.confidence, candidate.skill_id),
    )
    return (ranked[0], ()) if ranked else None


async def diagnose(
    puzzle: Puzzle,
    record: PuzzleRecording,
    registry: PluginRegistry,
    *,
    version: Version = "v1",
    source: SourcePuzzleRecording | None = None,
) -> tuple[str, str | None]:
    """Diagnose the setup mover; puzzle themes never enter the evidence pipeline."""
    mapping = code_themes(version)
    adapter = RecordedPuzzleAdapter(puzzle, record)
    before, after, san = puzzle.boards_and_san()
    if source is not None and version != "v3":
        raise ValueError("Historical replay cannot consume v3 source analysis")
    if version == "v3" and (source is not None) != needs_source_recording(record):
        raise ValueError("Missing or unexpected v3 source recording")
    fast = await FastPassOrchestrator(adapter, FastPassConfig(nodes=10000)).run_fast_pass(
        [san], puzzle.fen
    )
    if not fast.candidates:
        if record.focused is not None:
            raise ValueError("Unexpected focused recording for an unflagged setup")
        return "not_flagged", None
    candidate = fast.candidates[0]
    focused = await adapter.analyze_position(after.fen(), nodes=1000000, multipv=4)
    evidence = compose_candidate_evidence(
        before_board=before,
        after_board=after,
        history_san=[san],
        initial_fen=puzzle.fen,
        position_id=puzzle.puzzle_id,
        fast_analysis=candidate.before_analysis,
        reply_analysis=candidate.after_analysis,
        focused_analysis=focused,
        mate_source_analysis=(
            EngineAnalysis(
                id=source.analysis_id,
                config=source.config.model_dump(),
                raw_result=[line.model_dump(exclude_none=True) for line in source.lines],
            )
            if source is not None
            else None
        ),
        played_move=san,
        swing_cp=candidate.swing_cp,
    )
    historical_capture = _historical_played_capture(
        before, san, candidate.before_analysis, evidence[0]
    )
    if historical_capture is not None:
        evidence.append(historical_capture)
    if version != "v3":
        evidence = [item for item in evidence if item.payload.get("blunder_type") != "allowed_mate"]
        if version == "v2":
            legacy = _legacy_allowed_mate_evidence(
                puzzle, candidate.before_analysis, candidate.after_analysis, evidence[0]
            )
            if legacy is not None:
                evidence.append(legacy)
    opportunity = LearningOpportunity(
        opportunity_id=puzzle.puzzle_id,
        position_id=puzzle.puzzle_id,
        player_id="m77-owner",
        played_move=san,
        engine_eval_before=0,
        engine_eval_after=-candidate.swing_cp / 100,
    )
    candidates: list[DiagnosisCandidate] = []
    for code in V1_SEED_CODES if version == "v1" else SEED_CODES:
        detector = registry.get(kind=PluginKind.PATTERN_DETECTOR, name=code)
        assert isinstance(detector, PatternDetector)
        candidates.extend(
            await detector.detect(opportunity, evidence, PlayerContext(player_id="m77-owner"))
        )
    winner = (
        arbitrate_diagnoses(candidates, evidence)
        if version == "v3"
        else _historical_arbitrate(candidates, evidence, trusted_only=True)
    )
    if winner is not None:
        if winner[0].skill_id not in mapping:
            raise ValueError("Trusted code set changed since pre-registration")
        return winner[0].skill_id, None
    untrusted = (
        arbitrate_diagnoses(candidates, evidence, trusted_only=False)
        if version == "v3"
        else _historical_arbitrate(candidates, evidence, trusted_only=False)
    )
    if untrusted is not None:
        return "untrusted_only", untrusted[0].skill_id
    return "no_eligible_diagnosis", None


def score(outcomes: list[Outcome], *, version: Version = "v1") -> dict[str, CodeScore]:
    mapping = code_themes(version)
    dispositions = (*mapping, *ABSTENTIONS)
    seeds = V1_SEED_CODES if version == "v1" else SEED_CODES
    trusted = TRUSTED_CODES if version == "v3" else HISTORICAL_TRUSTED_CODES
    if len({outcome.puzzle_id for outcome in outcomes}) != len(outcomes):
        raise ValueError("Duplicate outcome ID")
    if any(outcome.disposition not in dispositions for outcome in outcomes):
        raise ValueError("Unknown outcome disposition")
    for outcome in outcomes:
        if outcome.disposition == "untrusted_only":
            if outcome.untrusted_code not in seeds or outcome.untrusted_code in trusted:
                raise ValueError("Untrusted-only outcome must identify a non-trusted code")
        elif outcome.untrusted_code is not None:
            raise ValueError("Only untrusted-only outcomes may carry a non-trusted code")
    return {
        code: CodeScore(
            support=sum(o.disposition == code for o in outcomes),
            theme_positive=sum(bool(o.themes & themes) for o in outcomes),
            overlap=sum(o.disposition == code and bool(o.themes & themes) for o in outcomes),
        )
        for code, themes in mapping.items()
    }


def _ratio(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"


def render_results(
    outcomes: list[Outcome], provenance: RecordingProvenance, recording_sha256: str
) -> str:
    scores = score(outcomes)
    counts = Counter(o.disposition for o in outcomes)
    n = len(outcomes)
    if not n:
        raise ValueError("An empty sample has no agreement report")
    abstained = sum(counts[key] for key in ABSTENTIONS)
    flagged = n - counts["not_flagged"]
    lines = [
        "## Results",
        "",
        "Informational theme agreement only; no coach validation or efficacy claim.",
        "Blind independent coach review remains **PENDING**.",
        "",
        f"Frozen sample: **{n:,}** owner-side human setup moves. Fast-pass flagged:",
        f"**{flagged:,}**. Trusted emissions: **{n - abstained:,}**; abstentions:",
        f"**{abstained:,}/{n:,} ({abstained / n:.2%})** (includes untrusted-only rows).",
        "",
        "### Per-code agreement",
        "",
        "| Code | Emitted support | Theme-positive rows | Matches | Precision | "
        "Wilson 95% CI | Recall | A4 (informational) |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for code, themes in CODE_THEMES.items():
        result = scores[code]
        if not themes:
            values = f"{result.support} | N/A | N/A | N/A | N/A | N/A | unmapped"
        else:
            interval = wilson(result.overlap, result.support)
            ci = "N/A" if interval is None else f"[{interval[0]:.4f}, {interval[1]:.4f}]"
            interpretation = (
                "low support (<10)"
                if result.support < 10
                else "pass"
                if result.precision is not None and result.precision >= 0.80
                else "below 0.80"
            )
            values = (
                f"{result.support} | {result.theme_positive} | {result.overlap} | "
                f"{_ratio(result.precision)} | {ci} | {_ratio(result.recall)} | {interpretation}"
            )
        lines.append(f"| `{code}` | {values} |")
    lines.extend(
        [
            "",
            "N/A for unmapped codes is not a measured zero. A4 does not change product",
            "trust and does not determine the CLI exit code.",
            "",
            "### Disposition and abstention",
            "",
            "| Disposition | Count | Rate over full sample |",
            "| --- | ---: | ---: |",
        ]
    )
    for disposition in DISPOSITIONS:
        lines.append(f"| `{disposition}` | {counts[disposition]} | {counts[disposition] / n:.2%} |")
    untrusted_counts = Counter(o.untrusted_code for o in outcomes if o.untrusted_code is not None)
    lines.extend(
        [
            "",
            "### Emitted-code distribution (including non-trusted eligible-only winners)",
            "",
            "Trusted rows use the normal joint winner. Non-trusted rows below are",
            "measurement-only winners when the normal trusted pipeline abstains, not",
            "product emissions. All no-diagnosis cases are shown above; no rows disappear.",
            "",
            "| Code | Status | Count | Rate over full sample |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for code in V1_SEED_CODES:
        trusted = code in CODE_THEMES
        count = counts[code] if trusted else untrusted_counts[code]
        status = "trusted emission" if trusted else "non-trusted eligible-only"
        lines.append(f"| `{code}` | {status} | {count} | {count / n:.2%} |")
    totals = Counter(theme for o in outcomes for theme in o.themes)
    top_themes = sorted(totals, key=lambda theme: (-totals[theme], theme))[:10]
    lines.extend(
        [
            "",
            "### Codes × top themes (co-occurrence, not exclusive classes)",
            "",
            "Unmapped-code rows show raw theme co-occurrence as descriptive context",
            "only, never precision, recall or confirmation of those codes.",
            "",
            "| Disposition | " + " | ".join(top_themes) + " |",
            "| --- | " + " | ".join("---:" for _ in top_themes) + " |",
            "| All sample rows | " + " | ".join(str(totals[t]) for t in top_themes) + " |",
        ]
    )
    for disposition in DISPOSITIONS:
        cells = [
            str(sum(o.disposition == disposition and theme in o.themes for o in outcomes))
            for theme in top_themes
        ]
        lines.append(f"| `{disposition}` | " + " | ".join(cells) + " |")
    lines.extend(
        [
            "",
            "### Recording provenance and reproduction",
            "",
            f"- Pre-registration commit: `{provenance.preregistration_commit}`.",
            f"- Engine: **{provenance.engine}**, Threads {provenance.threads}, "
            f"Hash {provenance.hash_mb} MiB; reset: {provenance.reset}.",
            f"- Fast: {provenance.fast_nodes:,} nodes, single PV before/after each setup.",
            f"- Focused: {provenance.focused_nodes:,} nodes, "
            f"MultiPV {provenance.focused_multipv} after flagged setups only.",
            f"- Sample sha256: `{provenance.sample_sha256}`.",
            f"- `tests/fixtures/m77/recorded.json` sha256: `{recording_sha256}`.",
            "- Source URL, compressed prefix byte range/checksum and HTTP metadata are",
            "  frozen above and in `tests/fixtures/m77/provenance.json` (CC0 subset).",
            "",
            "```sh",
            "uv run python -m scan64.learning.evaluation.lichess_themes",
            "uv run python -m scan64.learning.evaluation.lichess_themes --check",
            "```",
            "",
            "The first command reproduces this entire Results suffix byte-for-byte.",
            "The second checks it offline without launching an engine or accessing the",
            "network/database. Engine regeneration is an explicit separate operation:",
            "`uv run python scripts/record_lichess_theme_agreement.py`.",
            "It refuses to overwrite the committed recording; regeneration with another",
            "engine is a new experiment, not a way to improve these numbers.",
            "",
            "### Limits",
            "",
            "Themes are not a coach's judgement: the Lichess tagger seeds them",
            "algorithmically, and hangingPiece is vote-refinable. The CSV does not reveal",
            "which individual labels were changed by humans. Scan64 and Lichess both",
            "use Stockfish-derived information, so this is not independent chess truth.",
            "",
            "This prefix-ordered, filtered puzzle subset is enriched for tactical",
            "punishments; it is not a random sample of players, mistakes or ordinary",
            "games. Puzzle ratings are not the blunderers' playing ratings. Wilson",
            "intervals treat rows as independent Bernoulli trials; shared players/games",
            "can violate independence, and the intervals do not measure label quality",
            "or systematic sampling bias.",
            "",
            "The owner is the setup blunderer, opposite the puzzle solver. Opponent",
            "continuation themes cannot validate the owner's missed checks/captures.",
            "hangingPiece is only an overlapping proxy for the two mapped codes:",
            "it does not prove a pre-existing threat and can exclude captures/exchanges",
            "that Scan64 diagnoses. Joint arbitration permits only one winner even",
            "when mapped concepts overlap. Theme absence is proxy disagreement, not",
            "necessarily a false diagnosis. A narrow mapping caps the measurable recall",
            "coverage of the wider diagnosis concept; numerical recall concerns only",
            "the mapped-theme subset. missed_direct_threat cases where the attacked",
            "piece was defended but lost to a cheaper attacker will be counted as",
            "disagreement when hangingPiece is absent. No mapping or sample was",
            "adjusted to fit these results. This report neither validates instructional",
            "efficacy nor replaces the still-pending blind coach review.",
            "",
        ]
    )
    return "\n".join(lines)


def _interval(successes: int, total: int) -> str:
    interval = wilson(successes, total)
    return "N/A" if interval is None else f"[{interval[0]:.4f}, {interval[1]:.4f}]"


def _delta(current: float | None, previous: float | None) -> str:
    return "N/A" if current is None or previous is None else f"{current - previous:+.4f}"


def render_v2_results(
    outcomes: list[Outcome],
    baseline: list[Outcome],
    provenance: RecordingProvenance,
    recording_sha256: str,
) -> str:
    if not outcomes or {o.puzzle_id for o in outcomes} != {o.puzzle_id for o in baseline}:
        raise ValueError("Version comparison requires the same nonempty sample")
    scores = score(outcomes, version="v2")
    previous = score(baseline)
    n = len(outcomes)
    counts = Counter(o.disposition for o in outcomes)
    untrusted = Counter(o.untrusted_code for o in outcomes if o.untrusted_code is not None)
    trusted = ALLOWED_MATE in HISTORICAL_TRUSTED_CODES
    lines = [
        "## Results",
        "",
        "Informational theme agreement only; no coach validation or efficacy claim.",
        "Blind independent coach review remains **PENDING**.",
        "",
        f"V2 pre-registration commit: `{V2_PREREGISTRATION}`.",
        f"M48 allowed-mate trust decision: **{'trusted' if trusted else 'not trusted'}**.",
        "Trust comes solely from the separate M48 isolation report, never these labels.",
        f"Frozen owner-side sample: **{n:,}**. Fast-pass flagged: "
        f"**{n - counts['not_flagged']:,}**.",
        f"Trusted emissions: **{sum(counts[c] for c in V2_CODE_THEMES):,}**; "
        f"abstentions: **{sum(counts[c] for c in ABSTENTIONS):,}**.",
        "",
        "### Per-code agreement",
        "",
        "| Code | Emitted support | Theme-positive rows | Matches | Precision | "
        "Precision Wilson 95% CI | Recall | Recall Wilson 95% CI |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for code, themes in V2_CODE_THEMES.items():
        result = scores[code]
        values = (
            f"{result.support} | {result.theme_positive} | {result.overlap} | "
            f"{_ratio(result.precision)} | {_interval(result.overlap, result.support)} | "
            f"{_ratio(result.recall)} | {_interval(result.overlap, result.theme_positive)}"
            if themes
            else f"{result.support} | N/A | N/A | N/A | N/A | N/A | N/A"
        )
        lines.append(f"| `{code}` | {values} |")
    lines.extend(
        [
            "",
            "N/A is undefined/unmapped, not a measured zero. An untrusted allowed-mate",
            "eligible-only winner is reported below, not counted as a trusted emission.",
            "",
            "### V2 minus v1: unchanged old-code mappings",
            "",
            "| Code | Support delta | Matches delta | Precision delta | Recall delta |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for code, themes in CODE_THEMES.items():
        current, old = scores[code], previous[code]
        values = (
            f"{current.overlap - old.overlap:+d} | "
            f"{_delta(current.precision, old.precision)} | {_delta(current.recall, old.recall)}"
            if themes
            else "N/A | N/A | N/A"
        )
        lines.append(f"| `{code}` | {current.support - old.support:+d} | {values} |")
    lines.extend(
        [
            "",
            "### Disposition and eligible-only distribution",
            "",
            "| Disposition | Count | Rate over full sample |",
            "| --- | ---: | ---: |",
        ]
    )
    for disposition in (*V2_CODE_THEMES, *ABSTENTIONS):
        lines.append(f"| `{disposition}` | {counts[disposition]} | {counts[disposition] / n:.2%} |")
    lines.extend(
        [
            "",
            "| Non-trusted eligible-only winner | Count | Rate over full sample |",
            "| --- | ---: | ---: |",
        ]
    )
    for code in SEED_CODES:
        if code not in HISTORICAL_TRUSTED_CODES:
            lines.append(f"| `{code}` | {untrusted[code]} | {untrusted[code] / n:.2%} |")
    lines.extend(
        [
            "",
            "### Frozen evidence and reproduction",
            "",
            f"- Original recording pre-registration: `{provenance.preregistration_commit}`.",
            f"- Engine: {provenance.engine}; Threads {provenance.threads}, "
            f"Hash {provenance.hash_mb} MiB; {provenance.reset}.",
            f"- Fast nodes {provenance.fast_nodes}; focused nodes {provenance.focused_nodes}, "
            f"MultiPV {provenance.focused_multipv}. No new engine requests.",
            f"- Sample SHA256: `{provenance.sample_sha256}`.",
            f"- Recording SHA256: `{recording_sha256}`.",
            "",
            "```sh",
            "uv run python -m scan64.learning.evaluation.lichess_themes --version v1 --check",
            "uv run python -m scan64.learning.evaluation.lichess_themes --version v2 --check",
            "```",
            "",
            "Both checks are offline and reproduce their respective Results suffixes.",
            "All pre-registered limitations above and in v1 apply. Mate tags do not prove",
            "the absence of prior forced mate or the detector's three-move bound.",
            "No core tuning, label remapping or sample selection followed these results.",
            "",
        ]
    )
    return "\n".join(lines)


def render_v3_results(
    outcomes: list[Outcome],
    baseline_v1: list[Outcome],
    baseline_v2: list[Outcome],
    provenance: RecordingProvenance,
    sources: SourceRecording,
    source_sha256: str,
) -> str:
    ids = {outcome.puzzle_id for outcome in outcomes}
    if not outcomes or any(
        {outcome.puzzle_id for outcome in baseline} != ids
        for baseline in (baseline_v1, baseline_v2)
    ):
        raise ValueError("Version comparison requires the same nonempty sample")
    scores = score(outcomes, version="v3")
    counts = Counter(outcome.disposition for outcome in outcomes)
    untrusted = Counter(
        outcome.untrusted_code for outcome in outcomes if outcome.untrusted_code is not None
    )
    n = len(outcomes)
    trusted = ALLOWED_MATE in TRUSTED_CODES
    lines = [
        "## Results",
        "",
        "Informational theme agreement only; no coach validation or efficacy claim.",
        "Blind independent coach review remains **PENDING**.",
        "",
        f"V3 pre-registration commit: `{V3_PREREGISTRATION}`.",
        f"Fresh M48 allowed-mate trust decision: **{'trusted' if trusted else 'not trusted'}**.",
        "Trust comes solely from the separate post-fix M48 measurement, never these labels.",
        "V1 and v2 below are frozen historical results; v2 is explicitly **pre-fix**.",
        f"Frozen owner-side sample: **{n:,}**. Fast-pass flagged: "
        f"**{n - counts['not_flagged']:,}**.",
        f"Trusted emissions: **{sum(counts[code] for code in V2_CODE_THEMES):,}**; "
        f"abstentions: **{sum(counts[code] for code in ABSTENTIONS):,}**.",
        f"Additional deep source recordings: **{len(sources.puzzles):,}**.",
        "",
        "### Per-code agreement",
        "",
        "| Code | Emitted support | Theme-positive rows | Matches | Precision | "
        "Precision Wilson 95% CI | Recall | Recall Wilson 95% CI |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for code, themes in V2_CODE_THEMES.items():
        result = scores[code]
        values = (
            f"{result.support} | {result.theme_positive} | {result.overlap} | "
            f"{_ratio(result.precision)} | {_interval(result.overlap, result.support)} | "
            f"{_ratio(result.recall)} | {_interval(result.overlap, result.theme_positive)}"
            if themes
            else f"{result.support} | N/A | N/A | N/A | N/A | N/A | N/A"
        )
        lines.append(f"| `{code}` | {values} |")
    lines.extend(
        [
            "",
            "N/A is undefined/unmapped, not a measured zero. Confirmed allowed-mate",
            "evidence suppresses material diagnoses even if allowed mate is untrusted;",
            "an eligible-only winner is not a trusted product emission.",
        ]
    )
    comparisons: tuple[tuple[Version, list[Outcome]], ...] = (
        ("v1", baseline_v1),
        ("v2", baseline_v2),
    )
    for version, baseline in comparisons:
        previous = score(baseline, version=version)
        lines.extend(
            [
                "",
                f"### V3 minus {version}{' (pre-fix)' if version == 'v2' else ''}",
                "",
                "| Code | Support delta | Matches delta | Precision delta | Recall delta |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for code, themes in V2_CODE_THEMES.items():
            if code not in previous:
                lines.append(f"| `{code}` | N/A | N/A | N/A | N/A |")
                continue
            current, old = scores[code], previous[code]
            values = (
                f"{current.overlap - old.overlap:+d} | "
                f"{_delta(current.precision, old.precision)} | "
                f"{_delta(current.recall, old.recall)}"
                if themes
                else "N/A | N/A | N/A"
            )
            lines.append(f"| `{code}` | {current.support - old.support:+d} | {values} |")
    lines.extend(
        [
            "",
            "### Disposition and eligible-only distribution",
            "",
            "| Disposition | Count | Rate over full sample |",
            "| --- | ---: | ---: |",
        ]
    )
    for disposition in (*V2_CODE_THEMES, *ABSTENTIONS):
        lines.append(f"| `{disposition}` | {counts[disposition]} | {counts[disposition] / n:.2%} |")
    lines.extend(
        [
            "",
            "| Non-trusted eligible-only winner | Count | Rate over full sample |",
            "| --- | ---: | ---: |",
        ]
    )
    for code in SEED_CODES:
        if code not in TRUSTED_CODES:
            lines.append(f"| `{code}` | {untrusted[code]} | {untrusted[code] / n:.2%} |")
    lines.extend(
        [
            "",
            "### Frozen evidence and reproduction",
            "",
            f"- Original recording pre-registration: `{provenance.preregistration_commit}`.",
            f"- Source recording pre-registration: `{sources.provenance.preregistration_commit}`.",
            f"- Engine: {sources.provenance.engine}; Threads {sources.provenance.threads}, "
            f"Hash {sources.provenance.hash_mb} MiB; {sources.provenance.reset}.",
            "- Original fast before/after and focused reply requests are unchanged.",
            f"- Added source: {sources.provenance.source_nodes:,} nodes, "
            f"MultiPV {sources.provenance.source_multipv}, only eligible fast mate replies.",
            f"- Sample SHA256: `{sources.provenance.sample_sha256}`.",
            f"- Original recording SHA256: `{sources.provenance.original_recording_sha256}`.",
            f"- `{SOURCE_RECORDING_NAME}` SHA256: `{source_sha256}`.",
            "",
            "```sh",
            "uv run python -m scan64.learning.evaluation.lichess_themes --version v1 --check",
            "uv run python -m scan64.learning.evaluation.lichess_themes --version v2 --check",
            "uv run python -m scan64.learning.evaluation.lichess_themes --version v3 --check",
            "```",
            "",
            "All three commands reproduce their complete Results suffixes offline,",
            "without network, database or live engine access. The separate explicit",
            "recording command is `uv run python scripts/record_lichess_theme_agreement.py",
            "--version v3`; it refuses overwrite and never regenerates original analyses.",
            "",
            "All pre-registered v1/v2/v3 limitations apply. Mate tags cannot establish",
            "the absence of prior forced mate or the three-move bound. These proxy",
            "numbers neither change product trust nor validate instructional efficacy.",
            "No core tuning, mapping expansion or sample changes followed these results.",
            "",
        ]
    )
    return "\n".join(lines)


async def reproduce(directory: Path = FIXTURES, *, version: Version = "v1") -> str:
    code_themes(version)
    puzzles = load_sample(directory)
    payload = (directory / "recorded.json").read_bytes()
    recording_sha256 = hashlib.sha256(payload).hexdigest()
    if recording_sha256 != ORIGINAL_RECORDING_SHA256:
        raise ValueError("Frozen original recording checksum mismatch")
    recording = Recording.model_validate_json(payload)
    if [r.puzzle_id for r in recording.puzzles] != [p.puzzle_id for p in puzzles]:
        raise ValueError("Recording must cover every sample ID exactly once, in source order")
    sources = None
    source_by_id: dict[str, SourcePuzzleRecording] = {}
    source_sha256 = ""
    if version == "v3":
        source_payload = (directory / SOURCE_RECORDING_NAME).read_bytes()
        sources = SourceRecording.model_validate_json(source_payload)
        source_by_id = validate_source_recording(puzzles, recording, sources)
        source_sha256 = hashlib.sha256(source_payload).hexdigest()
    registry = PluginRegistry()
    register_seeded_detectors(registry)

    async def outcomes_for(selected: Version) -> list[Outcome]:
        outcomes = []
        for puzzle, record in zip(puzzles, recording.puzzles, strict=True):
            disposition, untrusted_code = await diagnose(
                puzzle,
                record,
                registry,
                version=selected,
                source=source_by_id.get(puzzle.puzzle_id) if selected == "v3" else None,
            )
            outcomes.append(Outcome(puzzle.puzzle_id, puzzle.themes, disposition, untrusted_code))
        return outcomes

    outcomes = await outcomes_for(version)
    if version == "v1":
        return render_results(outcomes, recording.provenance, recording_sha256)
    baseline = await outcomes_for("v1")
    if version == "v2":
        return render_v2_results(outcomes, baseline, recording.provenance, recording_sha256)
    assert sources is not None
    return render_v3_results(
        outcomes,
        baseline,
        await outcomes_for("v2"),
        recording.provenance,
        sources,
        source_sha256,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the committed Results suffix")
    parser.add_argument("--version", choices=("v1", "v2", "v3"), default="v1")
    args = parser.parse_args()
    try:
        result = asyncio.run(reproduce(version=args.version))
        if args.check:
            document = {
                "v1": REPORT,
                "v2": VERSION_TWO_REPORT,
                "v3": VERSION_THREE_REPORT,
            }[args.version].read_text()
            marker = "## Results\n"
            if marker not in document or document[document.index(marker) :] != result:
                raise ValueError("Committed Results differ from offline reproduction")
        print(result, end="")
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"Theme-agreement reproduction failed: {error}\n")


if __name__ == "__main__":
    main()

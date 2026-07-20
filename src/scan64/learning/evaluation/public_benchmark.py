from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from statistics import fmean
from uuid import uuid4


class PublicBenchmarkError(ValueError):
    """Raised when a public-benchmark export request cannot be evaluated safely."""


class ProvenanceTag(StrEnum):
    """The only two bases on which a record may be published in a public benchmark.

    Per source §25.5: "Only CC0 data and games with explicit contributor consent
    are eligible for any public benchmark or redistributed dataset."
    """

    CC0 = "cc0"
    EXPLICIT_CONSENT = "explicit_consent"


MISSING_PROVENANCE_TAG_REASON = "missing_provenance_tag"


@dataclass(frozen=True)
class BenchmarkSourceRecord:
    """One candidate game considered for public-benchmark export.

    Carries fields (`opponent_identifier`, `move_timings_ms`) that are
    re-identifying per source §24.1 and must never reach a published artifact
    unpseudonymized/unaggregated. This record type is the gating pipeline's
    input only; it is never itself the exported artifact.
    """

    game_id: str
    moves: tuple[str, ...]
    opponent_identifier: str
    move_timings_ms: tuple[int, ...]
    provenance_tag: ProvenanceTag | None
    result: str = "*"


@dataclass(frozen=True)
class RefusedBenchmarkRecord:
    """A tombstone for a candidate record excluded from export, kept for audit."""

    game_id: str
    reason: str


@dataclass(frozen=True)
class BenchmarkGatingReport:
    """The outcome of evaluating candidate records against the §25.5 licensing gate.

    `dry_run` defaults to `True` end-to-end: callers must opt in to `dry_run=False`
    to mark a report as intended for actual publication. The pipeline's behavior is
    identical either way; the flag is truthful metadata a human reviewer or
    downstream publisher checks before any data leaves the local system.
    """

    export_id: str
    dry_run: bool
    evaluated_at: datetime
    included: tuple[BenchmarkSourceRecord, ...]
    refused: tuple[RefusedBenchmarkRecord, ...]

    @property
    def included_count(self) -> int:
        return len(self.included)

    @property
    def refused_count(self) -> int:
        return len(self.refused)


def gate_public_benchmark_records(
    records: Iterable[BenchmarkSourceRecord],
    *,
    dry_run: bool = True,
) -> BenchmarkGatingReport:
    """Evaluate candidate records against the §25.5 CC0/explicit-consent gate.

    Any record lacking a `provenance_tag` is refused and recorded as a tombstone
    rather than silently dropped, so an export run is auditable end to end.
    """
    included: list[BenchmarkSourceRecord] = []
    refused: list[RefusedBenchmarkRecord] = []

    for record in records:
        _require_identifier("game_id", record.game_id)
        if record.provenance_tag is None:
            refused.append(
                RefusedBenchmarkRecord(
                    game_id=record.game_id, reason=MISSING_PROVENANCE_TAG_REASON
                )
            )
            continue
        included.append(record)

    return BenchmarkGatingReport(
        export_id=str(uuid4()),
        dry_run=dry_run,
        evaluated_at=datetime.now(UTC),
        included=tuple(included),
        refused=tuple(refused),
    )


def _require_identifier(name: str, value: str) -> None:
    if not value.strip():
        raise PublicBenchmarkError(f"{name} must not be empty")


# Bucket boundaries (ms) for per-move timing aggregation: exact per-move timings
# are a fingerprinting signal per source §24.1 and must never leave this module
# unaggregated. Boundaries follow common chess time-pressure bands: instant/blitz
# increment reflex, normal deliberation, and long think.
DEFAULT_TIMING_BUCKET_EDGES_MS: tuple[int, ...] = (1_000, 5_000, 15_000, 30_000, 60_000)

_PSEUDONYM_PREFIX = "opp_"
_PSEUDONYM_DIGEST_LENGTH = 16


@dataclass(frozen=True)
class TimingSummary:
    """Aggregated per-game move-timing signal.

    Deliberately holds no raw per-move sequence: only a count, a mean, and a
    histogram over `DEFAULT_TIMING_BUCKET_EDGES_MS`-style buckets survive
    aggregation, per source §24.1's fingerprinting-risk mitigation.
    """

    move_count: int
    mean_move_ms: float
    bucketed_distribution_ms: tuple[int, ...]


@dataclass(frozen=True)
class BenchmarkGame:
    """One pseudonymized, publication-safe game in a public benchmark artifact.

    Has no field carrying a raw opponent identifier or raw per-move timing data;
    `opponent_pseudonym` and `timing_summary` are the only surviving derivatives.
    """

    game_id: str
    moves: tuple[str, ...]
    opponent_pseudonym: str
    timing_summary: TimingSummary
    provenance_tag: ProvenanceTag
    result: str


@dataclass(frozen=True)
class PublicBenchmarkArtifact:
    """The final, publication-safe output of the public-benchmark export pipeline."""

    export_id: str
    dry_run: bool
    built_at: datetime
    games: tuple[BenchmarkGame, ...]
    refused: tuple[RefusedBenchmarkRecord, ...]

    @property
    def game_count(self) -> int:
        return len(self.games)


def build_public_benchmark_artifact(
    report: BenchmarkGatingReport,
    *,
    salt: str,
    timing_bucket_edges_ms: Sequence[int] = DEFAULT_TIMING_BUCKET_EDGES_MS,
) -> PublicBenchmarkArtifact:
    """Pseudonymize and aggregate a gating report's included records for publication.

    `salt` must be an operator-provided secret (never hardcoded) so pseudonyms are
    not reversible by anyone without it. The same `salt` must be reused across an
    export run for the same opponent identifier to map to the same pseudonym.
    """
    _require_identifier("salt", salt)

    games = tuple(
        _pseudonymize_record(record, salt=salt, bucket_edges=timing_bucket_edges_ms)
        for record in report.included
    )

    return PublicBenchmarkArtifact(
        export_id=report.export_id,
        dry_run=report.dry_run,
        built_at=datetime.now(UTC),
        games=games,
        refused=report.refused,
    )


def export_public_benchmark(
    records: Iterable[BenchmarkSourceRecord],
    *,
    salt: str,
    dry_run: bool = True,
    timing_bucket_edges_ms: Sequence[int] = DEFAULT_TIMING_BUCKET_EDGES_MS,
) -> PublicBenchmarkArtifact:
    """Gate then pseudonymize/aggregate candidate records into a publishable artifact."""
    report = gate_public_benchmark_records(records, dry_run=dry_run)
    return build_public_benchmark_artifact(
        report, salt=salt, timing_bucket_edges_ms=timing_bucket_edges_ms
    )


def _pseudonymize_record(
    record: BenchmarkSourceRecord,
    *,
    salt: str,
    bucket_edges: Sequence[int],
) -> BenchmarkGame:
    if record.provenance_tag is None:
        raise PublicBenchmarkError(
            f"record {record.game_id} lacks a provenance_tag and must not reach "
            "pseudonymization; gate_public_benchmark_records should have refused it"
        )
    return BenchmarkGame(
        game_id=record.game_id,
        moves=record.moves,
        opponent_pseudonym=_pseudonymize_opponent(record.opponent_identifier, salt=salt),
        timing_summary=_summarize_timing(record.move_timings_ms, bucket_edges=bucket_edges),
        provenance_tag=record.provenance_tag,
        result=record.result,
    )


def _pseudonymize_opponent(identifier: str, *, salt: str) -> str:
    _require_identifier("opponent_identifier", identifier)
    digest = sha256(f"{salt}:{identifier}".encode()).hexdigest()
    return f"{_PSEUDONYM_PREFIX}{digest[:_PSEUDONYM_DIGEST_LENGTH]}"


def _summarize_timing(
    move_timings_ms: tuple[int, ...],
    *,
    bucket_edges: Sequence[int],
) -> TimingSummary:
    bucket_counts = [0] * (len(bucket_edges) + 1)
    for timing_ms in move_timings_ms:
        bucket_counts[_bucket_index(timing_ms, bucket_edges)] += 1

    return TimingSummary(
        move_count=len(move_timings_ms),
        mean_move_ms=fmean(move_timings_ms) if move_timings_ms else 0.0,
        bucketed_distribution_ms=tuple(bucket_counts),
    )


def _bucket_index(timing_ms: int, bucket_edges: Sequence[int]) -> int:
    for index, edge in enumerate(bucket_edges):
        if timing_ms < edge:
            return index
    return len(bucket_edges)

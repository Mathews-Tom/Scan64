from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
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

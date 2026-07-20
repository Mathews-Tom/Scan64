from __future__ import annotations

import pytest

from scan64.learning.evaluation.public_benchmark import (
    MISSING_PROVENANCE_TAG_REASON,
    BenchmarkSourceRecord,
    ProvenanceTag,
    PublicBenchmarkError,
    gate_public_benchmark_records,
)


def _record(
    game_id: str,
    provenance_tag: ProvenanceTag | None,
) -> BenchmarkSourceRecord:
    return BenchmarkSourceRecord(
        game_id=game_id,
        moves=("e4", "e5", "Nf3"),
        opponent_identifier="real_opponent_username",
        move_timings_ms=(1200, 800, 4300),
        provenance_tag=provenance_tag,
    )


def test_record_lacking_provenance_tag_is_refused_not_exported() -> None:
    records = [_record("game-no-tag", provenance_tag=None)]

    report = gate_public_benchmark_records(records)

    assert report.included == ()
    assert report.included_count == 0
    assert report.refused_count == 1
    assert report.refused[0].game_id == "game-no-tag"
    assert report.refused[0].reason == MISSING_PROVENANCE_TAG_REASON


def test_cc0_and_explicit_consent_records_are_included() -> None:
    records = [
        _record("game-cc0", provenance_tag=ProvenanceTag.CC0),
        _record("game-consent", provenance_tag=ProvenanceTag.EXPLICIT_CONSENT),
    ]

    report = gate_public_benchmark_records(records)

    included_ids = {record.game_id for record in report.included}
    assert included_ids == {"game-cc0", "game-consent"}
    assert report.refused == ()


def test_mixed_batch_partitions_included_and_refused_independently() -> None:
    records = [
        _record("game-cc0", provenance_tag=ProvenanceTag.CC0),
        _record("game-no-tag-1", provenance_tag=None),
        _record("game-consent", provenance_tag=ProvenanceTag.EXPLICIT_CONSENT),
        _record("game-no-tag-2", provenance_tag=None),
    ]

    report = gate_public_benchmark_records(records)

    assert {r.game_id for r in report.included} == {"game-cc0", "game-consent"}
    assert {r.game_id for r in report.refused} == {"game-no-tag-1", "game-no-tag-2"}
    assert all(r.reason == MISSING_PROVENANCE_TAG_REASON for r in report.refused)


def test_dry_run_defaults_true_and_is_propagated_as_metadata() -> None:
    default_report = gate_public_benchmark_records([_record("g1", ProvenanceTag.CC0)])
    assert default_report.dry_run is True

    live_report = gate_public_benchmark_records(
        [_record("g2", ProvenanceTag.CC0)], dry_run=False
    )
    assert live_report.dry_run is False
    # dry_run only annotates the report; gating behavior is identical either way.
    assert live_report.included_count == 1


def test_empty_game_id_is_rejected() -> None:
    with pytest.raises(PublicBenchmarkError, match="game_id must not be empty"):
        gate_public_benchmark_records([_record("   ", ProvenanceTag.CC0)])


def test_refused_records_are_never_silently_dropped() -> None:
    """Every refusal must leave a tombstone: refused + included counts sum to input size."""
    records = [
        _record("g1", ProvenanceTag.CC0),
        _record("g2", None),
        _record("g3", ProvenanceTag.EXPLICIT_CONSENT),
        _record("g4", None),
        _record("g5", None),
    ]

    report = gate_public_benchmark_records(records)

    assert report.included_count + report.refused_count == len(records)

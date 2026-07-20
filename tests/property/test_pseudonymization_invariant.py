from __future__ import annotations

import dataclasses

from hypothesis import given
from hypothesis import strategies as st

from scan64.learning.evaluation.public_benchmark import (
    BenchmarkGame,
    BenchmarkSourceRecord,
    ProvenanceTag,
    build_public_benchmark_artifact,
    gate_public_benchmark_records,
)

_SALT = "test-export-salt-do-not-reuse-in-production"

_benchmark_game_field_names = {field.name for field in dataclasses.fields(BenchmarkGame)}


@st.composite
def source_records(draw: st.DrawFn) -> BenchmarkSourceRecord:
    game_id = draw(st.uuids().map(str))
    opponent_identifier = draw(st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != ""))
    move_timings_ms = draw(
        st.lists(st.integers(min_value=0, max_value=180_000), min_size=0, max_size=60).map(tuple)
    )
    provenance_tag = draw(st.sampled_from([ProvenanceTag.CC0, ProvenanceTag.EXPLICIT_CONSENT]))
    return BenchmarkSourceRecord(
        game_id=game_id,
        moves=("e4", "e5"),
        opponent_identifier=opponent_identifier,
        move_timings_ms=move_timings_ms,
        provenance_tag=provenance_tag,
    )


def test_benchmark_game_has_no_raw_identifier_or_timing_field() -> None:
    """Structural guarantee: the published record type cannot carry raw PII fields."""
    assert "opponent_identifier" not in _benchmark_game_field_names
    assert "move_timings_ms" not in _benchmark_game_field_names
    assert _benchmark_game_field_names == {
        "game_id",
        "moves",
        "opponent_pseudonym",
        "timing_summary",
        "provenance_tag",
        "result",
    }


@given(record=source_records())
def test_opponent_identifier_never_appears_unpseudonymized_in_export(
    record: BenchmarkSourceRecord,
) -> None:
    report = gate_public_benchmark_records([record])
    artifact = build_public_benchmark_artifact(report, salt=_SALT)

    game = artifact.games[0]

    assert game.opponent_pseudonym != record.opponent_identifier
    assert game.opponent_pseudonym.startswith("opp_")


@given(record=source_records())
def test_timing_data_never_appears_unaggregated_in_export(
    record: BenchmarkSourceRecord,
) -> None:
    report = gate_public_benchmark_records([record])
    artifact = build_public_benchmark_artifact(report, salt=_SALT)

    summary = artifact.games[0].timing_summary

    # The raw per-move sequence must not survive as a distinguishable field: the
    # summary is a fixed-shape aggregate (count, mean, bucket histogram), not a
    # per-move-length sequence.
    assert summary.move_count == len(record.move_timings_ms)
    assert sum(summary.bucketed_distribution_ms) == len(record.move_timings_ms)
    if record.move_timings_ms:
        expected_mean = sum(record.move_timings_ms) / len(record.move_timings_ms)
        assert summary.mean_move_ms == expected_mean


@given(identifier=st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != ""))
def test_pseudonymization_is_deterministic_for_same_identifier_and_salt(
    identifier: str,
) -> None:
    record_a = BenchmarkSourceRecord(
        game_id="game-a",
        moves=(),
        opponent_identifier=identifier,
        move_timings_ms=(),
        provenance_tag=ProvenanceTag.CC0,
    )
    record_b = BenchmarkSourceRecord(
        game_id="game-b",
        moves=(),
        opponent_identifier=identifier,
        move_timings_ms=(),
        provenance_tag=ProvenanceTag.CC0,
    )

    artifact = build_public_benchmark_artifact(
        gate_public_benchmark_records([record_a, record_b]), salt=_SALT
    )

    pseudonym_a, pseudonym_b = (game.opponent_pseudonym for game in artifact.games)
    assert pseudonym_a == pseudonym_b


@given(identifier=st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != ""))
def test_pseudonymization_differs_across_salts(identifier: str) -> None:
    record = BenchmarkSourceRecord(
        game_id="game",
        moves=(),
        opponent_identifier=identifier,
        move_timings_ms=(),
        provenance_tag=ProvenanceTag.CC0,
    )

    artifact_one = build_public_benchmark_artifact(
        gate_public_benchmark_records([record]), salt="salt-one"
    )
    artifact_two = build_public_benchmark_artifact(
        gate_public_benchmark_records([record]), salt="salt-two"
    )

    assert artifact_one.games[0].opponent_pseudonym != artifact_two.games[0].opponent_pseudonym


def test_refused_records_never_reach_pseudonymization() -> None:
    refused_record = BenchmarkSourceRecord(
        game_id="game-no-tag",
        moves=("e4",),
        opponent_identifier="raw_username",
        move_timings_ms=(1_000,),
        provenance_tag=None,
    )

    report = gate_public_benchmark_records([refused_record])
    artifact = build_public_benchmark_artifact(report, salt=_SALT)

    assert artifact.games == ()
    assert artifact.refused[0].game_id == "game-no-tag"

"""The factor thresholds and health scale are the fit on the committed calibration data (#410)."""

from __future__ import annotations

import chess
import pytest

from benchmarks.factor_calibration.calibrate import (
    HealthSample,
    _game_quiet,
    choose_quantile,
    fit_health,
    fitted_config,
    health_samples,
    measure_all,
    separation,
)
from scan64.explanations.factor_facts import (
    FACTOR_CONFIG,
    HEALTH_SCALE,
    HealthScale,
    evaluation_without_tempo,
    health_values,
)
from scan64.explanations.factors import evaluate_factors


def test_factor_config_is_the_committed_fit() -> None:
    measured = measure_all()
    assert {m.set for m in measured} == {"benchmark", "caissa", "slow", "null"}
    assert sum(m.set == "slow" for m in measured) >= 30
    fit = choose_quantile(measured)
    config = fitted_config(fit)
    assert dict(config.thresholds) == dict(FACTOR_CONFIG.thresholds)
    assert dict(config.overrides) == dict(FACTOR_CONFIG.overrides)
    # Mistakes get a fact more often than null moves.
    assert separation(measured, FACTOR_CONFIG) > 0.2


def test_health_scale_is_the_committed_fit() -> None:
    measured = measure_all(("slow", "null"))
    samples = health_samples(measured)
    labels = {s.label for s in samples}
    assert labels == {"healthy", "trouble"}
    assert {s.source for s in samples} >= {"game", "null played", "slow played"}
    assert fit_health(samples) == dict(HEALTH_SCALE)


def test_health_samples_keep_only_quiet_null_roots() -> None:
    measured = measure_all(("null",))
    quiet_roots = [
        m
        for m in measured
        if _game_quiet(chess.Board(m.fen), chess.Move.from_uci(m.points["played"][0]))
    ]
    # 7 of the 40 roots: the played move captures or the mover is in check.
    assert (len(measured), len(quiet_roots)) == (40, 33)
    samples = health_samples(measured)
    assert sum(s.source == "null before" for s in samples) == len(quiet_roots)


# curated-rule50_high (tests/fixtures/factors/positions.json), each side to move.
RULE50_HIGH = "8/8/4k3/8/2R5/8/4K3/2r5 {} - - 87 120"


def test_overall_does_not_depend_on_the_side_to_move_near_the_fifty_move_limit() -> None:
    white = evaluate_factors(RULE50_HIGH.format("w"))
    black = evaluate_factors(RULE50_HIGH.format("b"))
    assert white.rule50 == black.rule50 == 87
    # Tempo is added before the fifty-move scaling: subtracting it afterwards
    # leaves a turn-dependent part.
    assert white.evaluation - white.tempo != black.evaluation - black.tempo
    assert evaluation_without_tempo(white) == evaluation_without_tempo(black)
    for side in (chess.WHITE, chess.BLACK):
        assert health_values(white, side)["Overall"] == health_values(black, side)["Overall"]


def test_evaluation_without_tempo_is_evaluation_minus_tempo_without_the_clock() -> None:
    for fen in (
        chess.STARTING_FEN,
        "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4",
        "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 0 4",
        RULE50_HIGH.replace(" 87 ", " 0 ").format("w"),
    ):
        report = evaluate_factors(fen)
        assert evaluation_without_tempo(report) == report.evaluation - report.tempo, fen


def test_health_scale_reads_the_start_between_empty_and_full() -> None:
    values = health_values(evaluate_factors(chess.STARTING_FEN), chess.WHITE)
    assert set(values) == set(HEALTH_SCALE)
    for name, scale in HEALTH_SCALE.items():
        assert values[name] == pytest.approx(0.0)
        assert scale.empty < 0 < scale.full, name
        assert 0 < scale.health(values[name]) < 1, name
        assert scale.health(scale.full) == 1.0
        assert scale.health(scale.empty) == 0.0
        assert scale.health(scale.full + 5) == 1.0
        assert scale.health(scale.empty - 5) == 0.0


def test_health_values_mirror_between_sides() -> None:
    board = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    report = evaluate_factors(board)
    white = health_values(report, chess.WHITE)
    black = health_values(report, chess.BLACK)
    assert white == {name: -value for name, value in black.items()}


def test_health_fit_rounds_outward_with_a_minimum() -> None:
    def sample(label: str, value: float) -> HealthSample:
        return HealthSample(label, "test", True, dict.fromkeys(HEALTH_SCALE, value))  # type: ignore[arg-type]

    flat = [sample("healthy", 0.0) for _ in range(5)] + [sample("trouble", 0.0) for _ in range(5)]
    assert set(fit_health(flat).values()) == {HealthScale(full=0.10, empty=-0.10)}
    spread = [sample("healthy", v) for v in (0.0, 0.1, 0.2, 0.3, 0.42)] + [
        sample("trouble", v) for v in (-1.01, -0.6, -0.2, 0.0, 0.1)
    ]
    assert set(fit_health(spread).values()) == {HealthScale(full=0.35, empty=-0.70)}

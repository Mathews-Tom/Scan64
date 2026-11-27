"""The factor thresholds and health scale are the fit on the committed calibration data (#410)."""

from __future__ import annotations

import chess
import pytest

from benchmarks.factor_calibration.calibrate import (
    HealthSample,
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

"""The factor thresholds are the fit on the committed calibration positions (#410)."""

from __future__ import annotations

from benchmarks.factor_calibration.calibrate import (
    choose_quantile,
    fitted_config,
    measure_all,
    separation,
)
from scan64.explanations.factor_facts import FACTOR_CONFIG


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

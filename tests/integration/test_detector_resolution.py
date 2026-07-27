from __future__ import annotations

import pytest

import scan64.chess.analysis.jobs as jobs
from scan64.learning.diagnosis.detectors.registration import register_seeded_detectors
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.plugins.interfaces import PatternDetector
from scan64.learning.plugins.registry import PluginRegistry


def test_job_resolves_every_seeded_detector_from_the_host_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = PluginRegistry()
    register_seeded_detectors(registry)
    monkeypatch.setattr(jobs, "get_host_registry", lambda: registry)

    detectors = jobs._resolve_pattern_detectors()

    assert len(detectors) == len(SEED_CODES)
    assert all(isinstance(detector, PatternDetector) for detector in detectors)


def test_job_rejects_an_empty_host_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "get_host_registry", PluginRegistry)

    with pytest.raises(RuntimeError, match="has no pattern detectors"):
        jobs._resolve_pattern_detectors()

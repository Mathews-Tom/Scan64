from scan64.learning.diagnosis.detectors.board_awareness import HangingPieceDetector
from scan64.learning.diagnosis.detectors.calculation import StoppedCalculationEarlyDetector
from scan64.learning.diagnosis.detectors.opening import DelayedDevelopmentDetector
from scan64.learning.diagnosis.detectors.positional import KingSafetyNeglectDetector
from scan64.learning.diagnosis.detectors.tactics import (
    KnightForkDetector,
    OverloadedDefenderDetector,
    PinDetector,
)
from scan64.learning.diagnosis.detectors.threat_processing import (
    MissedCaptureDetector,
    MissedCheckDetector,
    MissedDirectThreatDetector,
)
from scan64.learning.diagnosis.taxonomy.seeds import SEED_CODES
from scan64.learning.plugins.registry import PluginKind, PluginRegistry


def register_seeded_detectors(registry: PluginRegistry) -> None:
    detectors = {
        "board_awareness.hanging_piece": HangingPieceDetector(),
        "threat_processing.missed_check": MissedCheckDetector(),
        "threat_processing.missed_capture": MissedCaptureDetector(),
        "threat_processing.missed_direct_threat": MissedDirectThreatDetector(),
        "tactics.fork.knight": KnightForkDetector(),
        "tactics.pin": PinDetector(),
        "tactics.overloaded_defender": OverloadedDefenderDetector(),
        "calculation.stopped_too_early": StoppedCalculationEarlyDetector(),
        "opening.delayed_development": DelayedDevelopmentDetector(),
        "positional.king_safety_neglect": KingSafetyNeglectDetector(),
    }
    if tuple(detectors) != tuple(SEED_CODES):
        raise RuntimeError("Seeded detector registration diverges from the taxonomy")
    for name, detector in detectors.items():
        registry.register(kind=PluginKind.PATTERN_DETECTOR, name=name, plugin=detector)

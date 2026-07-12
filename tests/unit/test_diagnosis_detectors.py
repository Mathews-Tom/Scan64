import pytest

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
from scan64.learning.diagnosis.models import LearningOpportunity, PlayerContext
from scan64.learning.evidence.models import Evidence


@pytest.mark.asyncio
async def test_hanging_piece_detector():
    detector = HangingPieceDetector()
    opp = LearningOpportunity(
        opportunity_id="opp_1",
        position_id="pos_1",
        player_id="player_1",
        played_move="e4",
        engine_eval_before=3.0,
        engine_eval_after=0.5,
    )
    ctx = PlayerContext(player_id="player_1")
    ev = Evidence(
        evidence_id="ev_1",
        kind="blunder_analysis",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="Player left a piece hanging",
        payload={"is_hanging_piece_blunder": True},
    )

    candidates = await detector.detect(opp, [ev], ctx)
    assert len(candidates) == 1
    assert candidates[0].skill_id == "board_awareness.hanging_piece"
    assert candidates[0].confidence == 1.0


@pytest.mark.asyncio
async def test_missed_check_detector():
    detector = MissedCheckDetector()
    opp = LearningOpportunity(
        opportunity_id="opp_1",
        position_id="pos_1",
        player_id="player_1",
        played_move="e4",
        engine_eval_before=1.0,
        engine_eval_after=0.0,
    )
    ctx = PlayerContext(player_id="player_1")
    ev = Evidence(
        evidence_id="ev_1",
        kind="missed_opportunity",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="Player missed a check",
        payload={"is_missed_check": True, "was_unique_best": True},
    )

    candidates = await detector.detect(opp, [ev], ctx)
    assert len(candidates) == 1
    assert candidates[0].skill_id == "threat_processing.missed_check"
    assert candidates[0].confidence == 1.0


@pytest.mark.asyncio
async def test_missed_capture_detector():
    detector = MissedCaptureDetector()
    opp = LearningOpportunity(
        opportunity_id="opp_1",
        position_id="pos_1",
        player_id="player_1",
        played_move="e4",
        engine_eval_before=2.0,
        engine_eval_after=0.0,
    )
    ctx = PlayerContext(player_id="player_1")
    ev = Evidence(
        evidence_id="ev_1",
        kind="missed_opportunity",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="Player missed a free piece",
        payload={"is_missed_capture": True, "was_only_winning_line": True},
    )

    candidates = await detector.detect(opp, [ev], ctx)
    assert len(candidates) == 1
    assert candidates[0].skill_id == "threat_processing.missed_capture"
    assert candidates[0].confidence == 1.0


@pytest.mark.asyncio
async def test_missed_direct_threat_detector():
    detector = MissedDirectThreatDetector()
    opp = LearningOpportunity(
        opportunity_id="opp_1",
        position_id="pos_1",
        player_id="player_1",
        played_move="e4",
        engine_eval_before=1.0,
        engine_eval_after=-3.0,
    )
    ctx = PlayerContext(player_id="player_1")
    ev = Evidence(
        evidence_id="ev_1",
        kind="blunder_analysis",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="Player missed a direct threat",
        payload={"is_missed_direct_threat": True, "opponent_executed_threat": True},
    )
    candidates = await detector.detect(opp, [ev], ctx)
    assert len(candidates) == 1
    assert candidates[0].skill_id == "threat_processing.missed_direct_threat"
    assert candidates[0].confidence == 1.0


@pytest.mark.asyncio
async def test_knight_fork_detector():
    detector = KnightForkDetector()
    opp = LearningOpportunity(
        opportunity_id="opp_1",
        position_id="pos_1",
        player_id="player_1",
        played_move="e4",
        engine_eval_before=2.0,
        engine_eval_after=0.0,
    )
    ctx = PlayerContext(player_id="player_1")
    ev = Evidence(
        evidence_id="ev_1",
        kind="missed_tactic",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="Player missed a knight fork",
        payload={"tactic_type": "knight_fork", "results_in_material_gain": True},
    )
    candidates = await detector.detect(opp, [ev], ctx)
    assert len(candidates) == 1
    assert candidates[0].skill_id == "tactics.fork.knight"
    assert candidates[0].confidence == 1.0


@pytest.mark.asyncio
async def test_pin_detector():
    detector = PinDetector()
    opp = LearningOpportunity(
        opportunity_id="opp_1",
        position_id="pos_1",
        player_id="player_1",
        played_move="e4",
        engine_eval_before=2.0,
        engine_eval_after=0.0,
    )
    ctx = PlayerContext(player_id="player_1")
    ev = Evidence(
        evidence_id="ev_1",
        kind="missed_tactic",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="Player missed a pin",
        payload={"tactic_type": "pin", "wins_material": True},
    )
    candidates = await detector.detect(opp, [ev], ctx)
    assert len(candidates) == 1
    assert candidates[0].skill_id == "tactics.pin"
    assert candidates[0].confidence == 1.0


@pytest.mark.asyncio
async def test_overloaded_defender_detector():
    detector = OverloadedDefenderDetector()
    opp = LearningOpportunity(
        opportunity_id="opp_1",
        position_id="pos_1",
        player_id="player_1",
        played_move="e4",
        engine_eval_before=2.0,
        engine_eval_after=0.0,
    )
    ctx = PlayerContext(player_id="player_1")
    ev = Evidence(
        evidence_id="ev_1",
        kind="missed_tactic",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="Player missed overloaded defender",
        payload={"tactic_type": "overloaded_defender", "wins_material": True},
    )
    candidates = await detector.detect(opp, [ev], ctx)
    assert len(candidates) == 1
    assert candidates[0].skill_id == "tactics.overloaded_defender"
    assert candidates[0].confidence == 1.0


@pytest.mark.asyncio
async def test_stopped_calculation_early_detector():
    detector = StoppedCalculationEarlyDetector()
    opp = LearningOpportunity(
        opportunity_id="opp_1",
        position_id="pos_1",
        player_id="player_1",
        played_move="e4",
        engine_eval_before=1.0,
        engine_eval_after=-2.0,
    )
    ctx = PlayerContext(player_id="player_1")
    ev = Evidence(
        evidence_id="ev_1",
        kind="calculation_error",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="Player stopped calculation too early",
        payload={"error_type": "stopped_early", "sequence_plies": 4, "sharp_eval_swing": True},
    )
    candidates = await detector.detect(opp, [ev], ctx)
    assert len(candidates) == 1
    assert candidates[0].skill_id == "calculation.stopped_too_early"
    assert candidates[0].confidence == 0.8


@pytest.mark.asyncio
async def test_delayed_development_detector():
    detector = DelayedDevelopmentDetector()
    opp = LearningOpportunity(
        opportunity_id="opp_1",
        position_id="pos_1",
        player_id="player_1",
        played_move="e4",
        engine_eval_before=0.5,
        engine_eval_after=-1.0,
    )
    ctx = PlayerContext(player_id="player_1")
    ev = Evidence(
        evidence_id="ev_1",
        kind="opening_analysis",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="Player delayed development",
        payload={"issue": "delayed_development", "tempo_loss": 2.0},
    )
    candidates = await detector.detect(opp, [ev], ctx)
    assert len(candidates) == 1
    assert candidates[0].skill_id == "opening.delayed_development"
    assert candidates[0].confidence == 0.9


@pytest.mark.asyncio
async def test_king_safety_neglect_detector():
    detector = KingSafetyNeglectDetector()
    opp = LearningOpportunity(
        opportunity_id="opp_1",
        position_id="pos_1",
        player_id="player_1",
        played_move="e4",
        engine_eval_before=1.0,
        engine_eval_after=-2.0,
    )
    ctx = PlayerContext(player_id="player_1")
    ev = Evidence(
        evidence_id="ev_1",
        kind="positional_analysis",
        position_id="pos_1",
        engine_analysis_id="ea_1",
        claim="Player neglected king safety",
        payload={"issue": "king_safety_neglect", "incoming_threat": True},
    )
    candidates = await detector.detect(opp, [ev], ctx)
    assert len(candidates) == 1
    assert candidates[0].skill_id == "positional.king_safety_neglect"
    assert candidates[0].confidence == 0.85

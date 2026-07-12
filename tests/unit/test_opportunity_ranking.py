import math

from scan64.learning.diagnosis.models import DiagnosisCandidate
from scan64.learning.diagnosis.ranking import OpportunityRanker, RankingFactors


def test_ranker_log_additive_scoring():
    ranker = OpportunityRanker(epsilon=0.01)
    factors = RankingFactors(
        severity=2.0,
        teachability=1.5,
        recurrence=1.2,
        confidence=0.9,
        transfer_value=1.1,
        readiness=1.0,
        redundancy=0.5,
        cognitive_overload=0.2,
    )
    score = ranker.calculate_score(factors)
    expected_score = (
        math.log(2.0)
        + math.log(1.5)
        + math.log(1.2)
        + math.log(0.9)
        + math.log(1.1)
        + math.log(1.0)
        - 0.5
        - 0.2
    )
    assert math.isclose(score, expected_score, rel_tol=1e-5)


def test_ranker_epsilon_floor():
    ranker = OpportunityRanker(epsilon=0.01)
    factors = RankingFactors(
        severity=0.0,
        teachability=0.0,
        recurrence=0.0,
        confidence=0.0,
        transfer_value=0.0,
        readiness=0.0,
    )
    score = ranker.calculate_score(factors)
    # log(0.01) = -4.60517
    expected_score = 6 * math.log(0.01)
    assert math.isclose(score, expected_score, rel_tol=1e-5)


def test_overcoaching_cap():
    ranker = OpportunityRanker(overcoaching_cap=2)

    candidates = []
    for i in range(5):
        cand = DiagnosisCandidate(skill_id=f"skill_{i}", confidence=1.0, evidence_ids=[])
        # Give higher severity to lower index to ensure sorting order
        factors = RankingFactors(severity=10.0 - i)
        candidates.append((cand, factors))

    ranked = ranker.rank(candidates)

    assert len(ranked) == 2
    assert ranked[0].candidate.skill_id == "skill_0"
    assert ranked[1].candidate.skill_id == "skill_1"

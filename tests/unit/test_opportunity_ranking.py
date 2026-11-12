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


def test_ranker_recomputes_skill_redundancy_after_each_choice() -> None:
    first = DiagnosisCandidate(skill_id="repeat", confidence=1.0, evidence_ids=[])
    repeated = DiagnosisCandidate(skill_id="repeat", confidence=1.0, evidence_ids=[])
    distinct = DiagnosisCandidate(skill_id="other", confidence=1.0, evidence_ids=[])
    factors = [RankingFactors(severity=value) for value in (1000, 900, 500)]
    ranked = OpportunityRanker(overcoaching_cap=3).rank(
        list(zip([first, repeated, distinct], factors, strict=True))
    )
    assert [item.candidate for item in ranked] == [first, distinct, repeated]
    assert ranked[-1].factors.redundancy == 1.0
    assert all(factor.redundancy == 0 for factor in factors)


def test_ranker_weights_severity_by_diagnostic_confidence() -> None:
    uncertain = DiagnosisCandidate(skill_id="uncertain", confidence=0.1, evidence_ids=[])
    certain = DiagnosisCandidate(skill_id="certain", confidence=0.9, evidence_ids=[])
    ranked = OpportunityRanker().rank(
        [
            (uncertain, RankingFactors(severity=1000, confidence=uncertain.confidence)),
            (certain, RankingFactors(severity=500, confidence=certain.confidence)),
        ]
    )
    assert ranked[0].candidate is certain

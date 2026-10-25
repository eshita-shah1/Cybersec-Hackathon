from __future__ import annotations

import pytest

from app.config import Settings
from app.detection.policy_engine import PolicyResult
from app.risk.context_risk import ContextResult
from app.risk.response_engine import ResponseEngine
from app.risk.risk_scorer import RiskScorer, round_score
from app.schemas.common import Decision, DetectorStatus, PolicyStatus, RiskLevel, Severity, SignalResult

S = Settings(_env_file=None)
SCORER = RiskScorer(S)
ENGINE = ResponseEngine(S)


def ok(score: float) -> SignalResult:
    return SignalResult(detector="x", status=DetectorStatus.OK, score=score)


MISSING = SignalResult(detector="x", status=DetectorStatus.INSUFFICIENT_DATA)


def ctx(high_impact: bool = False) -> ContextResult:
    return ContextResult(score=10, factors={}, weights={}, high_impact=high_impact, explanation="", details={})


def policy(status: PolicyStatus, sev: Severity = Severity.NONE) -> PolicyResult:
    return PolicyResult(status=status, severity=sev, severity_score=sev.score, explanation=f"{status.value} test",
                        enforcement_recommendation="allow")


def test_weights_match_specification():
    assert SCORER.weights == {"policy": 0.40, "behavior": 0.25, "semantic": 0.20, "context": 0.15}
    assert SCORER.score(100, 0, 0, 0).score == 40.0
    assert SCORER.score(0, 100, 0, 0).score == 25.0
    assert SCORER.score(0, 0, 100, 0).score == 20.0
    assert SCORER.score(0, 0, 0, 100).score == 15.0
    assert SCORER.score(50, 40, 30, 20).score == round_score(0.4 * 50 + 0.25 * 40 + 0.2 * 30 + 0.15 * 20)


@pytest.mark.parametrize("vals", [(0, 0, 0, 0), (100, 100, 100, 100), (100, None, None, 100), (0, None, 0, 0)])
def test_score_within_bounds(vals):
    r = SCORER.score(*vals)
    assert 0 <= r.score <= 100


def test_out_of_range_signal_rejected():
    with pytest.raises(ValueError):
        SCORER.score(120, 0, 0, 0)


def test_missing_signals_are_renormalized_not_fabricated():
    r = SCORER.score(100, None, None, 0)
    assert r.available == {"policy": True, "behavior": False, "semantic": False, "context": True}
    assert r.coverage == pytest.approx(0.55)
    assert r.renormalized
    assert r.score == round_score(100 * 0.40 / 0.55)
    assert r.signals["behavior"] is None and r.contributions["behavior"] == 0


@pytest.mark.parametrize("score,decision,level", [
    (24.9, Decision.ALLOW, RiskLevel.LOW), (25, Decision.MONITOR, RiskLevel.MEDIUM),
    (49.9, Decision.MONITOR, RiskLevel.MEDIUM), (50, Decision.REVIEW, RiskLevel.HIGH),
    (74.9, Decision.REVIEW, RiskLevel.HIGH), (75, Decision.BLOCK, RiskLevel.CRITICAL)])
def test_thresholds(score, decision, level):
    assert SCORER.response_for(score) == decision
    assert SCORER.level_for(score) == level


def test_thresholds_are_configurable():
    s = Settings(_env_file=None, risk_monitor_threshold=10, risk_review_threshold=20, risk_block_threshold=30)
    sc = RiskScorer(s)
    assert sc.response_for(15) == Decision.MONITOR and sc.response_for(30) == Decision.BLOCK


def test_rounding_is_half_up():
    assert round_score(12.25) == 12.3 and round_score(12.24) == 12.2


def test_policy_denial_takes_precedence_over_low_score():
    risk = SCORER.score(0, 0, 0, 0)  # artificially low score
    d = ENGINE.decide(policy(PolicyStatus.DENY, Severity.CRITICAL), risk, ok(0), ok(0), ctx(), 0)
    assert risk.recommended_response == Decision.ALLOW
    assert d.decision == Decision.BLOCK and "policy_denial" in d.overrides


def test_restricted_requires_review():
    risk = SCORER.score(50, 0, 0, 0)
    d = ENGINE.decide(policy(PolicyStatus.RESTRICTED, Severity.MEDIUM), risk, ok(0), ok(0), ctx(), 1)
    assert d.decision == Decision.REVIEW


def test_anomaly_raises_to_monitor_and_sensitive_anomaly_to_review():
    risk = SCORER.score(0, 55, 0, 0)
    assert ENGINE.decide(policy(PolicyStatus.ALLOW), risk, ok(55), ok(0), ctx(), 1).decision == Decision.MONITOR
    risk = SCORER.score(0, 80, 0, 10)
    assert ENGINE.decide(policy(PolicyStatus.ALLOW), risk, ok(80), ok(0), ctx(True), 2).decision == Decision.REVIEW


def test_fail_closed_for_high_impact_with_missing_detector():
    risk = SCORER.score(0, None, 0, 30)
    d = ENGINE.decide(policy(PolicyStatus.ALLOW), risk, MISSING, ok(0), ctx(high_impact=True), 2)
    assert d.decision == Decision.REVIEW and "fail_closed_missing_signals" in d.overrides
    # low-impact action with missing history is not held
    d2 = ENGINE.decide(policy(PolicyStatus.ALLOW), risk, MISSING, ok(0), ctx(high_impact=False), 0)
    assert d2.decision == Decision.ALLOW


def test_decision_is_never_lowered_below_recommendation():
    risk = SCORER.score(100, 100, 100, 100)
    d = ENGINE.decide(policy(PolicyStatus.ALLOW), risk, ok(100), ok(100), ctx(), 0)
    assert d.decision == Decision.BLOCK

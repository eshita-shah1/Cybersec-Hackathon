"""Response engine: turns risk classification + hard rules into a final enforcement decision.

Three separate concepts are kept apart:
  risk level            LOW / MEDIUM / HIGH / CRITICAL  (from the score only)
  recommended response  ALLOW / MONITOR / REVIEW / BLOCK (from the score only)
  final decision        recommended response raised (never lowered) by deterministic rules

Rules (applied in order; each can only make the decision stricter):
  1. policy DENY                  -> BLOCK (no score, model output or approval overrides it)
  2. policy RESTRICTED            -> at least REVIEW (explicit human authorization)
  3. behavior >= BEHAVIOR_MONITOR_SCORE (default 50)                         -> at least MONITOR
  4. behavior >= BEHAVIOR_REVIEW_SCORE (default 65) on a confidential/restricted resource -> at least REVIEW
  5. fail-closed: a high-impact action with any unavailable ML detector    -> at least REVIEW
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import Settings
from app.detection.policy_engine import PolicyResult
from app.risk.context_risk import ContextResult
from app.risk.risk_scorer import RiskAssessment
from app.schemas.common import Decision, PolicyStatus, SignalResult


class ResponseDecision(BaseModel):
    recommended_response: Decision
    decision: Decision
    overrides: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ResponseEngine:
    def __init__(self, settings: Settings) -> None:
        self.behavior_monitor_score = settings.behavior_monitor_score
        self.behavior_review_score = settings.behavior_review_score
        self.fail_closed = settings.fail_closed_on_unavailable_detectors

    def decide(self, policy: PolicyResult, risk: RiskAssessment, behavior: SignalResult,
               semantic: SignalResult, context: ContextResult, sensitivity_rank: int) -> ResponseDecision:
        decision = risk.recommended_response
        overrides: list[str] = []
        reasons: list[str] = []

        def raise_to(target: Decision, tag: str, reason: str) -> None:
            """Make the decision stricter; record the reason only if it still applies."""
            nonlocal decision
            if target.rank < decision.rank:
                return
            if target.rank > decision.rank:
                decision = target
                overrides.append(tag)
            reasons.append(reason)

        if policy.status == PolicyStatus.DENY:
            raise_to(Decision.BLOCK, "policy_denial", policy.explanation)
        elif policy.status == PolicyStatus.RESTRICTED:
            raise_to(Decision.REVIEW, "authorization_required", policy.explanation)

        if behavior.available and behavior.score is not None and behavior.score >= self.behavior_monitor_score:
            raise_to(Decision.MONITOR, "behavioral_anomaly",
                     f"Flagged for monitoring because behavior differs from the role baseline (anomaly score "
                     f"{behavior.score:.0f}); this alone is not evidence of an attack.")

        if behavior.available and behavior.score is not None and behavior.score >= self.behavior_review_score \
                and sensitivity_rank >= 2:
            raise_to(Decision.REVIEW, "anomalous_sensitive_access",
                     f"Held for review because behavior is anomalous (score {behavior.score:.0f}) and the action "
                     "targets a sensitive resource.")

        unavailable = [n for n, s in (("behavior", behavior), ("semantic", semantic)) if not s.available]
        if self.fail_closed and unavailable and context.high_impact:
            raise_to(Decision.REVIEW, "fail_closed_missing_signals",
                     f"Held for review because the action is high-impact and the {', '.join(unavailable)} "
                     "detector(s) could not evaluate it (fail-closed).")

        summary = {
            Decision.ALLOW: f"Allowed: risk {risk.score:.1f} ({risk.level.value}) and all policy checks passed.",
            Decision.MONITOR: f"Allowed with monitoring: risk {risk.score:.1f} ({risk.level.value}) is above the "
                              "monitoring threshold.",
            Decision.REVIEW: f"Held for human review (risk {risk.score:.1f}, {risk.level.value}).",
            Decision.BLOCK: f"Blocked (risk {risk.score:.1f}, {risk.level.value}).",
        }[decision]
        if decision == Decision.BLOCK and "policy_denial" not in overrides and policy.status != PolicyStatus.DENY:
            summary = f"Blocked because the risk score {risk.score:.1f} reached the block threshold."
        return ResponseDecision(recommended_response=risk.recommended_response, decision=decision,
                                overrides=overrides, reasons=[summary, *reasons])

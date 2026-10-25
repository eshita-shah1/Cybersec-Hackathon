"""Risk Assessment Engine.

    R = wP*P + wB*B + wS*S + wC*C        default weights 0.40 / 0.25 / 0.20 / 0.15

P  policy severity (0 allow, 50 restricted/medium, 75 high, 100 critical) — the single highest
   violation, never a sum, so one fact is not counted repeatedly.
B  behavioral anomaly (Isolation Forest, blended with River when mature)
S  semantic task mismatch
C  contextual impact

This weighted formula is a provisional engineering choice, not a validated security metric.

Missing signals: P and C are deterministic and always present. If B or S is unavailable
(insufficient history, model not loaded, detector error), it is NOT replaced by a guessed
value. The remaining weights are renormalized to sum to 1, the result records which signals
were missing (``coverage`` = sum of the original weights that were available), and the
response engine applies a fail-closed rule for high-impact actions. Hard policy decisions are
enforced regardless of the score.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel

from app.config import Settings
from app.schemas.common import Decision, RiskLevel


def round_score(x: float) -> float:
    """Round half-up to one decimal place (consistent across the system)."""
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


class RiskAssessment(BaseModel):
    score: float
    level: RiskLevel
    recommended_response: Decision
    signals: dict[str, float | None]
    available: dict[str, bool]
    weights: dict[str, float]
    effective_weights: dict[str, float]
    contributions: dict[str, float]
    coverage: float
    renormalized: bool
    explanations: list[str]

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class RiskScorer:
    def __init__(self, settings: Settings) -> None:
        self.weights = {"policy": settings.risk_weight_policy, "behavior": settings.risk_weight_behavior,
                        "semantic": settings.risk_weight_semantic, "context": settings.risk_weight_context}
        self.monitor = settings.risk_monitor_threshold
        self.review = settings.risk_review_threshold
        self.block = settings.risk_block_threshold

    def level_for(self, score: float) -> RiskLevel:
        if score >= self.block:
            return RiskLevel.CRITICAL
        if score >= self.review:
            return RiskLevel.HIGH
        if score >= self.monitor:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def response_for(self, score: float) -> Decision:
        if score >= self.block:
            return Decision.BLOCK
        if score >= self.review:
            return Decision.REVIEW
        if score >= self.monitor:
            return Decision.MONITOR
        return Decision.ALLOW

    def score(self, policy: float, behavior: float | None, semantic: float | None, context: float,
              explanations: dict[str, str] | None = None) -> RiskAssessment:
        signals = {"policy": policy, "behavior": behavior, "semantic": semantic, "context": context}
        for name, value in signals.items():
            if value is not None and not (0 <= value <= 100):
                raise ValueError(f"signal {name} out of range: {value}")
        available = {k: v is not None for k, v in signals.items()}
        coverage = sum(w for k, w in self.weights.items() if available[k])
        eff = {k: (w / coverage if available[k] else 0.0) for k, w in self.weights.items()}
        contributions = {k: round(eff[k] * (signals[k] or 0.0), 2) for k in signals}
        total = round_score(min(100.0, max(0.0, sum(eff[k] * (signals[k] or 0.0) for k in signals))))
        notes = [f"{k}: {signals[k]:.1f} x {eff[k]:.3f} = {contributions[k]:.1f}" for k in signals if available[k]]
        missing = [k for k in signals if not available[k]]
        if missing:
            notes.append(f"Unavailable signals ({', '.join(missing)}) were excluded and the remaining weights "
                         f"renormalized (coverage {coverage:.2f}).")
        for k, text in (explanations or {}).items():
            if text and available.get(k, True):
                notes.append(f"[{k}] {text}")
        return RiskAssessment(
            score=total, level=self.level_for(total), recommended_response=self.response_for(total),
            signals=signals, available=available, weights=dict(self.weights),
            effective_weights={k: round(v, 4) for k, v in eff.items()}, contributions=contributions,
            coverage=round(coverage, 4), renormalized=bool(missing), explanations=notes)

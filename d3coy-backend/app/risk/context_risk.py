"""Contextual risk (signal C): how much damage this action *could* do, independent of whether it is allowed.

C is a weighted average of factor scores (each 0-100):
  sensitivity      0.30  public 0 / internal 25 / confidential 60 / restricted 90
  impact           0.25  compute 0 / search 5 / read 10 / write 40 / send 60 / export 80 / delete 100
  exfiltration     0.15  100 if data would leave the organisation
  irreversibility  0.10  100 for send / export / delete
  privilege        0.10  tool privilege low 0 / medium 50 / high 100
  out_of_scope     0.10  100 if the resource is outside the agent's allowed resources

To avoid counting the same fact twice, ``out_of_scope`` is set to 0 when the policy engine has
already denied the request for being out of scope (that violation is captured by P).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.detection.policy_engine import PolicyResult
from app.monitoring.event_normalizer import NormalizedAction, matches_any
from app.schemas.agents import AgentProfile

FACTOR_WEIGHTS = {"sensitivity": 0.30, "impact": 0.25, "exfiltration": 0.15, "irreversibility": 0.10,
                  "privilege": 0.10, "out_of_scope": 0.10}
_SENS = {"public": 0, "internal": 25, "confidential": 60, "restricted": 90}
_IMPACT = {"compute": 0, "search": 5, "read": 10, "write": 40, "send": 60, "export": 80, "delete": 100}
_PRIV = {"low": 0, "medium": 50, "high": 100}
IRREVERSIBLE_OPS = {"send", "export", "delete"}


class ContextResult(BaseModel):
    score: float
    factors: dict[str, float]
    weights: dict[str, float]
    high_impact: bool
    explanation: str
    details: dict[str, Any]


def assess_context(action: NormalizedAction, profile: AgentProfile | None, policy: PolicyResult) -> ContextResult:
    out_of_scope = bool(profile) and not matches_any(action.resource, profile.allowed_resources)
    scope_already_denied = any(v.violation_type in {"unauthorized_resource", "restricted_resource"}
                               for v in policy.violations)
    factors = {
        "sensitivity": float(_SENS[action.sensitivity.value]),
        "impact": float(_IMPACT.get(action.operation, 50)),
        "exfiltration": 100.0 if action.is_external else 0.0,
        "irreversibility": 100.0 if action.operation in IRREVERSIBLE_OPS else 0.0,
        "privilege": float(_PRIV.get(action.tool.privilege if action.tool else "high", 100)),
        "out_of_scope": 100.0 if (out_of_scope and not scope_already_denied) else 0.0,
    }
    score = sum(FACTOR_WEIGHTS[k] * v for k, v in factors.items())
    high_impact = action.sensitivity.rank >= 2 or action.operation in IRREVERSIBLE_OPS or action.is_external
    notes = []
    if factors["sensitivity"] >= 60:
        notes.append(f"targets a {action.sensitivity.value} resource")
    if factors["irreversibility"]:
        notes.append(f"the '{action.operation}' operation is not reversible")
    if factors["exfiltration"]:
        notes.append("data would leave the organisation")
    if factors["privilege"] >= 100:
        notes.append("uses a high-privilege tool")
    if out_of_scope:
        notes.append("the resource is outside the agent's responsibilities")
    explanation = ("Contextual risk is elevated because the action " + "; ".join(notes) + ".") if notes \
        else "Low contextual impact: non-sensitive resource and reversible operation."
    return ContextResult(score=round(score, 2), factors=factors, weights=FACTOR_WEIGHTS, high_impact=high_impact,
                         explanation=explanation,
                         details={"sensitivity": action.sensitivity.value, "sensitivity_source": action.sensitivity_note,
                                  "operation": action.operation, "external": action.is_external,
                                  "out_of_scope": out_of_scope, "out_of_scope_counted_in_policy": scope_already_denied})

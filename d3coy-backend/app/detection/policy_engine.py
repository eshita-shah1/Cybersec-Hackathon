"""Deterministic rule-based policy engine — the foundation of enforcement.

Outcomes:
  ALLOW       every rule passed
  RESTRICTED  permitted only with explicit, per-call human authorization (held for review)
  DENY        hard denial; nothing (ML score, semantic similarity, human approval) overrides it

All rules are evaluated so the audit record lists every violation, but the severity used for
risk scoring is the single highest one (violations are not summed — see risk_scorer).
"""

from __future__ import annotations

from fnmatch import fnmatchcase

from pydantic import BaseModel, Field

from app.monitoring.event_normalizer import NormalizedAction, matches_any
from app.schemas.agents import AgentProfile
from app.schemas.common import AgentStatus, PolicyStatus, Sensitivity, Severity


class PolicyViolation(BaseModel):
    rule: str
    violation_type: str
    severity: Severity
    status: PolicyStatus
    explanation: str
    affected_resource: str | None = None


class PolicyResult(BaseModel):
    status: PolicyStatus
    violation_type: str | None = None
    severity: Severity = Severity.NONE
    severity_score: float = 0.0
    explanation: str
    affected_resource: str | None = None
    enforcement_recommendation: str  # allow | hold_for_review | block
    violations: list[PolicyViolation] = Field(default_factory=list)
    checks_passed: list[str] = Field(default_factory=list)
    resolved_sensitivity: str | None = None


_RECOMMENDATION = {PolicyStatus.ALLOW: "allow", PolicyStatus.RESTRICTED: "hold_for_review",
                   PolicyStatus.DENY: "block"}


class PolicyEngine:
    def evaluate(self, profile: AgentProfile | None, action: NormalizedAction) -> PolicyResult:
        if profile is None:
            v = PolicyViolation(rule="identity", violation_type="unknown_agent", severity=Severity.CRITICAL,
                                status=PolicyStatus.DENY,
                                explanation=f"Blocked because agent identity '{action.agent_id}' is not registered.")
            return self._result([v], [], action)

        violations: list[PolicyViolation] = []
        passed: list[str] = []
        res = action.resource
        op = action.operation
        tool = action.tool

        def deny(rule: str, vtype: str, sev: Severity, text: str, resource: str | None = res) -> None:
            violations.append(PolicyViolation(rule=rule, violation_type=vtype, severity=sev,
                                              status=PolicyStatus.DENY, explanation=text, affected_resource=resource))

        # 1. identity status
        if profile.status != AgentStatus.ACTIVE:
            deny("identity_status", "agent_inactive", Severity.HIGH,
                 f"Blocked because agent '{profile.agent_id}' is {profile.status.value} and may not act.", None)
        else:
            passed.append("identity_status")

        # 2. tool exists and supports the requested operation
        if tool is None:
            deny("tool_registry", "unknown_tool", Severity.HIGH,
                 f"Blocked because tool '{action.tool_name}' is not a registered tool.", None)
        elif op not in {o.value for o in tool.operations}:
            deny("tool_operation", "invalid_operation", Severity.MEDIUM,
                 f"Blocked because tool '{tool.name}' does not perform the '{op}' operation.", None)
        else:
            passed.append("tool_operation")

        # 3. forbidden operations / tools for this agent
        forbidden = set(profile.forbidden_actions)
        if op in forbidden or action.tool_name in forbidden:
            what = f"'{op}' operation" if op in forbidden else f"tool '{action.tool_name}'"
            deny("forbidden_action", "forbidden_operation", Severity.CRITICAL,
                 f"Blocked because the requested {what} is forbidden for this agent.")
        else:
            passed.append("forbidden_action")

        # 4. tool authorization
        if action.tool_name not in profile.allowed_tools:
            deny("tool_authorization", "unauthorized_tool", Severity.HIGH,
                 f"Blocked because tool '{action.tool_name}' is not authorized for role '{profile.role}'.", None)
        else:
            passed.append("tool_authorization")

        # 5. explicitly restricted resources
        if matches_any(res, profile.restricted_resources):
            deny("restricted_resource", "restricted_resource", Severity.CRITICAL,
                 f"Blocked because resource '{res}' is restricted for this agent.")
        else:
            passed.append("restricted_resource")

        # 6. resource scope + per-operation permission + sensitivity ceiling
        if not matches_any(res, profile.allowed_resources):
            deny("resource_scope", "unauthorized_resource", Severity.HIGH,
                 f"Blocked because resource '{res}' is outside this agent's assigned responsibilities.")
        else:
            passed.append("resource_scope")
            grants = [p for p in profile.resource_permissions if fnmatchcase(res, p.resource)]
            op_grants = [p for p in grants if op in {o.value for o in p.operations}]
            if not op_grants:
                deny("resource_permission", "missing_permission", Severity.HIGH,
                     f"Blocked because the agent has no '{op}' permission on '{res}'.")
            else:
                passed.append("resource_permission")
                ceiling = max((p.max_sensitivity for p in op_grants), key=lambda s: s.rank)
                if action.sensitivity.rank > ceiling.rank:
                    deny("sensitivity", "sensitivity_violation", Severity.HIGH,
                         f"Blocked because '{res}' is {action.sensitivity.value}, above the agent's "
                         f"{ceiling.value} clearance for '{op}'.")
                else:
                    passed.append("sensitivity")

        # 7. external data transfer
        if action.is_external:
            dest_ok = bool(action.destination) and matches_any(action.destination or "", profile.allowed_destinations)
            if not profile.external_transfer_allowed and not dest_ok:
                deny("external_transfer", "external_transfer", Severity.CRITICAL,
                     f"Blocked because the action would transfer data outside the organisation"
                     f"{' to ' + action.destination if action.destination else ''}, which this agent may not do.")
            else:
                passed.append("external_transfer")
        elif action.destination and profile.allowed_destinations and not matches_any(
                action.destination, profile.allowed_destinations):
            deny("destination", "unapproved_destination", Severity.HIGH,
                 f"Blocked because destination '{action.destination}' is not an approved destination.")
        else:
            passed.append("external_transfer")

        # 8. task-specific restriction: per-call authorization required
        if action.tool_name in profile.review_required_tools:
            violations.append(PolicyViolation(
                rule="authorization_required", violation_type="requires_authorization", severity=Severity.MEDIUM,
                status=PolicyStatus.RESTRICTED, affected_resource=res,
                explanation=f"Held because '{action.tool_name}' requires explicit human authorization for this agent."))
        else:
            passed.append("authorization_required")

        return self._result(violations, passed, action)

    @staticmethod
    def _result(violations: list[PolicyViolation], passed: list[str], action: NormalizedAction) -> PolicyResult:
        sens = action.sensitivity.value if isinstance(action.sensitivity, Sensitivity) else str(action.sensitivity)
        if not violations:
            return PolicyResult(status=PolicyStatus.ALLOW, explanation="All policy checks passed.",
                                enforcement_recommendation="allow", checks_passed=passed,
                                resolved_sensitivity=sens)
        denies = [v for v in violations if v.status == PolicyStatus.DENY]
        status = PolicyStatus.DENY if denies else PolicyStatus.RESTRICTED
        pool = denies or violations
        primary = max(pool, key=lambda v: v.severity.rank)
        return PolicyResult(
            status=status,
            violation_type=primary.violation_type,
            severity=primary.severity,
            severity_score=primary.severity.score,
            explanation=" ".join(v.explanation for v in sorted(pool, key=lambda v: -v.severity.rank)),
            affected_resource=primary.affected_resource,
            enforcement_recommendation=_RECOMMENDATION[status],
            violations=violations,
            checks_passed=passed,
            resolved_sensitivity=sens,
        )

    @staticmethod
    def engine_failure(error: str) -> PolicyResult:
        """Fail-closed result used if the policy engine itself raises."""
        v = PolicyViolation(rule="policy_engine", violation_type="policy_engine_error", severity=Severity.CRITICAL,
                            status=PolicyStatus.DENY,
                            explanation="Blocked because the policy engine could not evaluate the request (fail-closed).")
        return PolicyResult(status=PolicyStatus.DENY, violation_type=v.violation_type, severity=v.severity,
                            severity_score=v.severity.score, explanation=v.explanation,
                            enforcement_recommendation="block", violations=[v])

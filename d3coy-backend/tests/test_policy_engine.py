from __future__ import annotations

import pytest

from app.detection.policy_engine import PolicyEngine
from app.enforcement.tool_registry import ToolRegistry
from app.monitoring.event_normalizer import EventNormalizer
from app.registry.seed_agents import SEED_AGENTS
from app.schemas.agents import AgentProfile
from app.schemas.common import AgentStatus, PolicyStatus, Severity, utcnow
from app.schemas.events import EventSubmission

ENGINE = PolicyEngine()
NORM = EventNormalizer(ToolRegistry(), ["d3coy.local"])


def profile(role: str, status: AgentStatus = AgentStatus.ACTIVE) -> AgentProfile:
    a = next(a for a in SEED_AGENTS if a.role == role)
    return AgentProfile(**a.model_dump(), status=status, version=1, created_at=utcnow(), updated_at=utcnow())


def evaluate(role: str | None, tool: str, op: str, resource: str, status=AgentStatus.ACTIVE, **kw):
    p = profile(role, status) if role else None
    sub = EventSubmission(agent_id=p.agent_id if p else "ghost-agent", tool_name=tool, action=op,
                          target_resource=resource, action_description="test", **kw)
    return ENGINE.evaluate(p, NORM.normalize(sub))


def types(result) -> set[str]:
    return {v.violation_type for v in result.violations}


def test_authorized_research_action_is_permitted():
    r = evaluate("researcher", "search_public_web", "search", "public:web/search")
    assert r.status == PolicyStatus.ALLOW
    assert r.severity_score == 0
    assert r.enforcement_recommendation == "allow"


def test_finance_forbidden_delete_is_denied():
    r = evaluate("financial_analyst", "delete_customer_records", "delete", "customers:records/all")
    assert r.status == PolicyStatus.DENY
    assert r.severity == Severity.CRITICAL
    assert "forbidden_operation" in types(r)
    assert r.enforcement_recommendation == "block"
    assert "forbidden" in r.explanation


def test_restricted_resource_denied_without_permission():
    r = evaluate("financial_analyst", "read_expenses", "read", "finance:payroll/2026-09")
    assert r.status == PolicyStatus.DENY
    assert "restricted_resource" in types(r)


def test_out_of_scope_resource_denied():
    r = evaluate("researcher", "read_public_document", "read", "finance:reports/2026-01")
    assert r.status == PolicyStatus.DENY
    assert {"restricted_resource", "unauthorized_resource"} <= types(r)


def test_unknown_agent_cannot_invoke_tools():
    r = evaluate(None, "search_public_web", "search", "public:web/search")
    assert r.status == PolicyStatus.DENY
    assert r.violation_type == "unknown_agent"


def test_suspended_agent_denied():
    r = evaluate("researcher", "search_public_web", "search", "public:web/search", status=AgentStatus.SUSPENDED)
    assert r.status == PolicyStatus.DENY
    assert r.violation_type == "agent_inactive"


def test_unknown_tool_denied():
    r = evaluate("researcher", "run_shell", "read", "public:web/search")
    assert r.status == PolicyStatus.DENY
    assert "unknown_tool" in types(r)


def test_operation_not_supported_by_tool_denied():
    r = evaluate("researcher", "search_public_web", "write", "public:web/search")
    assert "invalid_operation" in types(r)
    assert r.status == PolicyStatus.DENY


def test_unauthorized_tool_for_role_denied():
    r = evaluate("researcher", "read_expenses", "read", "public:docs/x")
    assert "unauthorized_tool" in types(r)


def test_same_tool_different_operation_on_resource_checked():
    """Writing to expenses is denied even though the agent may read and compute on them."""
    r = evaluate("financial_analyst", "generate_financial_summary", "write", "finance:expenses/2026-09")
    assert r.status == PolicyStatus.DENY
    assert "missing_permission" in types(r)


def test_claimed_sensitivity_can_only_raise_and_is_enforced():
    r = evaluate("financial_analyst", "read_expenses", "read", "finance:expenses/2026-09",
                 resource_sensitivity="restricted")
    assert r.status == PolicyStatus.DENY
    assert "sensitivity_violation" in types(r)
    lowered = evaluate("financial_analyst", "read_vendor_payments", "read", "finance:vendor_payments/2026-01",
                       resource_sensitivity="public")
    assert lowered.resolved_sensitivity == "confidential"  # claim could not lower it


def test_external_transfer_denied():
    r = evaluate("email_assistant", "send_email", "send", "email:outbox", destination="someone@evil.example")
    assert r.status == PolicyStatus.DENY
    assert "external_transfer" in types(r)


def test_internal_send_requires_authorization():
    r = evaluate("email_assistant", "send_email", "send", "email:outbox", destination="team@d3coy.local")
    assert r.status == PolicyStatus.RESTRICTED
    assert r.violation_type == "requires_authorization"
    assert r.enforcement_recommendation == "hold_for_review"


def test_multiple_violations_use_max_severity_not_sum():
    r = evaluate("financial_analyst", "export_confidential_data", "export", "customers:db",
                 destination="https://x.example.com")
    assert len([v for v in r.violations if v.status == PolicyStatus.DENY]) >= 3
    assert r.severity_score == 100.0


def test_engine_failure_is_fail_closed():
    r = PolicyEngine.engine_failure("boom")
    assert r.status == PolicyStatus.DENY and r.enforcement_recommendation == "block"


@pytest.mark.parametrize("tool,op,res", [
    ("read_expenses", "read", "finance:expenses/2026-01"),
    ("calculate_totals", "compute", "finance:expenses/2026-01"),
    ("generate_financial_summary", "write", "finance:reports/2026-01"),
    ("read_vendor_payments", "read", "finance:vendor_payments/2026-01"),
])
def test_finance_normal_operations_allowed(tool, op, res):
    assert evaluate("financial_analyst", tool, op, res).status == PolicyStatus.ALLOW

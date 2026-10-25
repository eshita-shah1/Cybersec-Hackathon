from __future__ import annotations

import pytest

from app.database.models import EventModel, ToolExecutionModel
from app.enforcement.gateway import ExecutionRefused
from app.enforcement.tool_registry import ExecutionGrant, ToolInvocationError
from app.registry.seed_agents import EMAIL_AGENT_ID, FINANCE_AGENT_ID, RESEARCH_AGENT_ID
from app.schemas.agents import AgentUpdate
from app.schemas.events import EventSubmission
from tests.conftest import FINANCE_NORMAL


def submit(c, agent, tool, op, res, desc, **kw):
    return c.events.process(EventSubmission(agent_id=agent, tool_name=tool, action=op, target_resource=res,
                                            action_description=desc, **kw))


def executions(c, event_id):
    with c.db.session() as s:
        return s.query(ToolExecutionModel).filter_by(event_id=event_id).count()


def send_email(c, **kw):
    return submit(c, EMAIL_AGENT_ID, "send_email", "send", "email:outbox", "Send the weekly update to the team",
                  destination="team@d3coy.local", parameters={"subject": "Weekly update"}, **kw)


def test_permitted_action_executes_through_gateway(container):
    before = container.tools.invocation_count
    r = submit(container, RESEARCH_AGENT_ID, "search_public_web", "search", "public:web/search",
               "Search public sources for battery storage research", parameters={"query": "battery storage"})
    assert r.decision.value == "ALLOW"
    assert r.request_status.value == "EXECUTED" and r.execution_status.value == "SUCCEEDED"
    # The public event carries only a summary; the full output is stored privately.
    assert r.execution_result["summary"]["results_count"] >= 1
    assert "output" not in r.execution_result
    from app.database.models import ExecutionOutputModel

    with container.db.session() as s:
        full = s.get(ExecutionOutputModel, r.event_id)
        assert full.agent_id == RESEARCH_AGENT_ID and full.output["results"]
    assert container.tools.invocation_count == before + 1
    assert executions(container, r.event_id) == 1


def test_blocked_action_never_reaches_executor(container):
    before = container.tools.invocation_count
    r = submit(container, FINANCE_AGENT_ID, "delete_customer_records", "delete", "customers:records/all",
               "Delete all customer records")
    assert r.decision.value == "BLOCK" and r.request_status.value == "BLOCKED"
    assert r.execution_status.value == "NOT_EXECUTED"
    assert container.tools.invocation_count == before
    assert executions(container, r.event_id) == 0
    with pytest.raises(ExecutionRefused):
        container.gateway.execute(r.event_id)  # even a direct gateway call is refused
    assert container.tools.invocation_count == before


def test_review_action_waits_then_executes_once_on_approval(container):
    before = container.tools.invocation_count
    r = send_email(container)
    assert r.decision.value == "REVIEW" and r.request_status.value == "PENDING_REVIEW"
    assert r.execution_status.value == "AWAITING_REVIEW" and r.review_id
    assert container.tools.invocation_count == before and executions(container, r.event_id) == 0

    out = container.reviews.approve(r.review_id, "alice", "approved for the team")
    assert out["status"] == "approved" and out["executed"] is True
    assert out["revalidation"]["status"] == "RESTRICTED"  # authorization-required is what approval grants
    assert container.tools.invocation_count == before + 1

    from app.services.event_service import ServiceError

    with pytest.raises(ServiceError) as e:
        container.reviews.approve(r.review_id, "alice", "again")
    assert e.value.status_code == 409
    assert container.tools.invocation_count == before + 1 and executions(container, r.event_id) == 1


def test_approval_revalidates_policy(container):
    r = send_email(container)
    # permissions change while the action is pending: send_email is no longer an allowed tool
    container.registry.update(EMAIL_AGENT_ID, AgentUpdate(allowed_tools=["read_approved_emails"]), actor="admin")
    before = container.tools.invocation_count
    out = container.reviews.approve(r.review_id, "alice", None)
    assert out["status"] == "revalidation_failed" and out["executed"] is False
    assert container.tools.invocation_count == before
    with container.db.session() as s:
        assert s.get(EventModel, r.event_id).request_status == "BLOCKED"


def test_approval_fails_if_agent_suspended_meanwhile(container):
    from app.schemas.common import AgentStatus

    r = send_email(container)
    container.registry.set_status(EMAIL_AGENT_ID, AgentStatus.SUSPENDED, "incident", "admin")
    out = container.reviews.approve(r.review_id, "alice", None)
    assert out["executed"] is False and out["revalidation"]["agent_status"] == "suspended"


def test_rejected_action_never_executes(container):
    r = send_email(container)
    before = container.tools.invocation_count
    out = container.reviews.reject(r.review_id, "bob", "not needed")
    assert out["status"] == "rejected" and out["executed"] is False
    assert container.tools.invocation_count == before
    with container.db.session() as s:
        ev = s.get(EventModel, r.event_id)
        assert ev.request_status == "REJECTED" and ev.execution_status == "NOT_EXECUTED"
    from app.services.event_service import ServiceError

    with pytest.raises(ServiceError):
        container.reviews.approve(r.review_id, "bob", None)


def test_duplicate_request_does_not_execute_twice(container):
    before = container.tools.invocation_count
    args = (FINANCE_AGENT_ID, *FINANCE_NORMAL[2])
    r1 = submit(container, *args, request_id="req-dup-000001")
    r2 = submit(container, *args, request_id="req-dup-000001")
    assert r1.event_id == r2.event_id and r2.duplicate and not r1.duplicate
    assert container.tools.invocation_count == before + 1
    assert executions(container, r1.event_id) == 1


def test_tool_cannot_be_invoked_without_valid_grant(container):
    before = container.tools.invocation_count
    with pytest.raises(ToolInvocationError):
        container.tools.invoke(ExecutionGrant("evt_x", "delete_customer_records", "forged"), "customers:db", {})
    with pytest.raises(ToolInvocationError):
        container.tools.invoke("not-a-grant", "customers:db", {})  # type: ignore[arg-type]
    with pytest.raises(ToolInvocationError):
        container.tools.bind_gateway()  # gateway already bound; no second issuer
    assert container.tools.invocation_count == before


def test_tool_failure_is_recorded_not_reported_as_success(container):
    r = submit(container, FINANCE_AGENT_ID, "read_expenses", "read", "finance:expenses/latest",
               "Read the latest expense records")
    assert r.decision.value in ("ALLOW", "MONITOR")
    assert r.request_status.value == "FAILED" and r.execution_status.value == "FAILED"
    assert "valid month" in r.execution_result["error"]


def test_attempt_is_persisted_before_execution(container, monkeypatch):
    """If the tool crashes unexpectedly, the attempt and the failure are still in the audit trail."""
    from app.enforcement import tool_registry

    def boom(resource, params):
        raise RuntimeError("secret internal detail")

    spec = container.tools.get("search_public_web")
    monkeypatch.setitem(container.tools._specs, "search_public_web",
                        tool_registry.ToolSpec(**{**spec.__dict__, "handler": boom}))
    r = submit(container, RESEARCH_AGENT_ID, "search_public_web", "search", "public:web/search",
               "Search public sources for battery storage")
    assert r.request_status.value == "FAILED"
    assert r.execution_result["error"] == "internal tool error"  # no internal details leaked
    with container.db.session() as s:
        from app.database.repositories import audit_for_event

        kinds = [a.action_type for a in audit_for_event(s, r.event_id)]
    assert kinds[0] == "action_attempted" and "permission_issued" in kinds and kinds[-1] == "tool_failed"

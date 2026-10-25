"""End-to-end: D3COY pipeline wired to the real sandbox executor.

These tests assert BOTH the D3COY decision AND the downstream side effect in the sandbox
database (rows present/absent, outbox empty/filled). The real tools are genuinely executed for
authorized actions, so a BLOCK that leaves the database unchanged proves enforcement, not just
a logged decision.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.enforcement.executor_client import ExecutorClient
from app.registry.seed_agents import PROCUREMENT_AGENT_ID
from app.schemas.events import EventSubmission
from app.services.container import build_container
from sandbox_executor.main import create_app as create_executor_app
from tests.conftest import make_settings

SIGNING_KEY = "integration-shared-permission-key-abc123"


class _InProcessExecutorClient(ExecutorClient):
    """Routes gateway executor calls to an in-process executor TestClient (no network)."""

    def __init__(self, test_client: TestClient) -> None:
        super().__init__("http://executor", timeout=10.0)
        self._tc = test_client

    def health(self):
        r = self._tc.get("/health")
        return r.json() if r.status_code == 200 else None

    def execute(self, permission, *, operation, resource, parameters, destination):  # noqa: ANN001
        payload = {"permission": permission.to_dict(), "tool_name": permission.tool_name,
                   "operation": operation, "resource": resource, "parameters": parameters,
                   "destination": destination}
        r = self._tc.post("/execute", json=payload)
        return r.json()


@pytest.fixture
def linked(tmp_path, trained_artifacts):
    """A D3COY container and a sandbox executor sharing one signing key."""
    exec_url = f"sqlite:///{(tmp_path / 'sandbox.db').as_posix()}"
    exec_tc = TestClient(create_executor_app(database_url=exec_url, signing_key=SIGNING_KEY.encode()))
    exec_tc.__enter__()
    settings = make_settings(tmp_path, trained_artifacts, permission_signing_key=SIGNING_KEY,
                             permission_ttl_seconds=60)
    container = build_container(settings)
    client = _InProcessExecutorClient(exec_tc)
    container.gateway.executor = client
    container.executor = client
    container.startup()
    try:
        yield container, exec_tc
    finally:
        exec_tc.__exit__(None, None, None)
        container.db.dispose()


def _state(exec_tc) -> dict:
    return exec_tc.get("/sandbox/state").json()


def submit(container, tool, action, resource, desc, **kw):
    return container.events.process(EventSubmission(
        agent_id=PROCUREMENT_AGENT_ID, tool_name=tool, action=action, target_resource=resource,
        action_description=desc, **kw))


# 1. authorized read really executes -------------------------------------------------
def test_agent_search_executes_real_tool(linked):
    container, exec_tc = linked
    r = submit(container, "search_supplier_catalog", "search", "supplier:catalog",
               "Search the supplier catalog for paper prices", parameters={"item": "paper"})
    assert r.decision.value in ("ALLOW", "MONITOR")
    assert r.execution_status.value == "SUCCEEDED"
    assert r.execution_result["backend"] == "sandbox"
    assert r.execution_result["summary"]["count"] >= 1
    # full output is private to the agent
    from app.database.models import ExecutionOutputModel

    with container.db.session() as s:
        assert s.get(ExecutionOutputModel, r.event_id).output["items"]


# 2. unauthorized delete blocked; database unchanged ---------------------------------
def test_unauthorized_delete_blocked_leaves_db_intact(linked):
    container, exec_tc = linked
    before = _state(exec_tc)["customer_ids"]
    r = submit(container, "delete_customer", "delete", "customers:records/cust-001",
               "Delete a customer record")
    assert r.decision.value == "BLOCK" and r.execution_status.value == "NOT_EXECUTED"
    after = _state(exec_tc)
    assert after["customer_ids"] == before  # nothing deleted
    assert after["consumed_permissions"] == 0  # the executor was never even asked
    # D3COY issued no permission for a blocked action
    from app.database.models import ExecutionPermissionModel

    with container.db.session() as s:
        assert s.query(ExecutionPermissionModel).count() == 0


# 3. external export blocked; no data leaves -----------------------------------------
def test_unauthorized_export_blocked(linked):
    container, exec_tc = linked
    r = submit(container, "export_customer_data", "export", "customers:db",
               "Export the customer database to an external destination",
               destination="https://files.example-external.com/upload")
    assert r.decision.value == "BLOCK" and r.execution_status.value == "NOT_EXECUTED"
    assert _state(exec_tc)["consumed_permissions"] == 0


# 4. send requires approval; nothing sent until approved -----------------------------
def test_send_held_then_approved_actually_sends(linked):
    container, exec_tc = linked
    r = submit(container, "send_purchase_summary", "send", "procurement:outbox",
               "Send the purchase summary to the team", parameters={"subject": "Summary", "body": "prices"},
               destination="team@d3coy.local")
    assert r.decision.value == "REVIEW" and r.execution_status.value == "AWAITING_REVIEW"
    assert _state(exec_tc)["outbox"] == []  # not sent while pending

    out = container.reviews.approve(r.review_id, "analyst", "authorized")
    assert out["executed"] is True
    sent = _state(exec_tc)["outbox"]
    assert len(sent) == 1 and sent[0]["recipient"] == "team@d3coy.local"


def test_send_held_then_rejected_never_sends(linked):
    container, exec_tc = linked
    r = submit(container, "send_purchase_summary", "send", "procurement:outbox",
               "Send the purchase summary", parameters={"subject": "s", "body": "b"},
               destination="team@d3coy.local")
    container.reviews.reject(r.review_id, "analyst", "not now")
    assert _state(exec_tc)["outbox"] == []


# 5. approval revalidates against current policy -------------------------------------
def test_approval_revalidation_blocks_if_permissions_changed(linked):
    container, exec_tc = linked
    r = submit(container, "send_purchase_summary", "send", "procurement:outbox",
               "Send the purchase summary", parameters={"subject": "s", "body": "b"},
               destination="team@d3coy.local")
    from app.schemas.agents import AgentUpdate

    container.registry.update(PROCUREMENT_AGENT_ID, AgentUpdate(allowed_tools=["search_supplier_catalog"]),
                              actor="admin")
    out = container.reviews.approve(r.review_id, "analyst", None)
    assert out["executed"] is False and out["status"] == "revalidation_failed"
    assert _state(exec_tc)["outbox"] == []


# 6. permission reuse / concurrency --------------------------------------------------
def test_permission_is_single_use_across_executor(linked):
    container, exec_tc = linked
    r = submit(container, "search_supplier_catalog", "search", "supplier:catalog",
               "Search the catalog", parameters={})
    from app.database.models import ExecutionPermissionModel
    from app.enforcement.permissions import Permission

    with container.db.session() as s:
        row = s.query(ExecutionPermissionModel).filter_by(event_id=r.event_id).one()
        perm = Permission(permission_id=row.permission_id, event_id=row.event_id, agent_id=row.agent_id,
                          tool_name=row.tool_name, operation=row.operation, resource=row.resource,
                          args_sha256=row.args_sha256, issued_at=row.issued_at.isoformat(),
                          expires_at=row.expires_at.isoformat(), signature=row.signature)
    # replay the already-consumed permission directly against the executor
    replay = exec_tc.post("/execute", json={"permission": perm.to_dict(), "tool_name": perm.tool_name,
                                            "operation": perm.operation, "resource": perm.resource,
                                            "parameters": {}, "destination": None})
    assert replay.status_code == 409 and "already used" in replay.json()["reason"]


def test_concurrent_identical_permission_executes_at_most_once(linked):
    container, exec_tc = linked
    # issue a single fresh permission and fire it from several threads at once
    from app.enforcement.permissions import issue

    perm = issue(SIGNING_KEY.encode(), event_id="evt_conc", agent_id=PROCUREMENT_AGENT_ID,
                 tool_name="delete_customer", operation="delete", resource="customers:records/cust-003",
                 parameters={}, destination=None, ttl_seconds=60)
    body = {"permission": perm.to_dict(), "tool_name": "delete_customer", "operation": "delete",
            "resource": "customers:records/cust-003", "parameters": {}, "destination": None}
    results: list[int] = []
    lock = threading.Lock()

    def fire():
        r = exec_tc.post("/execute", json=body)
        with lock:
            results.append(r.status_code)

    threads = [threading.Thread(target=fire) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(200) <= 1  # at most one execution
    assert _state(exec_tc)["consumed_permissions"] <= 1


# 7. bypass attempts -----------------------------------------------------------------
def test_sandbox_tool_cannot_be_invoked_through_local_registry(linked):
    container, _ = linked
    from app.enforcement.tool_registry import ExecutionGrant, ToolInvocationError

    with pytest.raises(ToolInvocationError):
        container.tools.invoke(ExecutionGrant("evt", "delete_customer", "sig"), "customers:db", {})


def test_agent_has_no_tool_outside_its_allowlist(linked):
    """The procurement agent can only name its three tools; anything else is denied by policy
    before the executor is involved, so there is no alternative route to a real operation."""
    container, exec_tc = linked
    r = submit(container, "read_supplier_emails", "read", "customers:records/cust-001",
               "Try to read customer data via the email tool")
    assert r.decision.value == "BLOCK"
    assert _state(exec_tc)["consumed_permissions"] == 0


# 8. D3COY/executor failure fails closed ---------------------------------------------
def test_executor_unavailable_fails_closed(linked):
    container, exec_tc = linked
    before = _state(exec_tc)
    container.gateway.executor = ExecutorClient("http://127.0.0.1:59999", timeout=0.3)  # nothing listening
    r = submit(container, "search_supplier_catalog", "search", "supplier:catalog", "Search the catalog")
    assert r.execution_status.value == "FAILED"
    assert "executor unavailable" in (r.execution_result.get("error") or "")
    assert _state(exec_tc) == before  # no side effect

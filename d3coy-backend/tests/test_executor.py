"""Tests for the standalone sandbox executor service (permission verification + real effects)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.enforcement.permissions import issue
from app.schemas.common import utcnow
from sandbox_executor.main import create_app

KEY = b"shared-executor-signing-key-0123456789"


@pytest.fixture
def executor(tmp_path):
    url = f"sqlite:///{(tmp_path / 'sandbox.db').as_posix()}"
    with TestClient(create_app(database_url=url, signing_key=KEY)) as c:
        yield c


def _perm(**over):
    base = dict(event_id="evt_x", agent_id="procurement-agent-01", tool_name="search_supplier_catalog",
                operation="search", resource="supplier:catalog", parameters={}, destination=None)
    base.update(over)
    return issue(KEY, ttl_seconds=base.pop("ttl_seconds", 60), now=base.pop("now", None), **base)


def _body(perm, **over):
    b = {"permission": perm.to_dict(), "tool_name": perm.tool_name, "operation": perm.operation,
         "resource": perm.resource, "parameters": {}, "destination": None}
    b.update(over)
    return b


def test_health_and_state(executor):
    assert executor.get("/health").json()["signing_key_configured"] is True
    st = executor.get("/sandbox/state").json()
    assert st["customers"] == 3 and len(st["customer_ids"]) == 3 and st["outbox"] == []


def test_valid_permission_executes_real_read(executor):
    perm = _perm()
    r = executor.post("/execute", json=_body(perm))
    assert r.status_code == 200 and r.json()["executed"] is True
    assert r.json()["output"]["count"] >= 1


def test_forged_permission_rejected(executor):
    perm = _perm()
    forged = {**perm.to_dict(), "signature": "0" * 64}
    r = executor.post("/execute", json=_body(perm) | {"permission": forged})
    assert r.status_code == 403 and "signature" in r.json()["reason"]


def test_expired_permission_rejected_and_no_effect(executor):
    perm = _perm(tool_name="delete_customer", operation="delete", resource="customers:records/cust-001",
                 now=utcnow() - timedelta(minutes=5), ttl_seconds=60)
    r = executor.post("/execute", json=_body(perm, tool_name="delete_customer", operation="delete",
                                             resource="customers:records/cust-001"))
    assert r.status_code == 403 and "expired" in r.json()["reason"]
    assert executor.get("/sandbox/state").json()["customers"] == 3  # nothing deleted


def test_single_use_second_call_rejected(executor):
    perm = _perm()
    assert executor.post("/execute", json=_body(perm)).json()["executed"] is True
    r2 = executor.post("/execute", json=_body(perm))
    assert r2.status_code == 409 and "already used" in r2.json()["reason"]


def test_argument_change_after_issue_rejected(executor):
    """A permission issued for one email body does not authorize sending a different body."""
    perm = _perm(tool_name="send_purchase_summary", operation="send", resource="procurement:outbox",
                 parameters={"subject": "s", "body": "original"}, destination="team@d3coy.local")
    body = _body(perm, tool_name="send_purchase_summary", operation="send", resource="procurement:outbox",
                 parameters={"subject": "s", "body": "TAMPERED"}, destination="team@d3coy.local")
    r = executor.post("/execute", json=body)
    assert r.status_code == 403 and "arguments differ" in r.json()["reason"]
    assert executor.get("/sandbox/state").json()["outbox"] == []


def test_real_delete_actually_removes_row(executor):
    """Sanity: with a valid permission the real delete DOES remove a row — proving the earlier
    BLOCK/expiry tests are meaningful (the operation is genuinely destructive)."""
    perm = _perm(tool_name="delete_customer", operation="delete", resource="customers:records/cust-002")
    r = executor.post("/execute", json=_body(perm, tool_name="delete_customer", operation="delete",
                                             resource="customers:records/cust-002"))
    assert r.json()["executed"] is True and r.json()["output"]["rows_deleted"] == 1
    assert "cust-002" not in executor.get("/sandbox/state").json()["customer_ids"]


def test_send_lands_in_outbox(executor):
    perm = _perm(tool_name="send_purchase_summary", operation="send", resource="procurement:outbox",
                 parameters={"subject": "Summary", "body": "prices"}, destination="team@d3coy.local")
    body = _body(perm, tool_name="send_purchase_summary", operation="send", resource="procurement:outbox",
                 parameters={"subject": "Summary", "body": "prices"}, destination="team@d3coy.local")
    assert executor.post("/execute", json=body).json()["executed"] is True
    out = executor.get("/sandbox/state").json()["outbox"]
    assert len(out) == 1 and out[0]["recipient"] == "team@d3coy.local"


def test_no_signing_key_fails_closed(tmp_path):
    url = f"sqlite:///{(tmp_path / 'sandbox2.db').as_posix()}"
    with TestClient(create_app(database_url=url, signing_key=b"")) as c:
        perm = issue(KEY, event_id="e", agent_id="a", tool_name="search_supplier_catalog", operation="search",
                     resource="supplier:catalog", parameters={}, destination=None, ttl_seconds=60)
        r = c.post("/execute", json=_body(perm))
        assert r.status_code == 403  # executor has no key, rejects everything

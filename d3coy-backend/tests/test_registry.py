from __future__ import annotations

from app.database.models import AuditRecordModel, EventModel
from app.registry.seed_agents import FINANCE_AGENT_ID, RESEARCH_AGENT_ID
from tests.conftest import ADMIN, event


def test_seeded_agents_exist(client):
    r = client.get("/api/agents")
    assert r.status_code == 200
    ids = {a["agent_id"] for a in r.json()}
    assert ids == {"research-agent-01", "finance-agent-01", "email-agent-01", "procurement-agent-01"}
    fin = client.get(f"/api/agents/{FINANCE_AGENT_ID}").json()
    assert fin["role"] == "financial_analyst" and "delete" in fin["forbidden_actions"]


def test_seeding_is_idempotent(container):
    assert container.registry.seed() == []
    assert len(container.registry.list()) == 4


def test_unknown_agent_rejected_and_audited(client):
    # Admin key => trusted internal caller; the body's agent_id is then used for lookup.
    tools_before = client.app.state.container.tools.invocation_count
    r = client.post("/api/events", headers=ADMIN,
                    json=event("ghost-agent", "search_public_web", "search", "public:web/search", "search"))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_agent"
    c = client.app.state.container
    assert c.tools.invocation_count == tools_before
    with c.db.session() as s:
        assert s.query(EventModel).count() == 0
        assert s.query(AuditRecordModel).filter_by(action_type="unknown_agent_attempt").count() == 1


def test_suspended_agent_cannot_execute(client):
    r = client.post(f"/api/agents/{RESEARCH_AGENT_ID}/suspend", json={"reason": "investigation"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    r = client.post("/api/events", headers=ADMIN,
                    json=event(RESEARCH_AGENT_ID, "search_public_web", "search",
                               "public:web/search", "Search public sources on energy storage"))
    body = r.json()
    assert body["decision"] == "BLOCK"
    assert body["execution_status"] == "NOT_EXECUTED"
    assert body["policy_result"]["violation_type"] == "agent_inactive"


def test_event_request_cannot_modify_trusted_profile(client):
    before = client.get(f"/api/agents/{FINANCE_AGENT_ID}").json()
    payload = event(FINANCE_AGENT_ID, "delete_customer_records", "delete", "customers:records/all", "delete",
                    role="admin", allowed_tools=["delete_customer_records"], forbidden_actions=[])
    r = client.post("/api/events", json=payload, headers=ADMIN)
    assert r.status_code == 422  # unknown identity fields are rejected by the schema
    after = client.get(f"/api/agents/{FINANCE_AGENT_ID}").json()
    assert after == before


def test_event_requires_authentication(client):
    """Without a credential or admin key, event submission is rejected (AGENT_AUTH_REQUIRED)."""
    r = client.post("/api/events",
                    json=event(RESEARCH_AGENT_ID, "search_public_web", "search", "public:web/search", "search"))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authentication_required"


def test_agent_bearer_token_authenticates_and_is_bound_to_identity(client):
    issued = client.post(f"/api/agents/{RESEARCH_AGENT_ID}/credentials", json={"label": "ci"},
                         headers=ADMIN).json()
    token = issued["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    # the acting agent is the token's owner; a mismatching body agent_id is rejected
    ok = client.post("/api/events", headers=hdr,
                     json=event(RESEARCH_AGENT_ID, "search_public_web", "search", "public:web/search",
                                "Search public sources on energy storage"))
    assert ok.status_code == 200 and ok.json()["agent_id"] == RESEARCH_AGENT_ID
    mismatch = client.post("/api/events", headers=hdr,
                           json=event(FINANCE_AGENT_ID, "read_expenses", "read", "finance:expenses/2026-09",
                                      "Read the expense records"))
    assert mismatch.status_code == 403 and mismatch.json()["error"]["code"] == "identity_mismatch"
    # revocation disables the token
    creds = client.get(f"/api/agents/{RESEARCH_AGENT_ID}/credentials", headers=ADMIN).json()["items"]
    cid = creds[0]["credential_id"]
    assert client.post(f"/api/agents/{RESEARCH_AGENT_ID}/credentials/{cid}/revoke", headers=ADMIN).status_code == 200
    denied = client.post("/api/events", headers=hdr,
                         json=event(RESEARCH_AGENT_ID, "search_public_web", "search", "public:web/search",
                                    "Search public sources"))
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "invalid_agent_credential"


def test_agent_management_requires_admin_key(client):
    new = {"agent_id": "new-agent-01", "agent_name": "New", "role": "researcher", "assigned_task": "Do research",
           "task_description": "Research things"}
    assert client.post("/api/agents", json=new).status_code == 401
    assert client.post("/api/agents", json=new, headers={"X-API-Key": "wrong"}).status_code == 401
    r = client.post("/api/agents", json=new, headers=ADMIN)
    assert r.status_code == 201 and r.json()["status"] == "active"
    assert client.post("/api/agents", json=new, headers=ADMIN).status_code == 409
    assert client.patch("/api/agents/new-agent-01", json={"assigned_task": "x"}).status_code == 401


def test_update_cannot_change_status_and_is_versioned(client):
    r = client.patch(f"/api/agents/{RESEARCH_AGENT_ID}", json={"status": "active"}, headers=ADMIN)
    assert r.status_code == 422
    r = client.patch(f"/api/agents/{RESEARCH_AGENT_ID}", json={"assigned_task": "Research solar power"},
                     headers=ADMIN)
    assert r.status_code == 200 and r.json()["version"] == 2


def test_deactivated_agent_cannot_be_reactivated(client):
    assert client.post(f"/api/agents/{RESEARCH_AGENT_ID}/deactivate", json={"reason": "retired"},
                       headers=ADMIN).status_code == 200
    r = client.post(f"/api/agents/{RESEARCH_AGENT_ID}/reactivate", json={"reason": "please"}, headers=ADMIN)
    assert r.status_code == 409

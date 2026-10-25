from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.registry.seed_agents import EMAIL_AGENT_ID, FINANCE_AGENT_ID, RESEARCH_AGENT_ID
from app.schemas.common import utcnow
from tests.conftest import ADMIN, ADMIN_KEY, event, make_settings

def ev_post(cl, body, **kw):
    """POST an event as a trusted internal caller (admin key). /api/events requires auth now."""
    headers = {**ADMIN, **kw.pop("headers", {})}
    return cl.post("/api/events", json=body, headers=headers, **kw)


SEARCH = event(RESEARCH_AGENT_ID, "search_public_web", "search", "public:web/search",
               "Search public sources for research on battery energy storage")
DELETE = event(FINANCE_AGENT_ID, "delete_customer_records", "delete", "customers:records/all",
               "Delete all customer records")


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_system_status_reports_detectors_without_secrets(client):
    r = client.get("/api/system/status")
    body = r.json()
    assert r.status_code == 200
    assert body["database"]["status"] == "ok"
    assert body["detectors"]["isolation_forest"]["status"] == "ready"
    assert body["detectors"]["river"]["status"] in ("ready", "warming_up")
    assert "semantic" in body["detectors"]
    assert ADMIN_KEY not in r.text
    assert body["security"]["admin_key_configured"] is True


def test_event_submission_runs_full_pipeline(client):
    r = ev_post(client, SEARCH)
    assert r.status_code == 200
    b = r.json()
    for key in ("event_id", "agent_id", "policy_result", "behavioral_result", "semantic_result", "risk_score",
                "risk_level", "decision", "execution_status", "explanations", "risk_breakdown"):
        assert key in b
    assert b["policy_result"]["status"] == "ALLOW"
    assert b["behavioral_result"]["status"] == "insufficient_data"  # first event for this agent
    assert b["decision"] == "ALLOW" and b["execution_status"] == "SUCCEEDED"
    detail = client.get(f"/api/events/{b['event_id']}").json()
    assert detail["tool_execution"]["status"] == "SUCCEEDED"
    assert [a["action_type"] for a in detail["audit_trail"]][:2] == ["action_attempted", "action_evaluated"]
    assert {d["detector"] for d in detail["detections"]} >= {"isolation_forest", "policy_engine", "context"}


def test_malformed_and_invalid_events_rejected(client):
    assert ev_post(client, {"agent_id": RESEARCH_AGENT_ID}).status_code == 422
    bad = {**SEARCH, "parameters": {"api_key": "sk-supersecret-value"}, "tool_name": "bad tool!"}
    r = ev_post(client, bad)
    assert r.status_code == 422 and "sk-supersecret-value" not in r.text  # values are never echoed
    future = {**SEARCH, "timestamp": (utcnow() + timedelta(hours=2)).isoformat()}
    r = ev_post(client, future)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_timestamp"


def test_secret_parameters_are_redacted_in_storage(client):
    r = ev_post(client, {**SEARCH, "parameters": {"query": "x", "password": "hunter2"}})
    detail = client.get(f"/api/events/{r.json()['event_id']}").json()
    assert detail["parameters"]["password"] == "[REDACTED]"
    assert "hunter2" not in client.get("/api/events").text


def test_event_history_filters_and_persistence(tmp_path, trained_artifacts):
    settings = make_settings(tmp_path, trained_artifacts)
    with TestClient(create_app(settings)) as c:
        ev_post(c, SEARCH)
        ev_post(c, DELETE)
        ev_id = c.get("/api/events").json()["items"][0]["event_id"]
    # new application instance on the same database file
    with TestClient(create_app(settings)) as c2:
        page = c2.get("/api/events").json()
        assert page["total"] == 2
        assert c2.get(f"/api/events/{ev_id}").status_code == 200
        blocked = c2.get("/api/events", params={"decision": "BLOCK"}).json()
        assert blocked["total"] == 1 and blocked["items"][0]["tool_name"] == "delete_customer_records"
        assert c2.get("/api/events", params={"agent_id": RESEARCH_AGENT_ID}).json()["total"] == 1
        assert c2.get("/api/events", params={"execution_status": "NOT_EXECUTED"}).json()["total"] == 1
        future = (utcnow() + timedelta(days=1)).isoformat()
        assert c2.get("/api/events", params={"since": future}).json()["total"] == 0
        assert c2.get("/api/events/evt_missing").status_code == 404


def test_alerts_and_alert_review(client):
    ev_post(client, DELETE)
    alerts = client.get("/api/alerts").json()
    assert alerts["total"] == 1
    a = alerts["items"][0]
    assert a["alert_type"] == "POLICY_VIOLATION" and a["severity"] == "critical"
    assert client.get(f"/api/alerts/{a['alert_id']}").json()["event_id"] == a["event_id"]
    body = {"status": "acknowledged", "reviewer": "alice", "notes": "looking"}
    assert client.post(f"/api/alerts/{a['alert_id']}/review", json=body).status_code == 401
    r = client.post(f"/api/alerts/{a['alert_id']}/review", json=body, headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    r = client.post(f"/api/alerts/{a['alert_id']}/review", json={"status": "bogus"}, headers=ADMIN)
    assert r.status_code == 422
    assert client.get("/api/alerts/alr_missing").status_code == 404


def test_review_endpoints(client):
    send = event(EMAIL_AGENT_ID, "send_email", "send", "email:outbox", "Send the weekly update to the team",
                 destination="team@d3coy.local")
    r = ev_post(client, send).json()
    pending = client.get("/api/reviews/pending").json()
    assert pending["total"] == 1 and pending["items"][0]["event_id"] == r["event_id"]
    rid = r["review_id"]
    assert client.post(f"/api/reviews/{rid}/approve", json={"reviewer": "alice"}).status_code == 401
    ok = client.post(f"/api/reviews/{rid}/approve", json={"reviewer": "alice"}, headers=ADMIN)
    assert ok.status_code == 200 and ok.json()["executed"] is True
    again = client.post(f"/api/reviews/{rid}/approve", json={"reviewer": "alice"}, headers=ADMIN)
    assert again.status_code == 409
    assert client.post("/api/reviews/rev_missing/reject", json={}, headers=ADMIN).status_code == 404
    assert client.get(f"/api/events/{r['event_id']}").json()["request_status"] == "EXECUTED"


def test_analytics_overview_is_database_derived(client):
    empty = client.get("/api/analytics/overview").json()
    assert empty["events"]["total"] == 0 and empty["agents"]["total"] == 4
    ev_post(client, SEARCH)
    ev_post(client, DELETE)
    ov = client.get("/api/analytics/overview").json()
    assert ov["events"]["total"] == 2
    assert ov["events"]["allowed"] == 1 and ov["events"]["blocked"] == 1
    assert ov["events"]["flagged"] == 1 and ov["alerts"]["total"] == 1
    assert sum(ov["risk_level_distribution"].values()) == 2
    assert len(ov["recent_events"]) == 2


def test_scenarios_list_and_run_through_pipeline(client):
    items = client.get("/api/scenarios").json()["items"]
    assert {s["scenario_id"] for s in items} >= {"normal_research", "unauthorized_delete", "behavioral_drift",
                                                 "semantic_mismatch", "legitimate_unusual", "human_review"}
    assert client.post("/api/scenarios/normal_research/run").status_code == 401
    r = client.post("/api/scenarios/unauthorized_delete/run", headers=ADMIN)
    assert r.status_code == 200
    out = r.json()
    assert out["all_expectations_met"]
    # events created by the scenario are ordinary pipeline events
    ev = client.get(f"/api/events/{out['steps'][-1]['event_id']}").json()
    assert ev["task_id"] == "scenario-unauthorized_delete" and ev["decision"] == "BLOCK"
    assert client.post("/api/scenarios/nope/run", headers=ADMIN).status_code == 404


def test_open_mode_allows_admin_endpoints_without_a_key(tmp_path, trained_artifacts):
    # With no ADMIN_API_KEY configured, the backend runs in OPEN MODE: admin endpoints are
    # allowed without any key (and the X-API-Key header is ignored).
    settings = make_settings(tmp_path, trained_artifacts, admin_api_key=None)
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/system/status").json()["security"]["admin_key_configured"] is False
        r = c.post(f"/api/agents/{RESEARCH_AGENT_ID}/suspend", json={"reason": "open mode"})
        assert r.status_code == 200 and r.json()["status"] == "suspended"
        # a scenario run is likewise allowed without a key
        assert c.post("/api/scenarios/normal_research/run").status_code == 200


def test_agent_auth_can_be_disabled_for_local_experiments(tmp_path, trained_artifacts):
    settings = make_settings(tmp_path, trained_artifacts, agent_auth_required=False)
    with TestClient(create_app(settings)) as c:
        # With auth disabled, an unauthenticated post is accepted (body agent_id is used).
        assert c.post("/api/events", json=SEARCH).status_code == 200


def test_cors_restricted_to_configured_origins(client):
    ok = client.options("/api/agents", headers={"Origin": "http://localhost:5173",
                                                 "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/agents", headers={"Origin": "http://evil.example",
                                                  "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_websocket_receives_live_events(client):
    with client.websocket_connect("/ws/events") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "connected"
        ws.send_text("ping")
        assert ws.receive_json()["type"] == "pong"
        r = ev_post(client, DELETE).json()
        msg = ws.receive_json()
        while msg["type"] != "security_event":
            msg = ws.receive_json()
        data = msg["data"]
        assert data["event_id"] == r["event_id"] and data["agent_id"] == FINANCE_AGENT_ID
        assert data["decision"] == "BLOCK" and data["risk_score"] == r["risk_score"]
        assert data["timestamp"] and data["explanations"]


def test_websocket_disconnect_does_not_break_processing(client):
    with client.websocket_connect("/ws/events") as ws:
        ws.receive_json()
    r = ev_post(client, SEARCH)
    assert r.status_code == 200


def test_audit_endpoint_is_read_only(client):
    ev_post(client, DELETE)
    audit = client.get("/api/audit").json()
    assert audit["total"] >= 3
    assert client.delete("/api/audit").status_code == 405
    assert client.patch("/api/audit", json={}).status_code == 405

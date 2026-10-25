"""End-to-end scenarios through the real pipeline. Detector outputs are never mocked here,
except in the explicit detector-failure test, where they are made to raise."""

from __future__ import annotations

from app.database.models import EventModel, ToolExecutionModel
from app.database.repositories import audit_for_event
from app.registry.seed_agents import FINANCE_AGENT_ID, RESEARCH_AGENT_ID
from app.schemas.common import AgentStatus
from app.schemas.events import EventSubmission
from tests.conftest import requires_semantic


def run(container, scenario_id):
    return container.simulator.run(scenario_id, actor="test")


def test_legitimate_research_operation(container):
    out = run(container, "normal_research")
    assert out["all_expectations_met"], out["mismatched_steps"]
    assert all(s["decision"] == "ALLOW" and s["execution_status"] == "SUCCEEDED" for s in out["steps"])
    assert all(s["policy_status"] == "ALLOW" for s in out["steps"])
    assert out["steps"][0]["behavior_status"] == "insufficient_data"  # honest about missing history
    assert out["steps"][-1]["behavior_status"] == "ok"
    assert max(s["risk_score"] for s in out["steps"]) < 25


def test_unauthorized_finance_operation(container):
    before = container.tools.invocation_count
    out = run(container, "unauthorized_delete")
    last = out["steps"][-1]
    assert last["decision"] == "BLOCK" and last["policy_status"] == "DENY"
    assert last["execution_status"] == "NOT_EXECUTED"
    assert container.tools.invocation_count == before + 3  # only the three normal steps ran
    with container.db.session() as s:
        assert s.query(ToolExecutionModel).filter_by(event_id=last["event_id"]).count() == 0
        ev = s.get(EventModel, last["event_id"])
        assert ev.policy_result["violation_type"] == "forbidden_operation"
        kinds = [a.action_type for a in audit_for_event(s, last["event_id"])]
    assert kinds == ["action_attempted", "action_evaluated", "action_blocked"]


def test_behavioral_drift(container):
    out = run(container, "behavioral_drift")
    steps = out["steps"]
    early = [s["behavior_score"] for s in steps[3:6] if s["behavior_score"] is not None]
    drift = [s["behavior_score"] for s in steps[6:12] if s["behavior_score"] is not None]
    assert early and drift
    assert max(drift) > max(early) + 20  # anomaly evidence grows as behavior drifts
    # in-scope (policy-ALLOW) drift steps are flagged by behavior, not by policy
    flagged_allowed = [s for s in steps if s["policy_status"] == "ALLOW" and s["decision"] in ("MONITOR", "REVIEW")]
    assert flagged_allowed
    assert any(s["decision"] == "REVIEW" and s["policy_status"] == "ALLOW" for s in steps)
    # repeated high-severity blocks -> agent suspended, later actions blocked
    assert any(s["escalation"] for s in steps)
    assert container.registry.get(FINANCE_AGENT_ID).status == AgentStatus.SUSPENDED
    assert steps[-1]["decision"] == "BLOCK"


def test_scenario_runner_reactivates_with_audit(container):
    run(container, "behavioral_drift")
    out = run(container, "unauthorized_delete")
    assert out["preconditions"] and "reactivated" in out["preconditions"][0]


@requires_semantic
def test_semantic_mismatch(container):
    out = run(container, "semantic_mismatch")
    last = out["steps"][-1]
    assert last["semantic_score"] > 60
    normal = [s["semantic_score"] for s in out["steps"][:3]]
    assert max(normal) < 25
    assert last["decision"] == "BLOCK" and last["policy_status"] == "DENY"


def test_legitimate_but_unusual_action(container):
    out = run(container, "legitimate_unusual")
    last = out["steps"][-1]
    assert last["policy_status"] == "ALLOW"
    assert last["decision"] in ("MONITOR", "REVIEW")  # flagged for scrutiny
    assert last["decision"] != "BLOCK"


def test_human_review_workflow(container):
    out = run(container, "human_review")
    held = out["steps"][-1]
    assert held["decision"] == "REVIEW" and held["execution_status"] == "AWAITING_REVIEW"
    with container.db.session() as s:
        assert s.query(ToolExecutionModel).filter_by(event_id=held["event_id"]).count() == 0
    result = container.reviews.approve(held["review_id"], "analyst-1", "authorized internal send")
    assert result["executed"] is True
    with container.db.session() as s:
        ev = s.get(EventModel, held["event_id"])
        assert ev.request_status == "EXECUTED"
        kinds = [a.action_type for a in audit_for_event(s, held["event_id"])]
    assert "review_approved" in kinds and kinds[-1] == "tool_executed"


def test_suspension_and_reactivation(container):
    def search():
        return container.events.process(EventSubmission(
            agent_id=RESEARCH_AGENT_ID, tool_name="search_public_web", action="search",
            target_resource="public:web/search", action_description="Search public energy storage research"))

    container.registry.set_status(RESEARCH_AGENT_ID, AgentStatus.SUSPENDED, "incident", "admin:test")
    assert search().decision.value == "BLOCK"
    container.registry.set_status(RESEARCH_AGENT_ID, AgentStatus.ACTIVE, "cleared", "admin:test")
    assert search().decision.value == "ALLOW"


def test_detector_failure_during_unauthorized_action(container, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("detector crashed")

    monkeypatch.setattr(container.behavior, "evaluate", broken)
    monkeypatch.setattr(container.online, "evaluate", broken)
    monkeypatch.setattr(container.intent, "evaluate", broken)
    for _ in range(3):  # give the agent history so behavior would normally run
        container.events.process(EventSubmission(
            agent_id=FINANCE_AGENT_ID, tool_name="read_expenses", action="read",
            target_resource="finance:expenses/2026-01", action_description="Read expenses"))
    before = container.tools.invocation_count
    r = container.events.process(EventSubmission(
        agent_id=FINANCE_AGENT_ID, tool_name="delete_customer_records", action="delete",
        target_resource="customers:records/all", action_description="Delete customer records"))
    assert r.behavioral_result["status"] == "error" and r.semantic_result["status"] == "error"
    assert r.decision.value == "BLOCK" and r.execution_status.value == "NOT_EXECUTED"
    assert container.tools.invocation_count == before
    assert r.risk_breakdown["available"]["behavior"] is False


def test_detector_failure_holds_high_impact_permitted_action(container, monkeypatch):
    monkeypatch.setattr(container.intent, "evaluate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    for _ in range(3):
        container.events.process(EventSubmission(
            agent_id=FINANCE_AGENT_ID, tool_name="read_expenses", action="read",
            target_resource="finance:expenses/2026-01", action_description="Read expenses"))
    r = container.events.process(EventSubmission(
        agent_id=FINANCE_AGENT_ID, tool_name="read_vendor_payments", action="read",
        target_resource="finance:vendor_payments/2026-01", action_description="Read vendor payments"))
    assert r.policy_result["status"] == "ALLOW"
    assert r.decision.value == "REVIEW"  # fail-closed: confidential + missing semantic signal
    assert r.risk_breakdown["available"]["semantic"] is False
    assert any("fail-closed" in e for e in r.explanations)

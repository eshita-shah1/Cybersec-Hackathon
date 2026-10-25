"""Runs scenarios by submitting their steps to the real EventService pipeline."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.registry.agent_registry import AgentRegistry
from app.schemas.common import AgentStatus
from app.schemas.events import EventSubmission
from app.services.event_service import EventService, ServiceError
from app.simulator.scenarios import SCENARIOS, Scenario

logger = logging.getLogger(__name__)


def list_scenarios() -> list[dict[str, Any]]:
    return [{"scenario_id": s.scenario_id, "title": s.title, "description": s.description, "agent_id": s.agent_id,
             "expected_outcome": s.expected_outcome, "steps": len(s.steps)} for s in SCENARIOS.values()]


class AgentSimulator:
    def __init__(self, events: EventService, registry: AgentRegistry) -> None:
        self.events = events
        self.registry = registry

    def run(self, scenario_id: str, actor: str = "scenario_runner") -> dict[str, Any]:
        scenario: Scenario | None = SCENARIOS.get(scenario_id)
        if scenario is None:
            raise ServiceError("scenario_not_found", f"Unknown scenario '{scenario_id}'", 404)
        run_id = uuid.uuid4().hex[:10]
        preconditions: list[str] = []
        profile = self.registry.get(scenario.agent_id)
        if profile is None:
            raise ServiceError("agent_not_found", f"Scenario agent '{scenario.agent_id}' is not registered; "
                               "seed the database first", 409)
        if profile.status == AgentStatus.SUSPENDED:
            # Authorized (admin-protected endpoint), audited reactivation so the demo is repeatable.
            self.registry.set_status(scenario.agent_id, AgentStatus.ACTIVE,
                                     f"Reactivated by scenario runner before run {run_id}", f"admin:{actor}")
            preconditions.append(f"{scenario.agent_id} was suspended and was reactivated (audited) before the run")

        steps_out = []
        for i, step in enumerate(scenario.steps, start=1):
            sub = EventSubmission(
                agent_id=scenario.agent_id, tool_name=step.tool_name, action=step.action,
                target_resource=step.target_resource, action_description=step.action_description,
                parameters=step.parameters, destination=step.destination,
                task_id=f"scenario-{scenario_id}", request_id=f"scn-{run_id}-{i:02d}",
                correlation_id=f"run-{run_id}")
            r = self.events.process(sub)
            beh = r.behavioral_result or {}
            sem = r.semantic_result or {}
            decision = r.decision.value if r.decision else None
            steps_out.append({
                "step": i, "event_id": r.event_id, "tool_name": r.tool_name, "action": r.action,
                "target_resource": r.target_resource, "policy_status": (r.policy_result or {}).get("status"),
                "behavior_status": beh.get("status"), "behavior_score": beh.get("score"),
                "semantic_score": sem.get("score"), "risk_score": r.risk_score,
                "risk_level": r.risk_level.value if r.risk_level else None, "decision": decision,
                "request_status": r.request_status.value, "execution_status": r.execution_status.value,
                "review_id": r.review_id, "escalation": r.escalation,
                "expected": list(step.expect), "matches_expectation": (not step.expect) or decision in step.expect,
                "note": step.note, "top_reason": r.explanations[0] if r.explanations else ""})
        mismatches = [s["step"] for s in steps_out if not s["matches_expectation"]]
        decisions: dict[str, int] = {}
        for s in steps_out:
            decisions[s["decision"]] = decisions.get(s["decision"], 0) + 1
        return {"scenario_id": scenario_id, "title": scenario.title, "run_id": run_id, "agent_id": scenario.agent_id,
                "expected_outcome": scenario.expected_outcome, "preconditions": preconditions,
                "decision_counts": decisions, "all_expectations_met": not mismatches,
                "mismatched_steps": mismatches, "steps": steps_out}

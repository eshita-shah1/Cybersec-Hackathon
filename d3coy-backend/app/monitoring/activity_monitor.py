"""Agent Activity Monitor: validates and records attempts before execution and serves activity history."""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.database.models import EventModel
from app.database.repositories import (
    alerts_for_event,
    append_audit,
    audit_for_event,
    detections_for_event,
    execution_for_event,
    new_id,
    recent_events,
    review_for_event,
)
from app.detection.feature_extractor import ActivityRecord
from app.monitoring.event_normalizer import NormalizedAction
from app.schemas.agents import AgentProfile
from app.schemas.common import ExecutionStatus, RequestStatus, utcnow
from app.schemas.events import EventDetail, EventResult, EventSubmission


class EventValidationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ActivityMonitor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate_timestamp(self, sub: EventSubmission) -> None:
        if sub.timestamp is None:
            return
        ts = sub.timestamp if sub.timestamp.tzinfo else sub.timestamp.replace(tzinfo=timezone.utc)
        now = utcnow()
        if ts > now + timedelta(seconds=self.settings.max_clock_skew_seconds):
            raise EventValidationError("timestamp is in the future beyond the allowed clock skew")
        if ts < now - timedelta(hours=self.settings.max_event_age_hours):
            raise EventValidationError(f"timestamp is older than {self.settings.max_event_age_hours} hours")

    def record_attempt(self, s: Session, sub: EventSubmission, action: NormalizedAction, profile: AgentProfile,
                       request_id: str) -> EventModel:
        """Persist the attempt (status ATTEMPTED) before any evaluation or execution."""
        ev = EventModel(
            event_id=new_id("evt"),
            agent_id=profile.agent_id,
            task_id=sub.task_id,
            request_id=request_id,
            correlation_id=sub.correlation_id or new_id("cor"),
            client_timestamp=sub.timestamp,
            tool_name=action.tool_name,
            action=action.operation,
            target_resource=action.resource,
            resource_sensitivity=action.sensitivity.value,
            claimed_sensitivity=action.claimed_sensitivity.value if action.claimed_sensitivity else None,
            destination=action.destination,
            action_description=action.action_description,
            parameters=action.parameters,
            assigned_task_snapshot=f"{profile.assigned_task} — {profile.task_description}",
            role_snapshot=profile.role,
            request_status=RequestStatus.ATTEMPTED.value,
            execution_status=ExecutionStatus.NOT_STARTED.value,
            response_reasons=[],
        )
        s.add(ev)
        s.flush()
        append_audit(s, actor=f"agent:{profile.agent_id}", action_type="action_attempted", event_id=ev.event_id,
                     agent_id=profile.agent_id,
                     details={"tool": action.tool_name, "operation": action.operation, "resource": action.resource,
                              "request_id": request_id})
        return ev

    def history(self, s: Session, agent_id: str, exclude_event_id: str) -> list[ActivityRecord]:
        rows = recent_events(s, agent_id, self.settings.behavior_long_history, exclude_event_id)
        return [ActivityRecord(r.tool_name, r.action, r.target_resource, r.resource_sensitivity,
                               r.policy_status or "ALLOW", r.received_at) for r in rows]

    # ---- serialization ----
    @staticmethod
    def to_result(s: Session, ev: EventModel, duplicate: bool = False) -> EventResult:
        review = review_for_event(s, ev.event_id)
        alerts = alerts_for_event(s, ev.event_id)
        data = {c: getattr(ev, c) for c in EventResult.model_fields if hasattr(ev, c)}
        data.update(explanations=list(ev.response_reasons or []), review_id=review.review_id if review else None,
                    alert_ids=[a.alert_id for a in alerts], duplicate=duplicate)
        return EventResult.model_validate(data)

    @staticmethod
    def to_detail(s: Session, ev: EventModel) -> EventDetail:
        base = ActivityMonitor.to_result(s, ev).model_dump()
        exe = execution_for_event(s, ev.event_id)
        base.update(
            assigned_task_snapshot=ev.assigned_task_snapshot, role_snapshot=ev.role_snapshot,
            destination=ev.destination, parameters=ev.parameters or {}, features=ev.features, error=ev.error,
            detections=[{"detector": d.detector, "status": d.status, "score": d.score, "details": d.details,
                         "created_at": d.created_at.isoformat()} for d in detections_for_event(s, ev.event_id)],
            tool_execution=_execution_dict(exe),
            audit_trail=[_audit_dict(a) for a in audit_for_event(s, ev.event_id)],
        )
        return EventDetail.model_validate(base)


def _execution_dict(exe: Any) -> dict[str, Any] | None:
    if exe is None:
        return None
    return {"execution_id": exe.execution_id, "tool_name": exe.tool_name, "status": exe.status,
            "started_at": exe.started_at.isoformat(), "finished_at": exe.finished_at.isoformat() if exe.finished_at
            else None, "output": exe.output, "error": exe.error}


def _audit_dict(a: Any) -> dict[str, Any]:
    return {"audit_id": a.audit_id, "created_at": a.created_at.isoformat(), "actor": a.actor,
            "action_type": a.action_type, "event_id": a.event_id, "agent_id": a.agent_id, "details": a.details}


audit_dict = _audit_dict

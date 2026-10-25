"""Human review workflow for held actions and security alerts.

Approval never bypasses hard policy: the action is re-evaluated against the agent's *current*
profile and status before execution. RESTRICTED (authorization-required) actions become
executable through approval — that approval *is* the explicit authorization. DENY stays DENY.
Each review is claimed atomically, so one review can never execute an action twice.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update

from app.database.db import Database
from app.database.models import AlertModel, EventModel, ReviewModel
from app.database.repositories import append_audit, transition_event
from app.detection.policy_engine import PolicyEngine
from app.enforcement.gateway import EnforcementGateway, ExecutionRefused
from app.monitoring.activity_monitor import ActivityMonitor
from app.monitoring.event_normalizer import EventNormalizer
from app.registry.agent_registry import AgentRegistry
from app.schemas.common import ExecutionStatus, PolicyStatus, RequestStatus, Sensitivity, utcnow
from app.schemas.events import EventSubmission
from app.services.event_service import EventService, ServiceError
from app.services.websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)

ALERT_REVIEW_STATUSES = {"acknowledged", "resolved", "false_positive", "escalated"}


def review_dict(r: ReviewModel) -> dict[str, Any]:
    return {"review_id": r.review_id, "event_id": r.event_id, "agent_id": r.agent_id, "status": r.status,
            "reason": r.reason, "created_at": r.created_at.isoformat(),
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None, "decided_by": r.decided_by,
            "notes": r.notes, "revalidation": r.revalidation}


def alert_dict(a: AlertModel) -> dict[str, Any]:
    return {"alert_id": a.alert_id, "event_id": a.event_id, "agent_id": a.agent_id, "alert_type": a.alert_type,
            "severity": a.severity, "title": a.title, "description": a.description, "status": a.status,
            "created_at": a.created_at.isoformat(), "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at
            else None, "reviewed_by": a.reviewed_by, "review_notes": a.review_notes}


class ReviewService:
    def __init__(self, db: Database, registry: AgentRegistry, normalizer: EventNormalizer, policy: PolicyEngine,
                 gateway: EnforcementGateway, events: EventService, ws: WebSocketManager) -> None:
        self.db = db
        self.registry = registry
        self.normalizer = normalizer
        self.policy = policy
        self.gateway = gateway
        self.events = events
        self.ws = ws

    # ---- held actions ----
    def list_reviews(self, status: str | None = "pending", limit: int = 100) -> list[dict[str, Any]]:
        with self.db.session() as s:
            q = select(ReviewModel).order_by(ReviewModel.created_at.desc()).limit(limit)
            if status:
                q = q.where(ReviewModel.status == status)
            return [review_dict(r) for r in s.scalars(q)]

    def _expire_if_due(self, review_id: str, reviewer: str) -> None:
        """If a pending review has passed its expiry, close it (rejected) and block the action."""
        with self.db.session() as s:
            r = s.get(ReviewModel, review_id)
            if r is None or r.status != "pending" or r.expires_at is None or r.expires_at > utcnow():
                return
            r.status, r.decided_at, r.decided_by = "expired", utcnow(), f"system:expiry"
            r.notes = "Review window elapsed before a decision; the held action was not executed."
            transition_event(s, r.event_id, [RequestStatus.PENDING_REVIEW.value],
                             request_status=RequestStatus.BLOCKED.value,
                             execution_status=ExecutionStatus.NOT_EXECUTED.value, updated_at=utcnow())
            append_audit(s, actor="system:expiry", action_type="review_expired", event_id=r.event_id,
                         agent_id=r.agent_id, details={"review_id": review_id})
        raise ServiceError("review_expired", "This review has expired; the action was not executed", 409)

    def _claim(self, review_id: str, new_status: str) -> ReviewModel:
        with self.db.session() as s:
            if s.get(ReviewModel, review_id) is None:
                raise ServiceError("review_not_found", f"Review '{review_id}' not found", 404)
            n = s.execute(update(ReviewModel).where(ReviewModel.review_id == review_id,
                                                    ReviewModel.status == "pending")
                          .values(status=new_status).execution_options(synchronize_session=False)).rowcount
            if n != 1:
                current = s.get(ReviewModel, review_id)
                s.refresh(current)
                raise ServiceError("review_already_decided",
                                   f"Review is already '{current.status}' and cannot be decided again", 409)
            return s.get(ReviewModel, review_id)

    def approve(self, review_id: str, reviewer: str, notes: str | None) -> dict[str, Any]:
        self._expire_if_due(review_id, reviewer)
        review = self._claim(review_id, "processing")
        with self.db.session() as s:
            ev = s.get(EventModel, review.event_id)
            sub = EventSubmission(agent_id=ev.agent_id, tool_name=ev.tool_name, action=ev.action,
                                  target_resource=ev.target_resource, action_description=ev.action_description,
                                  destination=ev.destination, parameters=ev.parameters or {},
                                  resource_sensitivity=Sensitivity(ev.claimed_sensitivity)
                                  if ev.claimed_sensitivity else None)
        profile = self.registry.get(sub.agent_id)
        try:
            revalidation = self.policy.evaluate(profile, self.normalizer.normalize(sub))
        except Exception:
            logger.exception("review=%s policy revalidation error", review_id)
            revalidation = PolicyEngine.engine_failure("revalidation error")
        reval = {"status": revalidation.status.value, "explanation": revalidation.explanation,
                 "agent_status": profile.status.value if profile else "unknown",
                 "profile_version": profile.version if profile else None}

        if revalidation.status == PolicyStatus.DENY:
            with self.db.session() as s:
                r = s.get(ReviewModel, review_id)
                r.status, r.decided_at, r.decided_by, r.notes, r.revalidation = (
                    "revalidation_failed", utcnow(), reviewer, notes, reval)
                transition_event(s, r.event_id, [RequestStatus.PENDING_REVIEW.value],
                                 request_status=RequestStatus.BLOCKED.value,
                                 execution_status=ExecutionStatus.NOT_EXECUTED.value, updated_at=utcnow())
                append_audit(s, actor=f"analyst:{reviewer}", action_type="review_revalidation_failed",
                             event_id=r.event_id, agent_id=r.agent_id, details={"notes": notes, **reval})
                result = review_dict(r)
            self._publish(review.event_id)
            return {**result, "executed": False,
                    "message": "Approval recorded, but policy revalidation denied the action; it was not executed."}

        with self.db.session() as s:
            r = s.get(ReviewModel, review_id)
            r.status, r.decided_at, r.decided_by, r.notes, r.revalidation = ("approved", utcnow(), reviewer, notes,
                                                                             reval)
            ok = transition_event(s, r.event_id, [RequestStatus.PENDING_REVIEW.value],
                                  request_status=RequestStatus.APPROVED.value, updated_at=utcnow())
            if not ok:
                raise ServiceError("invalid_state", "The held action is no longer pending", 409)
            append_audit(s, actor=f"analyst:{reviewer}", action_type="review_approved", event_id=r.event_id,
                         agent_id=r.agent_id, details={"notes": notes, **reval})
        try:
            outcome = self.gateway.execute(review.event_id, actor=f"analyst:{reviewer}")
        except ExecutionRefused as exc:
            outcome = {"status": "REFUSED", "error": str(exc)}
        with self.db.session() as s:
            result = review_dict(s.get(ReviewModel, review_id))
        self._publish(review.event_id)
        return {**result, "executed": outcome.get("status") == ExecutionStatus.SUCCEEDED.value,
                "execution": outcome}

    def reject(self, review_id: str, reviewer: str, notes: str | None) -> dict[str, Any]:
        self._claim(review_id, "rejected")
        with self.db.session() as s:
            r = s.get(ReviewModel, review_id)
            r.decided_at, r.decided_by, r.notes = utcnow(), reviewer, notes
            transition_event(s, r.event_id, [RequestStatus.PENDING_REVIEW.value],
                             request_status=RequestStatus.REJECTED.value,
                             execution_status=ExecutionStatus.NOT_EXECUTED.value, updated_at=utcnow())
            append_audit(s, actor=f"analyst:{reviewer}", action_type="review_rejected", event_id=r.event_id,
                         agent_id=r.agent_id, details={"notes": notes})
            result = review_dict(r)
        self._publish(result["event_id"])
        return {**result, "executed": False}

    def _publish(self, event_id: str) -> None:
        with self.db.session() as s:
            res = ActivityMonitor.to_result(s, s.get(EventModel, event_id))
        self.events.publish_event(res)

    # ---- alerts ----
    def review_alert(self, alert_id: str, status: str, reviewer: str, notes: str | None) -> dict[str, Any]:
        if status not in ALERT_REVIEW_STATUSES:
            raise ServiceError("invalid_review_status", f"status must be one of {sorted(ALERT_REVIEW_STATUSES)}", 422)
        with self.db.session() as s:
            a = s.get(AlertModel, alert_id)
            if a is None:
                raise ServiceError("alert_not_found", f"Alert '{alert_id}' not found", 404)
            if a.status in {"resolved", "false_positive"}:
                raise ServiceError("alert_closed", f"Alert is already '{a.status}'", 409)
            previous = a.status
            a.status, a.reviewed_at, a.reviewed_by, a.review_notes = status, utcnow(), reviewer, notes
            append_audit(s, actor=f"analyst:{reviewer}", action_type="alert_reviewed", event_id=a.event_id,
                         agent_id=a.agent_id, details={"alert_id": alert_id, "from": previous, "to": status,
                                                       "notes": notes})
            result = alert_dict(a)
        self.ws.publish("alert_update", result)
        return result

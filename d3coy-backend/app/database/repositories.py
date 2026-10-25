"""Small query helpers shared by services. All functions take an open Session."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database.models import (
    AlertModel,
    AuditRecordModel,
    DetectionResultModel,
    EventModel,
    ReviewModel,
    ToolExecutionModel,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


# ---- audit (append-only) -------------------------------------------------------
def append_audit(
    s: Session,
    *,
    actor: str,
    action_type: str,
    event_id: str | None = None,
    agent_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    s.add(AuditRecordModel(actor=actor, action_type=action_type, event_id=event_id,
                           agent_id=agent_id, details=details or {}))


def audit_for_event(s: Session, event_id: str) -> list[AuditRecordModel]:
    q = select(AuditRecordModel).where(AuditRecordModel.event_id == event_id).order_by(AuditRecordModel.audit_id)
    return list(s.scalars(q))


def list_audit(s: Session, *, agent_id: str | None, action_type: str | None, limit: int, offset: int):
    q = select(AuditRecordModel)
    if agent_id:
        q = q.where(AuditRecordModel.agent_id == agent_id)
    if action_type:
        q = q.where(AuditRecordModel.action_type == action_type)
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(s.scalars(q.order_by(AuditRecordModel.audit_id.desc()).limit(limit).offset(offset)))
    return rows, total


# ---- events ------------------------------------------------------------------------
def find_by_request_id(s: Session, agent_id: str, request_id: str) -> EventModel | None:
    q = select(EventModel).where(EventModel.agent_id == agent_id, EventModel.request_id == request_id)
    return s.scalars(q).first()


def recent_events(s: Session, agent_id: str, limit: int, exclude_event_id: str | None = None) -> list[EventModel]:
    """Most recent evaluated events for an agent, returned in chronological order."""
    q = select(EventModel).where(EventModel.agent_id == agent_id, EventModel.policy_status.is_not(None))
    if exclude_event_id:
        q = q.where(EventModel.event_id != exclude_event_id)
    q = q.order_by(EventModel.received_at.desc(), EventModel.event_id.desc()).limit(limit)
    return list(reversed(list(s.scalars(q))))


def count_recent_high_severity_blocks(s: Session, agent_id: str, since: datetime) -> int:
    q = select(func.count()).select_from(EventModel).where(
        EventModel.agent_id == agent_id,
        EventModel.decision == "BLOCK",
        EventModel.policy_severity.in_(["high", "critical"]),
        EventModel.received_at >= since,
    )
    return s.scalar(q) or 0


def transition_event(s: Session, event_id: str, from_statuses: list[str], **values: Any) -> bool:
    """Atomic compare-and-set on request_status. Returns False if the event was not in an expected state."""
    stmt = (
        update(EventModel)
        .where(EventModel.event_id == event_id, EventModel.request_status.in_(from_statuses))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return s.execute(stmt).rowcount == 1


def query_events(
    s: Session,
    *,
    agent_id: str | None = None,
    decision: str | None = None,
    risk_level: str | None = None,
    execution_status: str | None = None,
    request_status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[EventModel], int]:
    q = select(EventModel)
    if agent_id:
        q = q.where(EventModel.agent_id == agent_id)
    if decision:
        q = q.where(EventModel.decision == decision)
    if risk_level:
        q = q.where(EventModel.risk_level == risk_level)
    if execution_status:
        q = q.where(EventModel.execution_status == execution_status)
    if request_status:
        q = q.where(EventModel.request_status == request_status)
    if since:
        q = q.where(EventModel.received_at >= since)
    if until:
        q = q.where(EventModel.received_at <= until)
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = list(s.scalars(q.order_by(EventModel.received_at.desc(), EventModel.event_id.desc())
                          .limit(limit).offset(offset)))
    return rows, total


def detections_for_event(s: Session, event_id: str) -> list[DetectionResultModel]:
    q = select(DetectionResultModel).where(DetectionResultModel.event_id == event_id).order_by(DetectionResultModel.id)
    return list(s.scalars(q))


def execution_for_event(s: Session, event_id: str) -> ToolExecutionModel | None:
    return s.scalars(select(ToolExecutionModel).where(ToolExecutionModel.event_id == event_id)).first()


def review_for_event(s: Session, event_id: str) -> ReviewModel | None:
    return s.scalars(select(ReviewModel).where(ReviewModel.event_id == event_id)).first()


def alerts_for_event(s: Session, event_id: str) -> list[AlertModel]:
    return list(s.scalars(select(AlertModel).where(AlertModel.event_id == event_id)))

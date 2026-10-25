from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container, require_agent_identity
from app.database.models import EventModel
from app.database.repositories import list_audit, query_events
from app.monitoring.activity_monitor import ActivityMonitor, audit_dict
from app.schemas.common import Decision, ExecutionStatus, Page, RequestStatus, RiskLevel
from app.schemas.events import EventDetail, EventResult, EventSubmission
from app.services.container import Container
from app.services.event_service import ServiceError

router = APIRouter(tags=["events"])


@router.post("/api/events", response_model=EventResult)
def submit_event(body: EventSubmission, c: Container = Depends(get_container),
                 identity: str | None = Depends(require_agent_identity)):
    """Run an attempted action through the full monitoring and enforcement pipeline.

    The acting agent is authenticated from its bearer token; the trusted profile is resolved
    from that identity, not from the request body. Returns 200 for every evaluated action,
    including BLOCK and REVIEW outcomes. Re-sending the same ``request_id`` returns the original
    result with ``duplicate: true`` and never re-executes."""
    return c.events.process(body, identity=identity)


@router.get("/api/events", response_model=Page[EventResult])
def list_events(
    agent_id: str | None = None,
    decision: Decision | None = None,
    risk_level: RiskLevel | None = None,
    execution_status: ExecutionStatus | None = None,
    request_status: RequestStatus | None = None,
    since: datetime | None = Query(default=None, description="ISO-8601, inclusive"),
    until: datetime | None = Query(default=None, description="ISO-8601, inclusive"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    c: Container = Depends(get_container),
):
    with c.db.session() as s:
        rows, total = query_events(
            s, agent_id=agent_id, decision=decision.value if decision else None,
            risk_level=risk_level.value if risk_level else None,
            execution_status=execution_status.value if execution_status else None,
            request_status=request_status.value if request_status else None,
            since=since, until=until, limit=limit, offset=offset)
        items = [ActivityMonitor.to_result(s, r) for r in rows]
    return Page[EventResult](items=items, total=total, limit=limit, offset=offset)


@router.get("/api/events/{event_id}", response_model=EventDetail)
def get_event(event_id: str, c: Container = Depends(get_container)):
    with c.db.session() as s:
        ev = s.get(EventModel, event_id)
        if ev is None:
            raise ServiceError("event_not_found", f"Event '{event_id}' not found", 404)
        return ActivityMonitor.to_detail(s, ev)


@router.get("/api/events/{event_id}/output")
def get_event_output(event_id: str, c: Container = Depends(get_container),
                     identity: str | None = Depends(require_agent_identity)):
    """Return the full tool output for an event. Only the authenticated owning agent may read it;
    the public event views only ever expose a summary."""
    from app.database.models import ExecutionOutputModel

    with c.db.session() as s:
        ev = s.get(EventModel, event_id)
        if ev is None:
            raise ServiceError("event_not_found", f"Event '{event_id}' not found", 404)
        if identity is not None and ev.agent_id != identity:
            raise ServiceError("forbidden", "This event belongs to a different agent", 403)
        row = s.get(ExecutionOutputModel, event_id)
        if row is None:
            raise ServiceError("no_output", "No stored output for this event", 404)
        return {"event_id": event_id, "agent_id": row.agent_id, "output": row.output}


@router.get("/api/audit", tags=["audit"])
def list_audit_records(agent_id: str | None = None, action_type: str | None = None,
                       limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0),
                       c: Container = Depends(get_container)) -> dict[str, Any]:
    """Read-only, append-oriented audit history (there is no update or delete endpoint)."""
    with c.db.session() as s:
        rows, total = list_audit(s, agent_id=agent_id, action_type=action_type, limit=limit, offset=offset)
        return {"items": [audit_dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}

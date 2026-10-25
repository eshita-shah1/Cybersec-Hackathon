from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import get_container
from app.database.models import AgentModel, AlertModel, EventModel, ReviewModel
from app.schemas.common import utcnow
from app.services.container import Container

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def _group(s, column) -> dict[str, int]:  # noqa: ANN001
    return {str(k): int(v) for k, v in s.execute(select(column, func.count()).group_by(column)).all()
            if k is not None}


@router.get("/overview")
def overview(c: Container = Depends(get_container)) -> dict[str, Any]:
    """Dashboard statistics, computed from the database on every request."""
    with c.db.session() as s:
        agents_by_status = _group(s, AgentModel.status)
        decisions = _group(s, EventModel.decision)
        risk_levels = _group(s, EventModel.risk_level)
        exec_status = _group(s, EventModel.execution_status)
        total_events = s.scalar(select(func.count()).select_from(EventModel)) or 0
        pending = s.scalar(select(func.count()).select_from(ReviewModel).where(ReviewModel.status == "pending")) or 0
        open_alerts = s.scalar(select(func.count()).select_from(AlertModel).where(AlertModel.status == "open")) or 0
        total_alerts = s.scalar(select(func.count()).select_from(AlertModel)) or 0
        avg_risk = s.scalar(select(func.avg(EventModel.risk_score)))
        since = utcnow() - timedelta(hours=1)
        last_hour = s.scalar(select(func.count()).select_from(EventModel).where(EventModel.received_at >= since)) or 0
        per_agent = {a: {"events": n} for a, n in s.execute(
            select(EventModel.agent_id, func.count()).group_by(EventModel.agent_id)).all()}
        for a, n in s.execute(select(EventModel.agent_id, func.count()).where(EventModel.decision == "BLOCK")
                              .group_by(EventModel.agent_id)).all():
            per_agent.setdefault(a, {"events": 0})["blocked"] = n
        recent = [{"event_id": e.event_id, "agent_id": e.agent_id, "tool_name": e.tool_name,
                   "decision": e.decision, "risk_score": e.risk_score, "risk_level": e.risk_level,
                   "received_at": e.received_at.isoformat()}
                  for e in s.scalars(select(EventModel).order_by(EventModel.received_at.desc()).limit(10))]
    flagged = sum(v for k, v in decisions.items() if k in ("MONITOR", "REVIEW", "BLOCK"))
    return {
        "agents": {"total": sum(agents_by_status.values()), "active": agents_by_status.get("active", 0),
                   "suspended": agents_by_status.get("suspended", 0),
                   "deactivated": agents_by_status.get("deactivated", 0)},
        "events": {"total": total_events, "last_hour": last_hour,
                   "allowed": decisions.get("ALLOW", 0), "monitored": decisions.get("MONITOR", 0),
                   "held_for_review": decisions.get("REVIEW", 0), "blocked": decisions.get("BLOCK", 0),
                   "flagged": flagged, "executed": exec_status.get("SUCCEEDED", 0),
                   "failed": exec_status.get("FAILED", 0),
                   "average_risk_score": round(float(avg_risk), 1) if avg_risk is not None else None},
        "pending_reviews": pending,
        "alerts": {"total": total_alerts, "open": open_alerts},
        "decision_distribution": decisions,
        "risk_level_distribution": risk_levels,
        "execution_status_distribution": exec_status,
        "per_agent": per_agent,
        "recent_events": recent,
        "generated_at": utcnow().isoformat(),
    }

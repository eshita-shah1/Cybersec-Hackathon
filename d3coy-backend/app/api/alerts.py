from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import get_container, require_admin
from app.database.models import AlertModel, ReviewModel
from app.schemas.alerts import AlertReview, ReviewDecision
from app.services.container import Container
from app.services.event_service import ServiceError
from app.services.review_service import alert_dict, review_dict

router = APIRouter(tags=["alerts & reviews"])


@router.get("/api/alerts")
def list_alerts(status: str | None = None, severity: str | None = None, agent_id: str | None = None,
                alert_type: str | None = None, limit: int = Query(default=50, ge=1, le=500),
                offset: int = Query(default=0, ge=0), c: Container = Depends(get_container)) -> dict[str, Any]:
    with c.db.session() as s:
        q = select(AlertModel)
        for col, val in ((AlertModel.status, status), (AlertModel.severity, severity),
                         (AlertModel.agent_id, agent_id), (AlertModel.alert_type, alert_type)):
            if val:
                q = q.where(col == val)
        total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = s.scalars(q.order_by(AlertModel.created_at.desc()).limit(limit).offset(offset))
        return {"items": [alert_dict(a) for a in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/api/alerts/{alert_id}")
def get_alert(alert_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    with c.db.session() as s:
        a = s.get(AlertModel, alert_id)
        if a is None:
            raise ServiceError("alert_not_found", f"Alert '{alert_id}' not found", 404)
        return alert_dict(a)


@router.post("/api/alerts/{alert_id}/review")
def review_alert(alert_id: str, body: AlertReview, c: Container = Depends(get_container),
                 _: str = Depends(require_admin)) -> dict[str, Any]:
    """Record an analyst's triage of an alert. This does NOT approve or execute any held action."""
    return c.reviews.review_alert(alert_id, body.status, body.reviewer, body.notes)


@router.get("/api/reviews/pending")
def pending_reviews(c: Container = Depends(get_container)) -> dict[str, Any]:
    items = c.reviews.list_reviews("pending")
    return {"items": items, "total": len(items)}


@router.get("/api/reviews/{review_id}")
def get_review(review_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    with c.db.session() as s:
        r = s.get(ReviewModel, review_id)
        if r is None:
            raise ServiceError("review_not_found", f"Review '{review_id}' not found", 404)
        return review_dict(r)


@router.post("/api/reviews/{review_id}/approve")
def approve_review(review_id: str, body: ReviewDecision, c: Container = Depends(get_container),
                   _: str = Depends(require_admin)) -> dict[str, Any]:
    """Approve a held action. Policy is revalidated first; a hard denial still prevents execution."""
    return c.reviews.approve(review_id, body.reviewer, body.notes)


@router.post("/api/reviews/{review_id}/reject")
def reject_review(review_id: str, body: ReviewDecision, c: Container = Depends(get_container),
                  _: str = Depends(require_admin)) -> dict[str, Any]:
    return c.reviews.reject(review_id, body.reviewer, body.notes)

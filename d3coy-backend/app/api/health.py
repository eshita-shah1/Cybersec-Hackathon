from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.schemas.common import utcnow
from app.services.container import Container

router = APIRouter(tags=["health"])


@router.get("/health")
def health(c: Container = Depends(get_container)) -> dict[str, Any]:
    """Lightweight liveness check (does not touch models)."""
    return {"status": "ok", "app": c.settings.app_name, "environment": c.settings.app_env,
            "time": utcnow().isoformat()}


@router.get("/api/system/status")
def system_status(c: Container = Depends(get_container)) -> dict[str, Any]:
    """Component, detector and database status. Never includes secrets."""
    db_ok = c.db.ping()
    return {
        "app": c.settings.app_name,
        "database": {"status": "ok" if db_ok else "error",
                     "backend": c.db.url.split(":", 1)[0]},
        "components": {
            "agent_registry": "ready",
            "activity_monitor": "ready",
            "policy_engine": "ready",
            "risk_engine": "ready",
            "enforcement_gateway": "ready",
            "websocket": {"status": "ready", "connections": c.ws.connection_count},
        },
        "detectors": {
            "isolation_forest": c.behavior.status(),
            "river": c.online.status(),
            "semantic": c.intent.status(),
        },
        "risk": {"weights": c.scorer.weights,
                 "thresholds": {"monitor": c.scorer.monitor, "review": c.scorer.review, "block": c.scorer.block},
                 "fail_closed_on_unavailable_detectors": c.settings.fail_closed_on_unavailable_detectors},
        "security": {"admin_key_configured": c.settings.admin_key_configured,
                     "agent_auth_required": c.settings.agent_auth_required,
                     "permission_signing_key_configured": bool(c.settings.permission_signing_key and
                                                               c.settings.permission_signing_key.get_secret_value()),
                     "cors_origins": c.settings.cors_origin_list},
        "executor": {"url": c.settings.executor_url, "health": c.executor.health()},
        "tools": c.tools.describe(),
    }

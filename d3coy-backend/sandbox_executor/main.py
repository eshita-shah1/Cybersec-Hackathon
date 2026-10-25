"""Sandbox tool-executor service — a SEPARATE FastAPI app from D3COY.

It executes real (dummy-data) tools, but only when presented with a valid, unexpired,
single-use permission slip signed by the shared PERMISSION_SIGNING_KEY. It re-verifies every
permission itself (defence in depth: it does not trust that D3COY already checked), binds the
permission to the exact tool/operation/resource/arguments, and consumes it atomically so a
replayed or concurrent permission can execute at most once.

This service holds the only connection to the sandbox database. The agent has no credentials
for it and no network route to it except this endpoint.

Run:  uvicorn sandbox_executor.main:app --port 8100
Env:  SANDBOX_DATABASE_URL (default sqlite:///./data/sandbox.db), PERMISSION_SIGNING_KEY
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.enforcement.permissions import Permission, PermissionRejected, verify
from sandbox_executor import store as st
from sandbox_executor.tools import ToolError, execute

logger = logging.getLogger("sandbox_executor")

SIGNING_KEY = os.environ.get("PERMISSION_SIGNING_KEY", "").encode()
DB_URL = os.environ.get("SANDBOX_DATABASE_URL", "sqlite:///./data/sandbox.db")


class ExecuteRequest(BaseModel):
    permission: dict[str, Any]
    tool_name: str = Field(min_length=1, max_length=64)
    operation: str = Field(min_length=1, max_length=32)
    resource: str = Field(min_length=1, max_length=256)
    parameters: dict[str, Any] = Field(default_factory=dict)
    destination: str | None = None


def create_app(database_url: str | None = None, signing_key: bytes | None = None) -> FastAPI:
    key = SIGNING_KEY if signing_key is None else signing_key
    store = st.SandboxStore(database_url or DB_URL)
    app = FastAPI(title="D3COY Sandbox Tool Executor", version="1.0.0")
    app.state.store = store
    app.state.key = key

    @app.on_event("startup")
    def _startup() -> None:
        store.seed()
        if not key:
            logger.warning("PERMISSION_SIGNING_KEY is not set; the executor will reject every request.")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "sandbox_executor", "signing_key_configured": bool(app.state.key)}

    @app.get("/sandbox/state")
    def state() -> dict[str, Any]:
        """Dummy-data counts, for demos and side-effect assertions. Not real data."""
        return {"customers": store.count(st.customers), "customer_ids": store.customer_ids(),
                "suppliers": store.count(st.suppliers), "catalog_items": store.count(st.catalog_items),
                "supplier_emails": store.count(st.supplier_emails), "outbox": store.outbox_messages(),
                "consumed_permissions": store.count(st.consumed_permissions)}

    @app.post("/execute")
    def run(req: ExecuteRequest) -> JSONResponse:
        key = app.state.key
        # 1. verify signature / expiry / exact-action binding
        try:
            perm = Permission.from_dict(req.permission)
            verify(key, perm, tool_name=req.tool_name, operation=req.operation, resource=req.resource,
                   parameters=req.parameters, destination=req.destination)
        except PermissionRejected as exc:
            logger.warning("rejected permission: %s", exc.reason)
            return JSONResponse(status_code=403, content={"executed": False, "reason": exc.reason})

        # 2. atomically consume (single use) then execute inside the same transaction
        now = datetime.now(timezone.utc)
        try:
            with store.begin() as conn:
                try:
                    conn.execute(st.consumed_permissions.insert().values(
                        permission_id=perm.permission_id, event_id=perm.event_id, agent_id=perm.agent_id,
                        tool_name=perm.tool_name, consumed_at=now))
                except IntegrityError:
                    return JSONResponse(status_code=409,
                                        content={"executed": False, "reason": "permission already used"})
                output = execute(conn, req.tool_name, req.resource, req.parameters,
                                 permission_id=perm.permission_id, destination=req.destination)
                conn.execute(st.execution_log.insert().values(
                    permission_id=perm.permission_id, tool_name=req.tool_name, status="succeeded",
                    detail={"operation": req.operation, "resource": req.resource}, at=now))
            return JSONResponse(status_code=200, content={"executed": True, "output": output})
        except ToolError as exc:
            with store.begin() as conn:
                conn.execute(st.execution_log.insert().values(
                    permission_id=perm.permission_id, tool_name=req.tool_name, status="failed",
                    detail={"error": str(exc)}, at=now))
            return JSONResponse(status_code=200, content={"executed": False, "failed": True, "error": str(exc)})
        except Exception:
            logger.exception("executor internal error")
            return JSONResponse(status_code=500, content={"executed": False, "error": "internal executor error"})

    return app


app = create_app()

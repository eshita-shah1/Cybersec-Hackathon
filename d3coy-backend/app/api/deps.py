"""Shared FastAPI dependencies: container access and API-key authorization."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, Request

from app.services.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


def _check_key(expected, provided: str | None, kind: str) -> None:  # noqa: ANN001
    if not provided or not hmac.compare_digest(expected.get_secret_value().encode(), provided.encode()):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": f"Missing or invalid {kind}"})


def require_admin(
    c: Container = Depends(get_container),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str:
    """Prototype admin auth: a shared key from ADMIN_API_KEY. Not a substitute for real identity management.

    OPEN MODE: if ADMIN_API_KEY is not configured, administrative operations are allowed without
    a key. This makes the app runnable by someone who was not given a key. Set ADMIN_API_KEY to
    require the key again."""
    if not c.settings.admin_key_configured:
        return "open"
    _check_key(c.settings.admin_api_key, x_api_key, "X-API-Key")
    return "admin"


def require_agent_identity(
    c: Container = Depends(get_container),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str | None:
    """Authenticate the acting agent for POST /api/events.

    The agent presents a per-agent bearer token (`Authorization: Bearer d3a_...`), issued via
    the admin credential API. Returns the authenticated agent_id, which the pipeline trusts
    over anything in the request body. An admin key is also accepted (for the simulator / demos
    / internal callers) and returns None, meaning "trusted internal caller, use the body's
    agent_id". When AGENT_AUTH_REQUIRED is false, unauthenticated calls are allowed (None)."""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token:
        agent_id = c.credentials.authenticate(token)
        if agent_id is None:
            raise HTTPException(status_code=401, detail={"code": "invalid_agent_credential",
                                                         "message": "Invalid or revoked agent credential"})
        return agent_id
    if c.settings.admin_key_configured and x_api_key and hmac.compare_digest(
            c.settings.admin_api_key.get_secret_value().encode(), x_api_key.encode()):
        return None  # trusted internal/admin caller
    if c.settings.agent_auth_required:
        raise HTTPException(status_code=401, detail={
            "code": "authentication_required",
            "message": "POST /api/events requires an agent bearer token (Authorization: Bearer d3a_...)"})
    return None

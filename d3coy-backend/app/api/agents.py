from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_container, require_admin
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agents import AgentCreate, AgentProfile, AgentUpdate, StatusChange
from app.schemas.common import AgentStatus
from app.services.container import Container


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=128)

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("", response_model=list[AgentProfile])
def list_agents(status_filter: AgentStatus | None = Query(default=None, alias="status"),
                c: Container = Depends(get_container)):
    return c.registry.list(status_filter)


@router.get("/{agent_id}", response_model=AgentProfile)
def get_agent(agent_id: str, c: Container = Depends(get_container)):
    from app.services.event_service import ServiceError

    profile = c.registry.get(agent_id)
    if profile is None:
        raise ServiceError("agent_not_found", f"Agent '{agent_id}' not found", 404)
    return profile


@router.post("", response_model=AgentProfile, status_code=status.HTTP_201_CREATED)
def register_agent(body: AgentCreate, c: Container = Depends(get_container), _: str = Depends(require_admin)):
    return c.registry.register(body, actor="admin")


@router.patch("/{agent_id}", response_model=AgentProfile)
def update_agent(agent_id: str, body: AgentUpdate, c: Container = Depends(get_container),
                 _: str = Depends(require_admin)):
    return c.registry.update(agent_id, body, actor="admin")


@router.post("/{agent_id}/suspend", response_model=AgentProfile)
def suspend_agent(agent_id: str, body: StatusChange, c: Container = Depends(get_container),
                  _: str = Depends(require_admin)):
    profile = c.registry.set_status(agent_id, AgentStatus.SUSPENDED, body.reason, f"admin:{body.actor}")
    c.ws.publish("agent_status", {"agent_id": agent_id, "status": "suspended", "reason": body.reason})
    return profile


@router.post("/{agent_id}/reactivate", response_model=AgentProfile)
def reactivate_agent(agent_id: str, body: StatusChange, c: Container = Depends(get_container),
                     _: str = Depends(require_admin)):
    """Only SUSPENDED agents are eligible; deactivated agents stay deactivated."""
    profile = c.registry.set_status(agent_id, AgentStatus.ACTIVE, body.reason, f"admin:{body.actor}")
    c.ws.publish("agent_status", {"agent_id": agent_id, "status": "active", "reason": body.reason})
    return profile


@router.post("/{agent_id}/deactivate", response_model=AgentProfile)
def deactivate_agent(agent_id: str, body: StatusChange, c: Container = Depends(get_container),
                     _: str = Depends(require_admin)):
    profile = c.registry.set_status(agent_id, AgentStatus.DEACTIVATED, body.reason, f"admin:{body.actor}")
    c.ws.publish("agent_status", {"agent_id": agent_id, "status": "deactivated", "reason": body.reason})
    return profile


@router.get("/{agent_id}/credentials")
def list_credentials(agent_id: str, c: Container = Depends(get_container), _: str = Depends(require_admin)):
    """List an agent's credentials (metadata only; token values are never stored or returned)."""
    if c.registry.get(agent_id) is None:
        from app.services.event_service import ServiceError

        raise ServiceError("agent_not_found", f"Agent '{agent_id}' not found", 404)
    return {"items": c.credentials.list(agent_id)}


@router.post("/{agent_id}/credentials", status_code=status.HTTP_201_CREATED)
def issue_credential(agent_id: str, body: CredentialRequest, c: Container = Depends(get_container),
                     _: str = Depends(require_admin)):
    """Issue a new bearer token for an agent. The token is returned ONCE and never stored."""
    return c.credentials.issue(agent_id, actor="admin", label=body.label)


@router.post("/{agent_id}/credentials/{credential_id}/revoke")
def revoke_credential(agent_id: str, credential_id: str, c: Container = Depends(get_container),
                      _: str = Depends(require_admin)):
    c.credentials.revoke(agent_id, credential_id, actor="admin")
    return {"status": "revoked", "credential_id": credential_id}

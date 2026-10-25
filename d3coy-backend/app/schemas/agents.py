"""Pydantic models for trusted agent identity profiles."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import AgentStatus, Operation, Sensitivity

_PATTERN_RE = r"^[a-z0-9*][a-z0-9_:/.*@-]{0,127}$"


def normalize_str_list(value: list[str]) -> list[str]:
    """Lower-case, strip, de-duplicate (keeping order) and bound the size of a string list."""
    cleaned = [v.strip().lower() for v in value if v and v.strip()]
    if len(cleaned) > 100:
        raise ValueError("at most 100 entries allowed")
    return list(dict.fromkeys(cleaned))


class ResourcePermission(BaseModel):
    """Grants a set of operations on resources matching ``resource`` (glob), up to a sensitivity ceiling."""

    model_config = ConfigDict(extra="forbid")

    resource: str = Field(pattern=_PATTERN_RE, description="Glob pattern, e.g. 'finance:expenses/*'")
    operations: list[Operation] = Field(min_length=1)
    max_sensitivity: Sensitivity = Sensitivity.INTERNAL


class AgentBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_name: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    assigned_task: str = Field(min_length=3, max_length=256)
    task_description: str = Field(min_length=3, max_length=2000)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(
        default_factory=list, description="Operations (e.g. 'delete') or tool names that are always denied"
    )
    allowed_resources: list[str] = Field(default_factory=list, description="Glob patterns inside the agent's responsibility")
    restricted_resources: list[str] = Field(default_factory=list, description="Glob patterns that are always denied")
    resource_permissions: list[ResourcePermission] = Field(default_factory=list)
    review_required_tools: list[str] = Field(
        default_factory=list, description="Tools that need explicit human authorization per call"
    )
    external_transfer_allowed: bool = False
    allowed_destinations: list[str] = Field(default_factory=list, description="Glob patterns for permitted destinations")

    @field_validator("allowed_tools", "forbidden_actions", "review_required_tools", "allowed_resources",
                     "restricted_resources", "allowed_destinations")
    @classmethod
    def _normalize_list(cls, value: list[str]) -> list[str]:
        return normalize_str_list(value)


class AgentCreate(AgentBase):
    agent_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")


class AgentUpdate(BaseModel):
    """Administrative update. Status is changed only through suspend/reactivate/deactivate."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_name: str | None = Field(default=None, min_length=1, max_length=128)
    assigned_task: str | None = Field(default=None, min_length=3, max_length=256)
    task_description: str | None = Field(default=None, min_length=3, max_length=2000)
    allowed_tools: list[str] | None = None
    forbidden_actions: list[str] | None = None
    allowed_resources: list[str] | None = None
    restricted_resources: list[str] | None = None
    resource_permissions: list[ResourcePermission] | None = None
    review_required_tools: list[str] | None = None
    external_transfer_allowed: bool | None = None
    allowed_destinations: list[str] | None = None

    @field_validator("allowed_tools", "forbidden_actions", "review_required_tools", "allowed_resources",
                     "restricted_resources", "allowed_destinations")
    @classmethod
    def _normalize_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return normalize_str_list(value)


class AgentProfile(AgentBase):
    """The trusted, server-side profile. Never taken from an event request."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    agent_id: str
    status: AgentStatus
    status_reason: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)
    actor: str = Field(default="analyst", min_length=1, max_length=128)

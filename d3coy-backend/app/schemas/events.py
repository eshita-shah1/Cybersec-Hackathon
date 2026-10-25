"""Event submission and result schemas."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import Decision, ExecutionStatus, RequestStatus, RiskLevel, Sensitivity

_MAX_PARAM_BYTES = 4096


class EventSubmission(BaseModel):
    """An action an agent is *attempting*. It carries no identity or permission data:
    the trusted profile is always resolved server-side from ``agent_id``. Unknown fields
    (e.g. ``role`` or ``allowed_tools``) are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    agent_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    tool_name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    action: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z_]+$",
                        description="Operation: search, read, compute, write, send, export or delete")
    target_resource: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_:/.*@-]+$")
    action_description: str = Field(min_length=3, max_length=1000)
    task_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    resource_sensitivity: Sensitivity | None = Field(
        default=None, description="Optional caller claim; can only raise the catalog sensitivity")
    destination: str | None = Field(default=None, max_length=256)
    parameters: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$",
                                   description="Idempotency key; re-submitting returns the original result")
    correlation_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    timestamp: datetime | None = Field(default=None, description="Client-side time of the attempt (UTC)")

    @field_validator("parameters")
    @classmethod
    def _bounded_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 20:
            raise ValueError("at most 20 parameters allowed")
        try:
            encoded = json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("parameters must be JSON-serializable") from exc
        if len(encoded.encode()) > _MAX_PARAM_BYTES:
            raise ValueError(f"parameters exceed {_MAX_PARAM_BYTES} bytes")
        return value


class EventResult(BaseModel):
    """Response of POST /api/events and summary form of an event."""

    model_config = ConfigDict(from_attributes=True)

    event_id: str
    agent_id: str
    task_id: str | None = None
    request_id: str
    correlation_id: str
    received_at: datetime
    tool_name: str
    action: str
    target_resource: str
    resource_sensitivity: str
    action_description: str
    request_status: RequestStatus
    execution_status: ExecutionStatus
    policy_result: dict[str, Any] | None = None
    behavioral_result: dict[str, Any] | None = None
    semantic_result: dict[str, Any] | None = None
    context_result: dict[str, Any] | None = None
    risk_score: float | None = None
    risk_level: RiskLevel | None = None
    risk_breakdown: dict[str, Any] | None = None
    recommended_response: Decision | None = None
    decision: Decision | None = None
    explanations: list[str] = Field(default_factory=list)
    escalation: dict[str, Any] | None = None
    execution_result: dict[str, Any] | None = None
    review_id: str | None = None
    alert_ids: list[str] = Field(default_factory=list)
    duplicate: bool = False


class EventDetail(EventResult):
    assigned_task_snapshot: str = ""
    role_snapshot: str = ""
    destination: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] | None = None
    detections: list[dict[str, Any]] = Field(default_factory=list)
    tool_execution: dict[str, Any] | None = None
    audit_trail: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None

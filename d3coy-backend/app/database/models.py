"""SQLAlchemy ORM models. The database is the authoritative record of all security state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC in SQLite and always returns timezone-aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class AgentModel(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(64), index=True)
    assigned_task: Mapped[str] = mapped_column(String(256))
    task_description: Mapped[str] = mapped_column(Text)
    allowed_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    forbidden_actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_resources: Mapped[list[str]] = mapped_column(JSON, default=list)
    restricted_resources: Mapped[list[str]] = mapped_column(JSON, default=list)
    resource_permissions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    review_required_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    external_transfer_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_destinations: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, onupdate=_now)


class EventModel(Base):
    """One attempted agent action and everything D3COY decided about it."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("agent_id", "request_id", name="uq_event_agent_request"),
        Index("ix_events_agent_received", "agent_id", "received_at"),
    )

    event_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    # Not a foreign key on purpose: attempts by unknown identities are audited too.
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str] = mapped_column(String(128))
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, index=True)
    client_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, onupdate=_now)

    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(32))
    target_resource: Mapped[str] = mapped_column(String(256))
    resource_sensitivity: Mapped[str] = mapped_column(String(16))
    claimed_sensitivity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(256), nullable=True)
    action_description: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    assigned_task_snapshot: Mapped[str] = mapped_column(Text, default="")
    role_snapshot: Mapped[str] = mapped_column(String(64), default="")

    request_status: Mapped[str] = mapped_column(String(20), index=True)
    execution_status: Mapped[str] = mapped_column(String(20), index=True)

    policy_status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    policy_severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    policy_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    behavioral_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    semantic_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    context_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    features: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    risk_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    recommended_response: Mapped[str | None] = mapped_column(String(16), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    response_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    escalation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DetectionResultModel(Base):
    __tablename__ = "detection_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id"), index=True)
    detector: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class AlertModel(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.event_id"), nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    alert_type: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReviewModel(Base):
    """An action held before execution until an analyst decides."""

    __tablename__ = "pending_reviews"

    review_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id"), unique=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    revalidation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class AgentCredentialModel(Base):
    """Per-agent bearer credential. Only a SHA-256 hash of the token is stored."""

    __tablename__ = "agent_credentials"

    credential_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ExecutionPermissionModel(Base):
    """Every permission slip the gateway issues; consumed_at is set atomically exactly once."""

    __tablename__ = "execution_permissions"

    permission_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id"), unique=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(32))
    resource: Mapped[str] = mapped_column(String(256))
    args_sha256: Mapped[str] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    signature: Mapped[str] = mapped_column(String(64))


class ExecutionOutputModel(Base):
    """Full output of a real (sandbox) tool, readable only by the authenticated owning agent.
    Public event views only show a summary."""

    __tablename__ = "execution_outputs"

    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id"), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class ToolExecutionModel(Base):
    """Exactly one row per executed tool call; the unique event_id prevents double execution."""

    __tablename__ = "tool_executions"

    execution_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.event_id"), unique=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditRecordModel(Base):
    """Append-only audit history. No API updates or deletes these rows."""

    __tablename__ = "audit_records"

    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now, index=True)
    event_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action_type: Mapped[str] = mapped_column(String(48), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

"""Shared enums and response envelopes."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

    @property
    def rank(self) -> int:
        return _SENSITIVITY_RANK[self]


_SENSITIVITY_RANK = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.CONFIDENTIAL: 2,
    Sensitivity.RESTRICTED: 3,
}


class Operation(str, Enum):
    SEARCH = "search"
    READ = "read"
    COMPUTE = "compute"
    WRITE = "write"
    SEND = "send"
    EXPORT = "export"
    DELETE = "delete"


class AgentStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class PolicyStatus(str, Enum):
    ALLOW = "ALLOW"
    RESTRICTED = "RESTRICTED"  # permitted only with explicit human authorization
    DENY = "DENY"


class Severity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def score(self) -> float:
        return {"none": 0.0, "low": 25.0, "medium": 50.0, "high": 75.0, "critical": 100.0}[self.value]

    @property
    def rank(self) -> int:
        return ["none", "low", "medium", "high", "critical"].index(self.value)


class Decision(str, Enum):
    ALLOW = "ALLOW"
    MONITOR = "MONITOR"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"

    @property
    def rank(self) -> int:
        return ["ALLOW", "MONITOR", "REVIEW", "BLOCK"].index(self.value)


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RequestStatus(str, Enum):
    """Lifecycle of an attempted action."""

    ATTEMPTED = "ATTEMPTED"
    EVALUATED = "EVALUATED"
    BLOCKED = "BLOCKED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class ExecutionStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NOT_EXECUTED = "NOT_EXECUTED"  # blocked or rejected: the tool was never invoked


class DetectorStatus(str, Enum):
    OK = "ok"
    INSUFFICIENT_DATA = "insufficient_data"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    ERROR = "error"


class SignalResult(BaseModel):
    """Output of one detector. ``score`` is None whenever the detector could not evaluate."""

    detector: str
    status: DetectorStatus
    score: float | None = None
    explanation: str = ""
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.status == DetectorStatus.OK and self.score is not None


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody

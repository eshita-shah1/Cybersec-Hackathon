"""Client used by the D3COY gateway to run a real tool through the sandbox executor service.

The gateway issues a signed permission slip and posts it to the executor. The executor
re-verifies and consumes it. This client never touches the sandbox database directly.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.enforcement.permissions import Permission

logger = logging.getLogger(__name__)


class ExecutorUnavailable(Exception):
    """The executor could not be reached or returned an unusable response (fail-closed)."""


class ExecutorClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any] | None:
        try:
            r = httpx.get(f"{self.base_url}/health", timeout=self.timeout)
            return r.json() if r.status_code == 200 else None
        except httpx.HTTPError:
            return None

    def execute(self, permission: Permission, *, operation: str, resource: str,
                parameters: dict[str, Any], destination: str | None) -> dict[str, Any]:
        """Return the executor's decision dict. Raises ExecutorUnavailable on transport failure."""
        payload = {"permission": permission.to_dict(), "tool_name": permission.tool_name,
                   "operation": operation, "resource": resource, "parameters": parameters,
                   "destination": destination}
        try:
            r = httpx.post(f"{self.base_url}/execute", json=payload, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ExecutorUnavailable(f"executor unreachable: {type(exc).__name__}") from exc
        if r.status_code in (200, 403, 409):
            try:
                return r.json()
            except ValueError as exc:
                raise ExecutorUnavailable("executor returned a non-JSON response") from exc
        raise ExecutorUnavailable(f"executor returned HTTP {r.status_code}")

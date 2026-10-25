"""Thin HTTP client the real agent uses to reach D3COY. The agent holds ONLY its bearer token
and the D3COY base URL — it has no database credentials and no route to the sandbox executor.

Every tool call the agent makes goes through ``submit_action`` (POST /api/events). The agent
acts on the returned decision; it cannot execute a tool any other way.
"""

from __future__ import annotations

from typing import Any

import httpx


class D3coyClient:
    def __init__(self, base_url: str, agent_token: str, agent_id: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {agent_token}"}
        # The server authenticates identity from the token and requires the body agent_id to
        # match it, so a tampered value is rejected rather than trusted.
        self.agent_id = agent_id
        self.timeout = timeout

    def submit_action(self, *, tool_name: str, action: str, target_resource: str, action_description: str,
                      parameters: dict[str, Any] | None = None, destination: str | None = None,
                      request_id: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:
        body = {"tool_name": tool_name, "action": action, "target_resource": target_resource,
                "action_description": action_description, "parameters": parameters or {},
                "destination": destination, "request_id": request_id, "correlation_id": correlation_id}
        body = {k: v for k, v in body.items() if v is not None}
        body["agent_id"] = self.agent_id
        r = httpx.post(f"{self.base_url}/api/events", json=body, headers=self._headers, timeout=self.timeout)
        if r.status_code == 200:
            return r.json()
        # The agent must learn its identity mismatch / auth failures, but never sees internals.
        try:
            detail = r.json().get("error", {})
        except ValueError:
            detail = {"message": r.text[:200]}
        return {"_http_error": r.status_code, "error": detail}

    def fetch_output(self, event_id: str) -> dict[str, Any] | None:
        """Read the full tool output the agent is entitled to (its own executed actions)."""
        r = httpx.get(f"{self.base_url}/api/events/{event_id}/output", headers=self._headers, timeout=self.timeout)
        return r.json().get("output") if r.status_code == 200 else None

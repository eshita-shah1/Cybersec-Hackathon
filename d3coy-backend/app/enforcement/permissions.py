"""Execution permissions ("permission slips").

A permission is issued by the D3COY gateway for ONE evaluated action and is:
  * bound to the exact event, agent, tool, operation, resource and a SHA-256 digest of the
    arguments (parameters + destination) — changing any argument invalidates it;
  * short-lived (``expires_at``);
  * signed with HMAC-SHA256 using PERMISSION_SIGNING_KEY, which only D3COY and the tool
    executor hold;
  * single-use: every executor records consumed permission ids under a unique constraint.

This module has no dependency on the rest of D3COY so the separate sandbox executor service
can verify permissions independently.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


class PermissionRejected(Exception):
    """Raised when a permission fails verification. ``reason`` is safe to return to callers."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def args_digest(parameters: dict[str, Any] | None, destination: str | None) -> str:
    canonical = json.dumps({"parameters": parameters or {}, "destination": destination},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class Permission:
    permission_id: str
    event_id: str
    agent_id: str
    tool_name: str
    operation: str
    resource: str
    args_sha256: str
    issued_at: str   # ISO-8601 UTC
    expires_at: str  # ISO-8601 UTC
    signature: str = ""

    def signing_payload(self) -> bytes:
        fields = [self.permission_id, self.event_id, self.agent_id, self.tool_name, self.operation,
                  self.resource, self.args_sha256, self.issued_at, self.expires_at]
        return "\n".join(fields).encode()

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Permission":
        try:
            return cls(**{k: str(data[k]) for k in cls.__dataclass_fields__})
        except KeyError as exc:
            raise PermissionRejected(f"permission is missing field {exc.args[0]}") from exc


def _sign(key: bytes, perm: Permission) -> str:
    return hmac.new(key, perm.signing_payload(), hashlib.sha256).hexdigest()


def issue(key: bytes, *, event_id: str, agent_id: str, tool_name: str, operation: str, resource: str,
          parameters: dict[str, Any] | None, destination: str | None, ttl_seconds: int,
          now: datetime | None = None) -> Permission:
    now = now or datetime.now(timezone.utc)
    unsigned = Permission(
        permission_id=f"perm_{secrets.token_hex(12)}", event_id=event_id, agent_id=agent_id,
        tool_name=tool_name, operation=operation, resource=resource,
        args_sha256=args_digest(parameters, destination),
        issued_at=now.isoformat(), expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat())
    return Permission(**{**unsigned.to_dict(), "signature": _sign(key, unsigned)})


def verify(key: bytes, perm: Permission, *, tool_name: str, operation: str, resource: str,
           parameters: dict[str, Any] | None, destination: str | None, now: datetime | None = None) -> None:
    """Check signature, expiry and that the permission authorizes exactly this call.

    Does NOT check single use — the caller must atomically record consumption."""
    if not key:
        raise PermissionRejected("executor has no signing key configured (fail-closed)")
    expected = _sign(key, perm)
    if not perm.signature or not hmac.compare_digest(expected, perm.signature):
        raise PermissionRejected("invalid permission signature")
    now = now or datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(perm.expires_at)
    except ValueError as exc:
        raise PermissionRejected("malformed expiry") from exc
    if now >= expires:
        raise PermissionRejected("permission expired")
    mismatches = [name for name, a, b in (("tool_name", perm.tool_name, tool_name),
                                          ("operation", perm.operation, operation),
                                          ("resource", perm.resource, resource))
                  if a != b]
    if mismatches:
        raise PermissionRejected(f"permission does not cover this call ({', '.join(mismatches)} differ)")
    if not hmac.compare_digest(perm.args_sha256, args_digest(parameters, destination)):
        raise PermissionRejected("arguments differ from the authorized arguments; a new authorization is required")

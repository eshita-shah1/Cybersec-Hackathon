"""Turns a validated submission into a normalized action enriched with trusted server-side context."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any

from app.enforcement.tool_registry import ToolRegistry, ToolSpec
from app.registry import resource_catalog
from app.schemas.common import Sensitivity
from app.schemas.events import EventSubmission

_SECRET_KEY_RE = re.compile(r"(pass(word)?|secret|token|api[_-]?key|credential|auth|ssn|card|cvv|private)", re.I)
_MAX_VALUE_CHARS = 300


def sanitize_parameters(params: dict[str, Any]) -> dict[str, Any]:
    """Redact secret-looking keys and truncate long values before anything is stored or logged."""
    clean: dict[str, Any] = {}
    for key, value in params.items():
        if _SECRET_KEY_RE.search(str(key)):
            clean[key] = "[REDACTED]"
        elif isinstance(value, str) and len(value) > _MAX_VALUE_CHARS:
            clean[key] = value[:_MAX_VALUE_CHARS] + "…[truncated]"
        elif isinstance(value, dict):
            clean[key] = sanitize_parameters(value)
        else:
            clean[key] = value
    return clean


@dataclass
class NormalizedAction:
    agent_id: str
    tool_name: str
    operation: str
    resource: str
    resource_namespace: str
    sensitivity: Sensitivity
    claimed_sensitivity: Sensitivity | None
    sensitivity_note: str
    destination: str | None
    is_external: bool
    action_description: str
    server_description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    tool: ToolSpec | None = None


class EventNormalizer:
    def __init__(self, tools: ToolRegistry, internal_domains: list[str]) -> None:
        self.tools = tools
        self.internal_domains = internal_domains

    def is_external_destination(self, destination: str | None) -> bool:
        if not destination:
            return False
        dest = destination.strip().lower()
        if "@" in dest:
            domain = dest.rsplit("@", 1)[1]
            return not any(domain == d or domain.endswith("." + d) for d in self.internal_domains)
        # URLs / hosts: anything not under an internal domain is external.
        host = re.sub(r"^[a-z]+://", "", dest).split("/", 1)[0].split(":", 1)[0]
        return not any(host == d or host.endswith("." + d) for d in self.internal_domains)

    def normalize(self, sub: EventSubmission) -> NormalizedAction:
        tool_name = sub.tool_name.strip().lower()
        operation = sub.action.strip().lower()
        resource = sub.target_resource.strip().lower()
        spec = self.tools.get(tool_name)
        sensitivity, note = resource_catalog.resolve_sensitivity(resource, sub.resource_sensitivity)
        external = bool(spec and spec.external) or self.is_external_destination(sub.destination)
        entry = resource_catalog.classify(resource)
        label = entry.label if entry else "an uncatalogued resource"
        tool_text = spec.description if spec else f"Invoke unregistered tool {tool_name}"
        server_description = f"{tool_text} ({operation} on {label})"
        return NormalizedAction(
            agent_id=sub.agent_id,
            tool_name=tool_name,
            operation=operation,
            resource=resource,
            resource_namespace=resource_catalog.namespace(resource),
            sensitivity=sensitivity,
            claimed_sensitivity=sub.resource_sensitivity,
            sensitivity_note=note,
            destination=sub.destination.strip().lower() if sub.destination else None,
            is_external=external,
            action_description=sub.action_description,
            server_description=server_description,
            parameters=sanitize_parameters(sub.parameters),
            tool=spec,
        )


def matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatchcase(value.lower(), p.lower()) for p in patterns)

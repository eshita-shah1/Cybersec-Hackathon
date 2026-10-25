"""Server-side catalog of resource sensitivity.

Callers may *claim* a sensitivity, but D3COY uses ``max(catalog, claimed)`` so a caller can
raise, never lower, the sensitivity of a resource. Unknown resources default to
CONFIDENTIAL (fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

from app.schemas.common import Sensitivity


@dataclass(frozen=True)
class ResourceClass:
    pattern: str
    sensitivity: Sensitivity
    label: str


# First match wins; keep more specific patterns first.
RESOURCE_CATALOG: tuple[ResourceClass, ...] = (
    ResourceClass("supplier:catalog*", Sensitivity.INTERNAL, "supplier catalog"),
    ResourceClass("supplier:emails*", Sensitivity.INTERNAL, "supplier emails"),
    ResourceClass("procurement:outbox", Sensitivity.INTERNAL, "procurement outgoing mail"),
    ResourceClass("public:web*", Sensitivity.PUBLIC, "public web search"),
    ResourceClass("public:docs/*", Sensitivity.PUBLIC, "public document"),
    ResourceClass("research:notes/*", Sensitivity.INTERNAL, "research notes"),
    ResourceClass("finance:expenses/*", Sensitivity.INTERNAL, "monthly expense records"),
    ResourceClass("finance:reports/*", Sensitivity.INTERNAL, "financial summary reports"),
    ResourceClass("finance:vendor_payments/*", Sensitivity.CONFIDENTIAL, "vendor payment records"),
    ResourceClass("finance:payroll*", Sensitivity.RESTRICTED, "payroll records"),
    ResourceClass("finance:*", Sensitivity.CONFIDENTIAL, "other financial records"),
    ResourceClass("customers:*", Sensitivity.RESTRICTED, "customer personal data"),
    ResourceClass("hr:*", Sensitivity.RESTRICTED, "human-resources records"),
    ResourceClass("email:approved/*", Sensitivity.INTERNAL, "approved email content"),
    ResourceClass("email:drafts/*", Sensitivity.INTERNAL, "email drafts"),
    ResourceClass("email:outbox", Sensitivity.INTERNAL, "outgoing email queue"),
    ResourceClass("email:confidential/*", Sensitivity.CONFIDENTIAL, "confidential email"),
    ResourceClass("email:*", Sensitivity.CONFIDENTIAL, "other email content"),
)

UNKNOWN_RESOURCE_SENSITIVITY = Sensitivity.CONFIDENTIAL


def classify(resource: str) -> ResourceClass | None:
    r = resource.lower()
    for entry in RESOURCE_CATALOG:
        if fnmatchcase(r, entry.pattern):
            return entry
    return None


def resolve_sensitivity(resource: str, claimed: Sensitivity | None = None) -> tuple[Sensitivity, str]:
    """Return the effective sensitivity and a short note on how it was derived."""
    entry = classify(resource)
    base = entry.sensitivity if entry else UNKNOWN_RESOURCE_SENSITIVITY
    note = f"catalog: {entry.label}" if entry else "not in catalog: defaulted to confidential (fail-closed)"
    if claimed is not None and claimed.rank > base.rank:
        return claimed, f"{note}; raised to {claimed.value} by caller claim"
    return base, note


def namespace(resource: str) -> str:
    """'finance:expenses/2026-09' -> 'finance:expenses'."""
    r = resource.lower()
    return r.split("/", 1)[0]

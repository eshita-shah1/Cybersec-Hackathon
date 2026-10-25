"""Registry of SIMULATED tools.

Every tool works on deterministic synthetic data and has no real side effects: nothing is
deleted, nothing leaves the machine, no email is sent. "Dangerous" tools exist so that attack
attempts produce realistic security events; their handlers only return a simulation notice.

Tools can only be invoked with a single-use, HMAC-signed grant issued by the enforcement
gateway (see ``bind_gateway``). This enforces the architectural rule that every tool call
passes through the security pipeline. It is an in-process contract, not a sandbox: Python
code running in the same process could still reach private attributes.
"""

from __future__ import annotations

import hashlib
import hmac
import random
import re
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.schemas.common import Operation

ToolHandler = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str  # natural-language description used for semantic comparison
    category: str  # search | read | compute | write | communicate | destructive | export
    operations: tuple[Operation, ...]
    privilege: str = "low"  # low | medium | high
    side_effect: bool = False
    external: bool = False  # always moves data outside the organisation
    dangerous: bool = False  # attack-simulation tool
    backend: str = "simulated"  # "simulated" (local harmless handler) or "sandbox" (executor service)
    handler: ToolHandler | None = field(default=None, repr=False, compare=False)


class ToolInvocationError(Exception):
    """Raised when a tool is invoked without a valid gateway grant."""


class ToolExecutionError(Exception):
    """Raised by a tool handler when the simulated operation fails."""


@dataclass(frozen=True)
class ExecutionGrant:
    event_id: str
    tool_name: str
    signature: str


class GrantIssuer:
    """Handed to the gateway exactly once; the only object able to mint execution grants."""

    def __init__(self, key: bytes) -> None:
        self._key = key

    def issue(self, event_id: str, tool_name: str) -> ExecutionGrant:
        return ExecutionGrant(event_id, tool_name, _sign(self._key, event_id, tool_name))


def _sign(key: bytes, event_id: str, tool_name: str) -> str:
    return hmac.new(key, f"{event_id}|{tool_name}".encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------------
_EXPENSE_CATEGORIES = ("travel", "software", "office", "meals", "utilities", "marketing")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_PUBLIC_CORPUS = [
    ("doc-lithium-ion", "Lithium-ion batteries dominate grid-scale storage due to falling costs."),
    ("doc-flow-batteries", "Flow batteries offer long-duration storage with separable power and energy."),
    ("doc-green-hydrogen", "Green hydrogen can store surplus renewable electricity for seasonal use."),
    ("doc-pumped-hydro", "Pumped hydro remains the largest installed form of energy storage worldwide."),
    ("doc-battery-recycling", "Battery recycling recovers lithium, cobalt and nickel for reuse."),
]


def _month_from_resource(resource: str) -> str:
    month = resource.rsplit("/", 1)[-1]
    if not _MONTH_RE.match(month):
        raise ToolExecutionError(f"Resource '{resource}' does not name a valid month (expected YYYY-MM)")
    return month


def _synthetic_expenses(month: str) -> list[dict[str, Any]]:
    rng = random.Random(f"expenses:{month}")
    items = []
    for i in range(rng.randint(10, 16)):
        cat = rng.choice(_EXPENSE_CATEGORIES)
        items.append({"id": f"exp-{month}-{i:03d}", "category": cat, "amount": round(rng.uniform(20, 900), 2)})
    return items


def _h_search_public_web(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    query = str(params.get("query", "energy storage"))[:200]
    words = {w for w in query.lower().split() if len(w) > 3}
    hits = [{"document": f"public:docs/{d}", "snippet": t} for d, t in _PUBLIC_CORPUS
            if not words or words & set(t.lower().replace(",", "").split())] or \
           [{"document": f"public:docs/{d}", "snippet": t} for d, t in _PUBLIC_CORPUS[:2]]
    return {"query": query, "results": hits[:5]}


def _h_read_public_document(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    name = resource.rsplit("/", 1)[-1]
    text = dict(_PUBLIC_CORPUS).get(name, f"Synthetic public document '{name}' about renewable energy storage.")
    return {"document": resource, "text": text}


def _h_generate_summary(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    text = str(params.get("text", "")) or " ".join(t for _, t in _PUBLIC_CORPUS[:3])
    first = text.split(".")[0].strip()
    return {"summary_resource": resource, "summary": f"Summary: {first}."}


def _h_read_expenses(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    month = _month_from_resource(resource)
    items = _synthetic_expenses(month)
    return {"month": month, "record_count": len(items), "sample": items[:3]}


def _h_calculate_totals(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    month = _month_from_resource(resource)
    totals: dict[str, float] = {c: 0.0 for c in _EXPENSE_CATEGORIES}
    for item in _synthetic_expenses(month):
        totals[item["category"]] += item["amount"]
    totals = {k: round(v, 2) for k, v in totals.items()}
    return {"month": month, "totals_by_category": totals, "grand_total": round(sum(totals.values()), 2)}


def _h_generate_financial_summary(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    month = _month_from_resource(resource)
    totals = _h_calculate_totals(f"finance:expenses/{month}", {})["totals_by_category"]
    top = max(totals, key=totals.get)
    return {"report": resource, "summary": f"In {month} the largest spending category was {top} ({totals[top]:.2f})."}


def _h_read_vendor_payments(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    month = _month_from_resource(resource)
    rng = random.Random(f"vendors:{month}")
    payments = [{"vendor": f"Vendor-{rng.randint(100, 999)}", "amount": round(rng.uniform(500, 5000), 2)}
                for _ in range(4)]
    return {"month": month, "payments": payments}


def _h_read_approved_emails(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"mailbox": resource, "messages": [
        {"id": "msg-1", "subject": "Quarterly planning", "summary": "Planning meeting moved to Thursday."},
        {"id": "msg-2", "subject": "Office move", "summary": "New desks arrive next week."},
    ]}


def _h_summarize_messages(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"mailbox": resource, "summary": "Two approved messages: planning moved to Thursday; desks arrive next week."}


def _h_create_email_draft(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    subject = str(params.get("subject", "Team update"))[:120]
    return {"draft": resource, "subject": subject, "status": "draft saved (simulated)"}


def _h_send_email(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"simulated": True, "status": "queued in simulated outbox; no real email was sent",
            "subject": str(params.get("subject", ""))[:120]}


def _h_simulated_danger(resource: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"simulated": True, "status": "simulation only: no data was deleted, read or transferred"}


_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("search_public_web", "Search public web sources for information on a topic", "search",
             (Operation.SEARCH,), handler=_h_search_public_web),
    ToolSpec("read_public_document", "Read a publicly available document", "read",
             (Operation.READ,), handler=_h_read_public_document),
    ToolSpec("generate_summary", "Write a summary of research findings", "compute",
             (Operation.COMPUTE, Operation.WRITE), handler=_h_generate_summary),
    ToolSpec("read_expenses", "Read monthly expense records", "read",
             (Operation.READ,), handler=_h_read_expenses),
    ToolSpec("calculate_totals", "Calculate total spending for each expense category", "compute",
             (Operation.COMPUTE,), handler=_h_calculate_totals),
    ToolSpec("generate_financial_summary", "Write a financial summary report of monthly spending", "write",
             (Operation.WRITE,), side_effect=True, handler=_h_generate_financial_summary),
    ToolSpec("read_vendor_payments", "Read vendor payment records", "read",
             (Operation.READ,), privilege="medium", handler=_h_read_vendor_payments),
    ToolSpec("read_approved_emails", "Read approved email messages", "read",
             (Operation.READ,), handler=_h_read_approved_emails),
    ToolSpec("summarize_messages", "Summarize email messages", "compute",
             (Operation.COMPUTE,), handler=_h_summarize_messages),
    ToolSpec("create_email_draft", "Prepare an email draft", "write",
             (Operation.WRITE,), side_effect=True, handler=_h_create_email_draft),
    ToolSpec("send_email", "Send an email message to recipients", "communicate",
             (Operation.SEND,), privilege="medium", side_effect=True, handler=_h_send_email),
    # ---- attack-simulation tools (handlers never do anything real) ----
    ToolSpec("delete_customer_records", "Delete customer records from the customer database", "destructive",
             (Operation.DELETE,), privilege="high", side_effect=True, dangerous=True, handler=_h_simulated_danger),
    ToolSpec("export_confidential_data", "Export confidential data to an external destination", "export",
             (Operation.EXPORT,), privilege="high", side_effect=True, external=True, dangerous=True,
             handler=_h_simulated_danger),
    ToolSpec("access_payroll", "Read employee payroll and salary records", "read",
             (Operation.READ,), privilege="high", dangerous=True, handler=_h_simulated_danger),
    # ---- real, sandboxed tools (executed by the separate executor service) ----
    # The Procurement Agent uses these two; the others are real attack-surface tools the
    # policy layer is expected to block before the executor is ever asked to run them.
    ToolSpec("search_supplier_catalog", "Search the supplier catalog for item prices", "search",
             (Operation.SEARCH, Operation.READ), backend="sandbox"),
    ToolSpec("read_supplier_emails", "Read supplier emails", "read",
             (Operation.READ,), backend="sandbox"),
    ToolSpec("send_purchase_summary", "Send a purchase summary email to a recipient", "communicate",
             (Operation.SEND,), privilege="medium", side_effect=True, backend="sandbox"),
    ToolSpec("delete_customer", "Delete a customer record from the customer database", "destructive",
             (Operation.DELETE,), privilege="high", side_effect=True, dangerous=True, backend="sandbox"),
    ToolSpec("export_customer_data", "Export customer records to an external destination", "export",
             (Operation.EXPORT,), privilege="high", side_effect=True, external=True, dangerous=True,
             backend="sandbox"),
)


class ToolRegistry:
    """Holds tool specs and executes handlers only for valid, unused gateway grants."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {t.name: t for t in _TOOLS}
        self.__key = secrets.token_bytes(32)
        self.__issuer_bound = False
        self.__used_grants: set[str] = set()
        self.__lock = threading.Lock()
        self.invocation_count = 0  # observable for tests: number of handler executions

    # ---- metadata (safe to expose) ----
    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "category": t.category,
                 "operations": [o.value for o in t.operations], "privilege": t.privilege,
                 "side_effect": t.side_effect, "external": t.external, "attack_simulation": t.dangerous,
                 "backend": t.backend}
                for t in self._specs.values()]

    # ---- gateway binding ----
    def bind_gateway(self) -> GrantIssuer:
        """Return the grant issuer. Can be called once; later calls fail."""
        with self.__lock:
            if self.__issuer_bound:
                raise ToolInvocationError("A gateway is already bound to this tool registry")
            self.__issuer_bound = True
            return GrantIssuer(self.__key)

    def invoke(self, grant: ExecutionGrant, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(grant, ExecutionGrant):
            raise ToolInvocationError("Tool execution requires a gateway grant")
        expected = _sign(self.__key, grant.event_id, grant.tool_name)
        if not hmac.compare_digest(expected, grant.signature):
            raise ToolInvocationError("Invalid execution grant")
        with self.__lock:
            if grant.signature in self.__used_grants:
                raise ToolInvocationError("Execution grant already used")
            self.__used_grants.add(grant.signature)
        spec = self._specs.get(grant.tool_name)
        if spec is None:
            raise ToolInvocationError(f"Unknown tool '{grant.tool_name}'")
        if spec.backend != "simulated" or spec.handler is None:
            raise ToolInvocationError(f"Tool '{grant.tool_name}' is not a locally-simulated tool")
        self.invocation_count += 1
        return spec.handler(resource, params)

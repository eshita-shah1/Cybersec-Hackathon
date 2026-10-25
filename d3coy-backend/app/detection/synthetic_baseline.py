"""Reproducible synthetic activity for the three seeded roles.

Normal sequences model legitimate work sessions. Drift sequences (used only for validation,
never for training) start normally and then drift toward off-pattern behavior. Policy status
for each synthetic record is computed by the real PolicyEngine against the seeded profiles,
so the synthetic world follows the same rules as live enforcement.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from app.detection.feature_extractor import ActivityRecord
from app.detection.policy_engine import PolicyEngine
from app.enforcement.tool_registry import ToolRegistry
from app.monitoring.event_normalizer import EventNormalizer
from app.registry.resource_catalog import resolve_sensitivity
from app.registry.seed_agents import SEED_AGENTS
from app.schemas.agents import AgentProfile
from app.schemas.common import AgentStatus, utcnow
from app.schemas.events import EventSubmission

Step = tuple[str, str, str]  # (tool, operation, resource)

_TOPICS = ["energy-storage", "solar", "battery-recycling", "grid", "hydrogen", "pumped-hydro"]


def _months(rng: random.Random) -> str:
    return f"2026-{rng.randint(1, 12):02d}"


# ---- normal session templates ------------------------------------------------------
def _research_session(rng: random.Random) -> list[Step]:
    topic = rng.choice(_TOPICS)
    steps: list[Step] = [("search_public_web", "search", "public:web/search")]
    for _ in range(rng.randint(1, 3)):
        steps.append(("read_public_document", "read", f"public:docs/{topic}-{rng.randint(1, 40)}"))
    if rng.random() < 0.3:
        steps.append(("search_public_web", "search", "public:web/search"))
        for _ in range(rng.randint(1, 2)):
            steps.append(("read_public_document", "read", f"public:docs/{topic}-{rng.randint(1, 40)}"))
    steps.append(("generate_summary", "compute", f"research:notes/{topic}-summary"))
    return steps


def _finance_session(rng: random.Random) -> list[Step]:
    month = _months(rng)
    steps: list[Step] = [("read_expenses", "read", f"finance:expenses/{month}")]
    if rng.random() < 0.3:
        steps.append(("read_expenses", "read", f"finance:expenses/{_months(rng)}"))
    if rng.random() < 0.03:  # rare but legitimate
        steps.append(("read_vendor_payments", "read", f"finance:vendor_payments/{month}"))
    steps.append(("calculate_totals", "compute", f"finance:expenses/{month}"))
    if rng.random() < 0.8:
        steps.append(("generate_financial_summary", "write", f"finance:reports/{month}"))
    return steps


def _email_session(rng: random.Random) -> list[Step]:
    steps: list[Step] = [("read_approved_emails", "read", "email:approved/inbox")]
    if rng.random() < 0.3:
        steps.append(("read_approved_emails", "read", "email:approved/announcements"))
    steps.append(("summarize_messages", "compute", "email:approved/inbox"))
    steps.append(("create_email_draft", "write", f"email:drafts/draft-{rng.randint(1, 500)}"))
    if rng.random() < 0.03:
        steps.append(("send_email", "send", "email:outbox"))
    return steps


def _procurement_session(rng: random.Random) -> list[Step]:
    steps: list[Step] = [("read_supplier_emails", "read", "supplier:emails")]
    for _ in range(rng.randint(1, 2)):
        steps.append(("search_supplier_catalog", "search", "supplier:catalog"))
    if rng.random() < 0.08:  # sending is occasional and authorized per-call
        steps.append(("send_purchase_summary", "send", "procurement:outbox"))
    return steps


# Rare benign mistakes (~1%) so a single denial in history is not unseen behavior.
_MISTAKES: dict[str, list[Step]] = {
    "researcher": [("read_public_document", "read", "finance:reports/2026-01")],
    "financial_analyst": [("read_expenses", "read", "finance:budget/2026")],
    "email_assistant": [("read_approved_emails", "read", "email:confidential/board")],
    "procurement_assistant": [("read_supplier_emails", "read", "finance:reports/2026-01")],
}

# Off-pattern actions used to build validation drift sequences.
_DRIFT: dict[str, Callable[[random.Random], Step]] = {
    "researcher": lambda r: r.choice([
        ("read_public_document", "read", f"finance:reports/2026-{r.randint(1, 12):02d}"),
        ("read_expenses", "read", f"finance:expenses/2026-{r.randint(1, 12):02d}"),
        ("access_payroll", "read", "finance:payroll"),
        ("export_confidential_data", "export", "customers:db"),
        ("read_public_document", "read", f"customers:records/{r.randint(1, 900)}"),
    ]),
    "financial_analyst": lambda r: r.choice([
        ("read_vendor_payments", "read", f"finance:vendor_payments/2026-{r.randint(1, 12):02d}"),
        ("read_vendor_payments", "read", f"finance:vendor_payments/2025-{r.randint(1, 12):02d}"),
        ("access_payroll", "read", "finance:payroll"),
        ("read_expenses", "read", f"customers:records/{r.randint(1, 900)}"),
        ("export_confidential_data", "export", "customers:db"),
        ("read_expenses", "read", f"hr:salaries/{r.randint(1, 50)}"),
    ]),
    "email_assistant": lambda r: r.choice([
        ("read_approved_emails", "read", f"email:confidential/{r.randint(1, 90)}"),
        ("send_email", "send", "email:outbox"),
        ("export_confidential_data", "export", "customers:db"),
        ("read_approved_emails", "read", f"customers:records/{r.randint(1, 900)}"),
    ]),
    "procurement_assistant": lambda r: r.choice([
        ("export_customer_data", "export", "customers:db"),
        ("delete_customer", "delete", f"customers:records/{r.randint(1, 900)}"),
        ("read_supplier_emails", "read", f"customers:records/{r.randint(1, 900)}"),
        ("search_supplier_catalog", "search", f"finance:expenses/2026-{r.randint(1, 12):02d}"),
    ]),
}

_SESSIONS = {"researcher": _research_session, "financial_analyst": _finance_session,
             "email_assistant": _email_session, "procurement_assistant": _procurement_session}


class SyntheticWorld:
    """Turns steps into ActivityRecords, using the real policy engine for policy status."""

    def __init__(self) -> None:
        self.tools = ToolRegistry()
        self.normalizer = EventNormalizer(self.tools, ["d3coy.local"])
        self.policy = PolicyEngine()
        now = utcnow()
        self.profiles: dict[str, AgentProfile] = {
            a.role: AgentProfile(**a.model_dump(), status=AgentStatus.ACTIVE, version=1,
                                 created_at=now, updated_at=now)
            for a in SEED_AGENTS
        }

    def record(self, role: str, step: Step) -> ActivityRecord:
        tool, op, resource = step
        profile = self.profiles[role]
        sub = EventSubmission(agent_id=profile.agent_id, tool_name=tool, action=op, target_resource=resource,
                              action_description="synthetic",
                              destination="team@d3coy.local" if tool == "send_email" else None)
        action = self.normalizer.normalize(sub)
        status = self.policy.evaluate(profile, action).status.value
        sens, _ = resolve_sensitivity(resource)
        return ActivityRecord(tool, op, resource, sens.value, status)

    def normal_sequence(self, role: str, rng: random.Random, length: int) -> list[ActivityRecord]:
        out: list[ActivityRecord] = []
        while len(out) < length:
            if rng.random() < 0.01:
                out.append(self.record(role, rng.choice(_MISTAKES[role])))
            for step in _SESSIONS[role](rng):
                out.append(self.record(role, step))
        return out[:length]

    def drift_sequence(self, role: str, rng: random.Random, prefix: int, drift: int) -> list[ActivityRecord]:
        seq = self.normal_sequence(role, rng, prefix)
        for _ in range(drift):
            if rng.random() < 0.25:  # drift is gradual: normal steps still interleave
                seq.append(self.record(role, rng.choice(_SESSIONS[role](rng))))
            else:
                seq.append(self.record(role, _DRIFT[role](rng)))
        return seq

    @property
    def roles(self) -> list[str]:
        return list(_SESSIONS)

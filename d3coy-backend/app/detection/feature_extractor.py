"""Behavioral feature extraction.

The SAME function is used for synthetic training data and for live events, so the model sees
identical feature definitions in both places.

Features describe a sliding window made of the agent's most recent prior events plus the
current attempted action. All model features are bounded to [0, 1] (River's Half-Space Trees
require that). Time-based rates are computed but reported as *informational only*: the
simulator replays scenarios at machine speed, so wall-clock rates would not match the
synthetic training distribution.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

HIGH_IMPACT_OPS = {"send", "export", "delete"}
SENSITIVE_LEVELS = {"confidential", "restricted"}
OFF_ROLE_PROB = 0.03

FEATURE_NAMES: list[str] = [
    "tool_rarity",              # current tool is rare for this role
    "operation_rarity",         # current operation type is rare for this role
    "transition_rarity",        # previous tool -> current tool sequence is rare
    "unique_tools_ratio",       # tool diversity in window
    "unique_resources_ratio",   # resource diversity in window
    "sensitive_ratio",          # share of window touching confidential/restricted data
    "prior_denied_ratio",       # share of prior window events denied by policy
    "off_role_ratio",           # share of window using tools rare for the role
    "read_ratio",               # tool-category mix ...
    "compute_ratio",
    "write_ratio",
    "high_impact_ratio",        # send / export / delete
    "tool_mix_divergence",      # Jensen-Shannon divergence of window tool mix vs role baseline
    "new_namespace_ratio",      # resource areas never seen for this agent or role
]

FEATURE_DESCRIPTIONS: dict[str, str] = {
    "tool_rarity": "the requested tool is rarely used by agents with this role",
    "operation_rarity": "the requested operation type is unusual for this role",
    "transition_rarity": "the sequence of tool calls is unusual compared with the role baseline",
    "unique_tools_ratio": "the agent is switching between more tools than usual",
    "unique_resources_ratio": "the agent is accessing more distinct resources than usual",
    "sensitive_ratio": "recent activity touches sensitive resources more often than usual",
    "prior_denied_ratio": "recent attempts by this agent were denied by policy",
    "off_role_ratio": "recent activity uses tools that are uncommon for this role",
    "read_ratio": "the share of read operations differs from the usual pattern",
    "compute_ratio": "the share of computation steps differs from the usual pattern",
    "write_ratio": "the share of write operations differs from the usual pattern",
    "high_impact_ratio": "recent activity includes more send/export/delete operations than usual",
    "tool_mix_divergence": "the recent tool-usage mix differs from the agent's role baseline",
    "new_namespace_ratio": "the agent is reaching resource areas it has not used before",
}


@dataclass(frozen=True)
class ActivityRecord:
    tool_name: str
    operation: str
    resource: str
    sensitivity: str
    policy_status: str = "ALLOW"
    timestamp: datetime | None = None

    @property
    def namespace(self) -> str:
        return self.resource.lower().split("/", 1)[0]


@dataclass
class RoleBaseline:
    """Statistics of legitimate behavior for a role, learned from the synthetic baseline."""

    tool_probs: dict[str, float]
    op_probs: dict[str, float]
    transitions: dict[str, dict[str, float]]
    namespaces: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {"tool_probs": self.tool_probs, "op_probs": self.op_probs,
                "transitions": self.transitions, "namespaces": sorted(self.namespaces)}

    @classmethod
    def from_dict(cls, d: dict) -> "RoleBaseline":
        return cls(d["tool_probs"], d["op_probs"], d["transitions"], set(d.get("namespaces", [])))

    @classmethod
    def fit(cls, sequences: list[list[ActivityRecord]]) -> "RoleBaseline":
        tools: Counter[str] = Counter()
        ops: Counter[str] = Counter()
        trans: dict[str, Counter[str]] = {}
        ns: set[str] = set()
        for seq in sequences:
            for i, r in enumerate(seq):
                if r.policy_status == "DENY":
                    continue  # baseline describes accepted behavior only
                tools[r.tool_name] += 1
                ops[r.operation] += 1
                ns.add(r.namespace)
                if i > 0:
                    trans.setdefault(seq[i - 1].tool_name, Counter())[r.tool_name] += 1
        total_t, total_o = sum(tools.values()), sum(ops.values())
        return cls(
            tool_probs={k: v / total_t for k, v in tools.items()},
            op_probs={k: v / total_o for k, v in ops.items()},
            transitions={p: {k: v / sum(c.values()) for k, v in c.items()} for p, c in trans.items()},
            namespaces=ns,
        )


def _category(op: str) -> str:
    if op in ("search", "read"):
        return "read"
    if op in HIGH_IMPACT_OPS:
        return "high_impact"
    return op  # compute | write | other


def _js_divergence(p: dict[str, float], q: dict[str, float]) -> float:
    keys = set(p) | set(q)
    m = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}

    def kl(a: dict[str, float]) -> float:
        return sum(a[k] * math.log2(a[k] / m[k]) for k in keys if a.get(k, 0.0) > 0)

    return max(0.0, min(1.0, 0.5 * kl(p) + 0.5 * kl(q)))


def extract_features(
    current: ActivityRecord,
    prior: list[ActivityRecord],
    seen_namespaces: set[str],
    baseline: RoleBaseline,
) -> dict[str, float]:
    """Compute model features for ``current`` given up to W-1 prior records (chronological)."""
    window = [*prior, current]
    n = len(window)
    tp = baseline.tool_probs
    cats = Counter(_category(r.operation) for r in window)
    prev = prior[-1].tool_name if prior else None
    trans_p = baseline.transitions.get(prev, {}).get(current.tool_name, 0.0) if prev else 1.0
    tool_counts = Counter(r.tool_name for r in window)
    known_ns = seen_namespaces | baseline.namespaces
    feats = {
        "tool_rarity": 1.0 - tp.get(current.tool_name, 0.0),
        "operation_rarity": 1.0 - baseline.op_probs.get(current.operation, 0.0),
        "transition_rarity": 1.0 - trans_p,
        "unique_tools_ratio": len(tool_counts) / n,
        "unique_resources_ratio": len({r.resource for r in window}) / n,
        "sensitive_ratio": sum(r.sensitivity in SENSITIVE_LEVELS for r in window) / n,
        "prior_denied_ratio": (sum(r.policy_status == "DENY" for r in prior) / len(prior)) if prior else 0.0,
        "off_role_ratio": sum(tp.get(r.tool_name, 0.0) < OFF_ROLE_PROB for r in window) / n,
        "read_ratio": cats["read"] / n,
        "compute_ratio": cats["compute"] / n,
        "write_ratio": cats["write"] / n,
        "high_impact_ratio": cats["high_impact"] / n,
        "tool_mix_divergence": _js_divergence({k: v / n for k, v in tool_counts.items()}, tp),
        "new_namespace_ratio": sum(r.namespace not in known_ns for r in window) / n,
    }
    return {k: round(float(feats[k]), 6) for k in FEATURE_NAMES}


def informational_features(current_time: datetime, prior: list[ActivityRecord]) -> dict[str, float | None]:
    """Wall-clock rate features. Reported for explainability, NOT fed to the models."""
    stamps = [r.timestamp for r in prior if r.timestamp]
    if not stamps:
        return {"calls_last_5min": 0, "mean_seconds_between_calls": None}
    recent = sum(1 for t in stamps if t >= current_time - timedelta(minutes=5))
    ordered = sorted([*stamps, current_time])
    gaps = [(b - a).total_seconds() for a, b in zip(ordered, ordered[1:])]
    return {"calls_last_5min": recent + 1,
            "mean_seconds_between_calls": round(sum(gaps) / len(gaps), 2) if gaps else None}

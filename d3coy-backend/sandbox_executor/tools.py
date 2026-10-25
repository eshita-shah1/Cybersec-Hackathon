"""Real (sandboxed) tool implementations. These perform actual database reads, writes, and
deletes against the sandbox store, and record sent mail in a real outbox table.

Each handler takes (conn, resource, parameters) where conn is an open transaction. The handler
map is keyed by tool name. A tool not in the map cannot be executed, even with a valid
permission — the executor has no generic code path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Connection

from sandbox_executor import store as st


class ToolError(Exception):
    """Recoverable, caller-safe tool failure."""


def _month_ok(resource: str) -> str:
    month = resource.rsplit("/", 1)[-1]
    parts = month.split("-")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit() and 1 <= int(parts[1]) <= 12):
        raise ToolError(f"Resource '{resource}' does not name a valid month (YYYY-MM)")
    return month


# ---- procurement agent tools ----
def search_supplier_catalog(conn: Connection, resource: str, params: dict[str, Any]) -> dict[str, Any]:
    item = str(params.get("item", "")).lower().strip()
    q = select(st.catalog_items.c.supplier_id, st.catalog_items.c.item, st.catalog_items.c.unit_price,
               st.catalog_items.c.min_order, st.catalog_items.c.lead_time_days)
    rows = [dict(r._mapping) for r in conn.execute(q)]
    if item:
        rows = [r for r in rows if item in r["item"].lower()]
    return {"resource": resource, "count": len(rows), "items": rows}


def read_supplier_emails(conn: Connection, resource: str, params: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for r in conn.execute(select(st.supplier_emails)):
        m = dict(r._mapping)
        m["flagged_untrusted"] = (m["email_id"] == st.FLAGGED_EMAIL_ID)
        rows.append(m)
    return {"resource": resource, "count": len(rows), "emails": rows}


def send_purchase_summary(conn: Connection, resource: str, params: dict[str, Any], *,
                          permission_id: str, destination: str | None) -> dict[str, Any]:
    recipient = destination or str(params.get("recipient", ""))
    if not recipient:
        raise ToolError("no recipient provided")
    subject = str(params.get("subject", "Purchase summary"))[:256]
    body = str(params.get("body", ""))[:10000]
    conn.execute(insert(st.outbox).values(permission_id=permission_id, recipient=recipient, subject=subject,
                                          body=body, sent_at=datetime.now(timezone.utc)))
    return {"status": "sent", "recipient": recipient, "subject": subject,
            "note": "delivered to the sandbox outbox table; no real email left the machine"}


# ---- attack-surface tools (real operations the policy layer is expected to block upstream) ----
def delete_customer(conn: Connection, resource: str, params: dict[str, Any]) -> dict[str, Any]:
    target = str(params.get("customer_id") or resource.rsplit("/", 1)[-1])
    if target in ("all", "*"):
        n = conn.execute(delete(st.customers)).rowcount
    else:
        n = conn.execute(delete(st.customers).where(st.customers.c.customer_id == target)).rowcount
    return {"status": "deleted", "target": target, "rows_deleted": n}


def export_customer_data(conn: Connection, resource: str, params: dict[str, Any], *,
                         destination: str | None) -> dict[str, Any]:
    rows = [dict(r._mapping) for r in conn.execute(select(st.customers))]
    return {"status": "exported", "destination": destination, "record_count": len(rows), "rows": rows}


def read_expenses(conn: Connection, resource: str, params: dict[str, Any]) -> dict[str, Any]:
    _month_ok(resource)
    rows = [dict(r._mapping) for r in conn.execute(select(st.catalog_items))]
    return {"resource": resource, "line_items": len(rows)}


Handler = Callable[..., dict[str, Any]]

# Tools whose handler needs the permission id (side-effecting sends) or destination.
_NEEDS_PERMISSION = {"send_purchase_summary"}
_NEEDS_DESTINATION = {"send_purchase_summary", "export_customer_data"}

HANDLERS: dict[str, Handler] = {
    "search_supplier_catalog": search_supplier_catalog,
    "read_supplier_emails": read_supplier_emails,
    "send_purchase_summary": send_purchase_summary,
    "delete_customer": delete_customer,
    "delete_customer_records": delete_customer,
    "export_customer_data": export_customer_data,
    "export_confidential_data": export_customer_data,
    "read_expenses": read_expenses,
}


def execute(conn: Connection, tool_name: str, resource: str, params: dict[str, Any], *,
            permission_id: str, destination: str | None) -> dict[str, Any]:
    handler = HANDLERS.get(tool_name)
    if handler is None:
        raise ToolError(f"tool '{tool_name}' is not implemented by the executor")
    kwargs: dict[str, Any] = {}
    if tool_name in _NEEDS_PERMISSION:
        kwargs["permission_id"] = permission_id
    if tool_name in _NEEDS_DESTINATION:
        kwargs["destination"] = destination
    return handler(conn, resource, params, **kwargs)

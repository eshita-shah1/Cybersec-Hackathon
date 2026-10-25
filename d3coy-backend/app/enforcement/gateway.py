"""Enforcement gateway: the only code path that can execute a tool.

Execution is a bounded state machine guarded by an atomic compare-and-set:
  EVALUATED (decision ALLOW/MONITOR) --claim--> EXECUTING --> EXECUTED | FAILED
  APPROVED  (human approval + policy revalidation) --claim--> EXECUTING --> EXECUTED | FAILED
A claim succeeds once; a second attempt (duplicate request, double approval, race) fails.
The tool_executions table has a unique event_id as a second, database-level guard.

Every execution is authorised by a signed, expiring, single-use permission slip bound to the
exact event, agent, tool, operation, resource and argument digest:

  * SIMULATED tools run in-process via the ToolRegistry (harmless synthetic data). The gateway
    consumes the permission atomically in the D3COY database first.
  * SANDBOX tools run in the separate executor service. The gateway posts the permission; the
    executor independently re-verifies it and consumes it under its own unique constraint, so
    neither a replay nor a race can execute a real operation twice. If the executor cannot be
    reached, the action fails closed (it is never executed).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.database.db import Database
from app.database.models import EventModel, ExecutionOutputModel, ExecutionPermissionModel, ToolExecutionModel
from app.database.repositories import append_audit, new_id, transition_event
from app.enforcement.executor_client import ExecutorClient, ExecutorUnavailable
from app.enforcement.permissions import Permission, issue
from app.enforcement.tool_registry import ToolExecutionError, ToolRegistry
from app.schemas.common import Decision, ExecutionStatus, PolicyStatus, RequestStatus, utcnow

logger = logging.getLogger(__name__)

_MAX_SUMMARY_CHARS = 600


class ExecutionRefused(Exception):
    """The event is not in a state that permits execution."""


def _summarize(output: dict[str, Any] | None) -> dict[str, Any] | None:
    """A small, safe summary of a tool output for the public event record. Full output (which
    may contain record contents) is stored privately for the owning agent only."""
    if output is None:
        return None
    summary: dict[str, Any] = {}
    for key, value in output.items():
        if isinstance(value, (int, float, bool)) or value is None:
            summary[key] = value
        elif isinstance(value, str):
            summary[key] = value[:200]
        elif isinstance(value, list):
            summary[f"{key}_count"] = len(value)
        elif isinstance(value, dict):
            summary[f"{key}_keys"] = sorted(value)[:10]
    return summary or {"note": "output stored privately"}


class EnforcementGateway:
    def __init__(self, db: Database, tools: ToolRegistry, *, signing_key: bytes, permission_ttl: int,
                 executor: ExecutorClient) -> None:
        self.db = db
        self.tools = tools
        self.signing_key = signing_key
        self.permission_ttl = permission_ttl
        self.executor = executor
        self._grant_issuer = tools.bind_gateway()  # still guards local simulated invocation

    def execute(self, event_id: str, actor: str = "system:gateway") -> dict[str, Any]:
        # ---- 1. claim the event + issue and persist a permission (atomic) ----
        try:
            with self.db.session() as s:
                ev = s.get(EventModel, event_id)
                if ev is None:
                    raise ExecutionRefused("event not found")
                if ev.policy_status == PolicyStatus.DENY.value or ev.decision == Decision.BLOCK.value:
                    raise ExecutionRefused("hard policy denial or block decision: execution is never permitted")
                if ev.request_status == RequestStatus.EVALUATED.value:
                    if ev.decision not in (Decision.ALLOW.value, Decision.MONITOR.value):
                        raise ExecutionRefused(f"decision {ev.decision} does not permit execution")
                    if ev.policy_status != PolicyStatus.ALLOW.value:
                        raise ExecutionRefused("policy did not allow unattended execution")
                elif ev.request_status != RequestStatus.APPROVED.value:
                    raise ExecutionRefused(f"event is {ev.request_status}; it cannot be executed")
                claimed = transition_event(
                    s, event_id, [RequestStatus.EVALUATED.value, RequestStatus.APPROVED.value],
                    request_status=RequestStatus.EXECUTING.value, execution_status=ExecutionStatus.RUNNING.value,
                    updated_at=utcnow())
                if not claimed:
                    raise ExecutionRefused("event was already claimed for execution")
                tool_name, operation = ev.tool_name, ev.action
                resource, destination = ev.target_resource, ev.destination
                params, agent_id = dict(ev.parameters or {}), ev.agent_id
                spec = self.tools.get(tool_name)
                backend = spec.backend if spec else "simulated"
                perm = issue(self.signing_key, event_id=event_id, agent_id=agent_id, tool_name=tool_name,
                             operation=operation, resource=resource, parameters=params, destination=destination,
                             ttl_seconds=self.permission_ttl)
                s.add(ExecutionPermissionModel(
                    permission_id=perm.permission_id, event_id=event_id, agent_id=agent_id, tool_name=tool_name,
                    operation=operation, resource=resource, args_sha256=perm.args_sha256,
                    issued_at=_iso(perm.issued_at), expires_at=_iso(perm.expires_at), signature=perm.signature))
                s.add(ToolExecutionModel(execution_id=new_id("exe"), event_id=event_id, tool_name=tool_name,
                                         status=ExecutionStatus.RUNNING.value))
                append_audit(s, actor=actor, action_type="permission_issued", event_id=event_id,
                             agent_id=agent_id, details={"tool": tool_name, "backend": backend,
                                                         "permission_id": perm.permission_id})
        except IntegrityError as exc:
            raise ExecutionRefused("a tool execution already exists for this event") from exc

        # ---- 2. execute via the right backend ----
        if backend == "sandbox":
            output, error, status = self._run_sandbox(event_id, agent_id, perm, operation, resource, params,
                                                       destination)
        else:
            output, error, status = self._run_simulated(event_id, agent_id, perm, tool_name, resource, params)

        # ---- 3. record the outcome ----
        final = RequestStatus.EXECUTED if status == ExecutionStatus.SUCCEEDED else RequestStatus.FAILED
        with self.db.session() as s:
            ev = s.get(EventModel, event_id)
            ev.request_status = final.value
            ev.execution_status = status.value
            ev.execution_result = {"status": status.value, "backend": backend, "summary": _summarize(output),
                                   "error": error}
            ev.error = error
            ev.updated_at = utcnow()
            row = s.query(ToolExecutionModel).filter_by(event_id=event_id).one()
            row.status, row.output, row.error, row.finished_at = (
                status.value, _summarize(output), error, utcnow())
            if output is not None:
                s.merge(ExecutionOutputModel(event_id=event_id, agent_id=agent_id, output=output))
            append_audit(s, actor=actor, action_type="tool_executed" if final == RequestStatus.EXECUTED
                         else "tool_failed", event_id=event_id, agent_id=ev.agent_id,
                         details={"tool": tool_name, "backend": backend, "status": status.value, "error": error})
        logger.info("event=%s tool=%s backend=%s execution=%s", event_id, tool_name, backend, status.value)
        return {"status": status.value, "backend": backend, "summary": _summarize(output), "error": error}

    # ------------------------------------------------------------------
    def _run_simulated(self, event_id, agent_id, perm, tool_name, resource, params):  # noqa: ANN001
        if not self._consume_permission(perm.permission_id):
            return None, "permission already consumed", ExecutionStatus.FAILED
        grant = self._grant_issuer.issue(event_id, tool_name)
        try:
            return self.tools.invoke(grant, resource, params), None, ExecutionStatus.SUCCEEDED
        except ToolExecutionError as exc:
            return None, str(exc), ExecutionStatus.FAILED
        except Exception:
            logger.exception("Simulated tool %s failed for event %s", tool_name, event_id)
            return None, "internal tool error", ExecutionStatus.FAILED

    def _run_sandbox(self, event_id, agent_id, perm, operation, resource, params, destination):  # noqa: ANN001
        try:
            result = self.executor.execute(perm, operation=operation, resource=resource, parameters=params,
                                            destination=destination)
        except ExecutorUnavailable as exc:
            # fail closed: the real operation did not run
            logger.error("event=%s executor unavailable: %s", event_id, exc)
            return None, f"tool executor unavailable; action was not executed ({exc})", ExecutionStatus.FAILED
        self._mark_consumed(perm.permission_id)  # executor is authoritative; mirror locally
        if result.get("executed"):
            return result.get("output"), None, ExecutionStatus.SUCCEEDED
        reason = result.get("error") or result.get("reason") or "execution refused by executor"
        return None, reason, ExecutionStatus.FAILED

    def _consume_permission(self, permission_id: str) -> bool:
        """Atomically mark a permission consumed; returns False if it was already consumed."""
        with self.db.session() as s:
            n = s.execute(update(ExecutionPermissionModel)
                          .where(ExecutionPermissionModel.permission_id == permission_id,
                                  ExecutionPermissionModel.consumed_at.is_(None))
                          .values(consumed_at=utcnow())
                          .execution_options(synchronize_session=False)).rowcount
            return n == 1

    def _mark_consumed(self, permission_id: str) -> None:
        with self.db.session() as s:
            s.execute(update(ExecutionPermissionModel)
                      .where(ExecutionPermissionModel.permission_id == permission_id,
                              ExecutionPermissionModel.consumed_at.is_(None))
                      .values(consumed_at=utcnow()).execution_options(synchronize_session=False))


def _iso(value: str):  # noqa: ANN001, ANN201
    from datetime import datetime

    return datetime.fromisoformat(value)

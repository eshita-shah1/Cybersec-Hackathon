"""Agent Identity and Role Registry: the single source of trusted agent profiles."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.database.db import Database
from app.database.models import AgentModel
from app.database.repositories import append_audit
from app.registry.seed_agents import SEED_AGENTS
from app.schemas.agents import AgentCreate, AgentProfile, AgentUpdate
from app.schemas.common import AgentStatus, utcnow

logger = logging.getLogger(__name__)


class RegistryError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class AgentRegistry:
    """CRUD for agent profiles. Profiles change only through these administrative methods."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- reads -----------------------------------------------------------------
    def get(self, agent_id: str) -> AgentProfile | None:
        with self.db.session() as s:
            row = s.get(AgentModel, agent_id)
            return AgentProfile.model_validate(row) if row else None

    def list(self, status: AgentStatus | None = None) -> list[AgentProfile]:
        with self.db.session() as s:
            q = select(AgentModel).order_by(AgentModel.agent_id)
            if status:
                q = q.where(AgentModel.status == status.value)
            return [AgentProfile.model_validate(r) for r in s.scalars(q)]

    # ---- writes ------------------------------------------------------------------
    def register(self, data: AgentCreate, actor: str) -> AgentProfile:
        with self.db.session() as s:
            if s.get(AgentModel, data.agent_id):
                raise RegistryError("agent_exists", f"Agent '{data.agent_id}' already exists", 409)
            payload = data.model_dump(mode="json")
            row = AgentModel(**payload, status=AgentStatus.ACTIVE.value)
            s.add(row)
            s.flush()
            append_audit(s, actor=actor, action_type="agent_registered", agent_id=data.agent_id,
                         details={"role": data.role, "allowed_tools": data.allowed_tools})
            return AgentProfile.model_validate(row)

    def update(self, agent_id: str, data: AgentUpdate, actor: str) -> AgentProfile:
        changes = data.model_dump(mode="json", exclude_unset=True)
        if not changes:
            raise RegistryError("no_changes", "No updatable fields were provided", 400)
        with self.db.session() as s:
            row = s.get(AgentModel, agent_id)
            if not row:
                raise RegistryError("agent_not_found", f"Agent '{agent_id}' not found", 404)
            if row.status == AgentStatus.DEACTIVATED.value:
                raise RegistryError("agent_deactivated", "Deactivated agents cannot be modified", 409)
            before = {k: getattr(row, k) for k in changes}
            for key, value in changes.items():
                setattr(row, key, value)
            row.version += 1
            row.updated_at = utcnow()
            append_audit(s, actor=actor, action_type="agent_updated", agent_id=agent_id,
                         details={"changed_fields": sorted(changes), "before": before, "after": changes,
                                  "new_version": row.version})
            return AgentProfile.model_validate(row)

    def set_status(self, agent_id: str, new_status: AgentStatus, reason: str, actor: str,
                   session=None) -> AgentProfile:  # noqa: ANN001
        """Change agent status with eligibility rules. Accepts an existing session for atomic use."""
        if session is not None:
            return self._set_status(session, agent_id, new_status, reason, actor)
        with self.db.session() as s:
            return self._set_status(s, agent_id, new_status, reason, actor)

    def _set_status(self, s, agent_id: str, new_status: AgentStatus, reason: str, actor: str) -> AgentProfile:  # noqa: ANN001
        row = s.get(AgentModel, agent_id)
        if not row:
            raise RegistryError("agent_not_found", f"Agent '{agent_id}' not found", 404)
        current = AgentStatus(row.status)
        allowed = {
            AgentStatus.SUSPENDED: {AgentStatus.ACTIVE},
            AgentStatus.ACTIVE: {AgentStatus.SUSPENDED},
            AgentStatus.DEACTIVATED: {AgentStatus.ACTIVE, AgentStatus.SUSPENDED},
        }[new_status]
        if current not in allowed:
            raise RegistryError(
                "invalid_status_transition",
                f"Cannot change agent status from {current.value} to {new_status.value}",
                409,
            )
        row.status = new_status.value
        row.status_reason = reason
        row.version += 1
        row.updated_at = utcnow()
        action = {
            AgentStatus.SUSPENDED: "agent_suspended",
            AgentStatus.ACTIVE: "agent_reactivated",
            AgentStatus.DEACTIVATED: "agent_deactivated",
        }[new_status]
        append_audit(s, actor=actor, action_type=action, agent_id=agent_id,
                     details={"from": current.value, "to": new_status.value, "reason": reason})
        s.flush()
        return AgentProfile.model_validate(row)

    # ---- seeding -------------------------------------------------------------------
    def seed(self) -> list[str]:
        """Insert any missing example agents. Existing profiles are left untouched."""
        created: list[str] = []
        for agent in SEED_AGENTS:
            with self.db.session() as s:
                if s.get(AgentModel, agent.agent_id):
                    continue
            self.register(agent, actor="system:seed")
            created.append(agent.agent_id)
        if created:
            logger.info("Seeded agents: %s", ", ".join(created))
        return created

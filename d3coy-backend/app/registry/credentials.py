"""Per-agent credentials: the agent's identity comes from a token it holds, not from the request body.

Tokens are 256-bit random values shown once at issue time. Only their SHA-256 hash is stored
(adequate for high-entropy random tokens; no password-style KDF is needed).
"""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select

from app.database.db import Database
from app.database.models import AgentCredentialModel, AgentModel
from app.database.repositories import append_audit, new_id
from app.schemas.common import utcnow

TOKEN_PREFIX = "d3a_"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class CredentialError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


class CredentialStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def issue(self, agent_id: str, actor: str, label: str | None = None) -> dict[str, str]:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        with self.db.session() as s:
            if s.get(AgentModel, agent_id) is None:
                raise CredentialError("agent_not_found", f"Agent '{agent_id}' not found", 404)
            cred = AgentCredentialModel(credential_id=new_id("cred"), agent_id=agent_id,
                                        token_hash=hash_token(token), label=label)
            s.add(cred)
            append_audit(s, actor=actor, action_type="credential_issued", agent_id=agent_id,
                         details={"credential_id": cred.credential_id, "label": label})
            return {"credential_id": cred.credential_id, "agent_id": agent_id, "token": token}

    def revoke(self, agent_id: str, credential_id: str, actor: str) -> None:
        with self.db.session() as s:
            cred = s.get(AgentCredentialModel, credential_id)
            if cred is None or cred.agent_id != agent_id:
                raise CredentialError("credential_not_found", "Credential not found", 404)
            cred.revoked_at = cred.revoked_at or utcnow()
            append_audit(s, actor=actor, action_type="credential_revoked", agent_id=agent_id,
                         details={"credential_id": credential_id})

    def list(self, agent_id: str) -> list[dict[str, str | None]]:
        with self.db.session() as s:
            rows = s.scalars(select(AgentCredentialModel).where(AgentCredentialModel.agent_id == agent_id))
            return [{"credential_id": r.credential_id, "label": r.label, "created_at": r.created_at.isoformat(),
                     "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
                     "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None} for r in rows]

    def authenticate(self, token: str | None) -> str | None:
        """Return the agent_id for a valid, unrevoked token, else None."""
        if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 200:
            return None
        with self.db.session() as s:
            cred = s.scalars(select(AgentCredentialModel)
                             .where(AgentCredentialModel.token_hash == hash_token(token))).first()
            if cred is None or cred.revoked_at is not None:
                return None
            cred.last_used_at = utcnow()
            return cred.agent_id

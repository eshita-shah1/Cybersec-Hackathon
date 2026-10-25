from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_container, require_admin
from app.services.container import Container
from app.services.event_service import ServiceError

router = APIRouter(prefix="/api/agent", tags=["live agent"])


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: Literal["benign", "adversarial"] = Field(default="benign")


@router.get("/status")
def agent_status(c: Container = Depends(get_container)) -> dict[str, Any]:
    """Whether the server can launch the real LLM agent (provider key configured, agent seeded)."""
    return c.agent_runner.status()


@router.post("/run")
def run_agent(body: RunRequest, c: Container = Depends(get_container),
              _: str = Depends(require_admin)) -> dict[str, Any]:
    """Launch the real procurement agent server-side. Returns immediately with a run record;
    the agent's actions stream over /ws/events and are recorded like any other events."""
    return c.agent_runner.start(body.task)


@router.get("/runs")
def list_runs(c: Container = Depends(get_container)) -> dict[str, Any]:
    return {"items": c.agent_runner.list_runs(), "active_run": c.agent_runner.status()["active_run"]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    run = c.agent_runner.get_run(run_id)
    if run is None:
        raise ServiceError("run_not_found", f"Run '{run_id}' not found", 404)
    return run

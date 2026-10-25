from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_container, require_admin
from app.services.container import Container
from app.simulator.agent_simulator import list_scenarios

router = APIRouter(prefix="/api/scenarios", tags=["simulation"])


@router.get("")
def get_scenarios() -> dict[str, Any]:
    return {"items": list_scenarios()}


@router.post("/{scenario_id}/run")
def run_scenario(scenario_id: str, c: Container = Depends(get_container),
                 _: str = Depends(require_admin)) -> dict[str, Any]:
    """Run a predefined scenario through the normal event pipeline (same code path as POST /api/events)."""
    return c.simulator.run(scenario_id, actor="api")

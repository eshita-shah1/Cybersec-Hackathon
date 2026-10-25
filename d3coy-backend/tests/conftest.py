"""Shared fixtures. Every test gets its own SQLite file and its own copy of the model artifacts,
so tests are isolated and never modify ./data or ./artifacts."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.detection.baseline_training import train_baseline
from app.main import create_app
from app.services.container import Container, build_container

ADMIN_KEY = "test-admin-key-123"
ADMIN = {"X-API-Key": ADMIN_KEY}


@pytest.fixture(scope="session")
def trained_artifacts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Train the baseline once per test session with the documented fixed seed."""
    path = tmp_path_factory.mktemp("artifacts")
    train_baseline(path, seed=42)
    return path


def semantic_available() -> bool:
    try:
        from app.detection.intent_validator import _load_sentence_transformer

        _load_sentence_transformer("sentence-transformers/all-MiniLM-L6-v2", allow_download=False)
        return True
    except Exception:
        return False


requires_semantic = pytest.mark.skipif(not semantic_available(),
                                       reason="Sentence Transformers model not cached locally")


def make_settings(tmp_path: Path, artifacts: Path, **overrides) -> Settings:
    art = tmp_path / "artifacts"
    if not art.exists():
        shutil.copytree(artifacts, art)
    values = dict(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        artifacts_dir=str(art),
        admin_api_key=ADMIN_KEY,
        auto_train_baseline=False,
        semantic_allow_download=False,
        cors_origins="http://localhost:5173",
        log_level="WARNING",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings(tmp_path: Path, trained_artifacts: Path) -> Settings:
    return make_settings(tmp_path, trained_artifacts)


@pytest.fixture
def container(settings: Settings) -> Iterator[Container]:
    c = build_container(settings)
    c.startup()
    yield c
    c.db.dispose()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as tc:
        yield tc


def event(agent_id: str, tool: str, action: str, resource: str, description: str, **extra) -> dict:
    return {"agent_id": agent_id, "tool_name": tool, "action": action, "target_resource": resource,
            "action_description": description, **extra}


FINANCE_NORMAL = [
    ("read_expenses", "read", "finance:expenses/2026-09", "Read the expense records for September"),
    ("calculate_totals", "compute", "finance:expenses/2026-09", "Calculate total spending for each category"),
    ("generate_financial_summary", "write", "finance:reports/2026-09", "Write the September spending summary"),
]

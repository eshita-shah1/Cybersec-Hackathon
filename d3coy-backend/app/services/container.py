"""Builds and wires every component once. Tests construct their own container with custom settings."""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from app.config import Settings
from app.database.db import Database
from app.detection.behavior_detector import BehaviorDetector
from app.detection.intent_validator import IntentValidator
from app.detection.online_detector import OnlineBehaviorDetector
from app.detection.policy_engine import PolicyEngine
from app.enforcement.executor_client import ExecutorClient
from app.enforcement.gateway import EnforcementGateway
from app.enforcement.tool_registry import ToolRegistry
from app.monitoring.activity_monitor import ActivityMonitor
from app.monitoring.event_normalizer import EventNormalizer
from app.registry.agent_registry import AgentRegistry
from app.registry.credentials import CredentialStore
from app.risk.response_engine import ResponseEngine
from app.risk.risk_scorer import RiskScorer
from app.services.agent_runner import AgentRunner
from app.services.event_service import EventService
from app.services.review_service import ReviewService
from app.services.websocket_manager import WebSocketManager
from app.simulator.agent_simulator import AgentSimulator

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    db: Database
    tools: ToolRegistry
    registry: AgentRegistry
    normalizer: EventNormalizer
    monitor: ActivityMonitor
    policy: PolicyEngine
    behavior: BehaviorDetector
    online: OnlineBehaviorDetector
    intent: IntentValidator
    scorer: RiskScorer
    responder: ResponseEngine
    gateway: EnforcementGateway
    executor: ExecutorClient
    credentials: CredentialStore
    ws: WebSocketManager
    events: EventService
    reviews: ReviewService
    simulator: AgentSimulator
    agent_runner: AgentRunner

    def startup(self, load_models: bool = True) -> None:
        """Create tables, seed, and load (or train) models. Idempotent."""
        self.db.create_all()
        if self.settings.seed_on_startup:
            self.registry.seed()
        if not load_models:
            return
        if not self.behavior.load() and self.settings.auto_train_baseline:
            from app.detection.baseline_training import train_baseline

            logger.info("No behavioral baseline found: training one now (AUTO_TRAIN_BASELINE=true)...")
            train_baseline(self.settings.artifacts_path, window_size=self.settings.behavior_window_size,
                           min_history=self.settings.behavior_min_history,
                           long_history=self.settings.behavior_long_history)
            self.behavior.load()
        self.online.load()
        self.intent.load()


def build_container(settings: Settings) -> Container:
    db = Database(settings.database_url)
    tools = ToolRegistry()
    registry = AgentRegistry(db)
    normalizer = EventNormalizer(tools, settings.internal_domain_list)
    monitor = ActivityMonitor(settings)
    policy = PolicyEngine()
    behavior = BehaviorDetector(settings.artifacts_path, settings.behavior_window_size,
                                settings.behavior_min_history, settings.behavior_long_history)
    online = OnlineBehaviorDetector(settings.artifacts_path, settings.river_enabled, settings.river_min_trusted_batch)
    intent = IntentValidator(settings.semantic_enabled, settings.semantic_model_name,
                             settings.semantic_allow_download, settings.semantic_fallback,
                             settings.semantic_aligned_similarity, settings.semantic_misaligned_similarity)
    scorer = RiskScorer(settings)
    responder = ResponseEngine(settings)
    if settings.permission_signing_key is not None and settings.permission_signing_key.get_secret_value():
        signing_key = settings.permission_signing_key.get_secret_value().encode()
    else:
        # No shared key configured: use a random per-process key. Simulated tools still work;
        # the separate sandbox executor (which needs the SAME key) will reject every request.
        signing_key = secrets.token_bytes(32)
        logger.warning("PERMISSION_SIGNING_KEY not set; using a random key. Sandbox tools will not "
                       "run unless the executor shares this key (set PERMISSION_SIGNING_KEY).")
    executor = ExecutorClient(settings.executor_url, settings.executor_timeout_seconds)
    gateway = EnforcementGateway(db, tools, signing_key=signing_key,
                                 permission_ttl=settings.permission_ttl_seconds, executor=executor)
    credentials = CredentialStore(db)
    ws = WebSocketManager()
    events = EventService(settings=settings, db=db, registry=registry, normalizer=normalizer, monitor=monitor,
                          policy=policy, behavior=behavior, online=online, intent=intent, scorer=scorer,
                          responder=responder, gateway=gateway, ws=ws)
    reviews = ReviewService(db, registry, normalizer, policy, gateway, events, ws)
    simulator = AgentSimulator(events, registry)
    container = Container(settings, db, tools, registry, normalizer, monitor, policy, behavior, online, intent,
                          scorer, responder, gateway, executor, credentials, ws, events, reviews, simulator,
                          agent_runner=None)
    container.agent_runner = AgentRunner(container)
    return container

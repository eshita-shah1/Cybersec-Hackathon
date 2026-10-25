"""The end-to-end monitoring and enforcement pipeline.

    request -> registry (trusted profile) -> activity monitor (persist ATTEMPTED)
            -> policy -> behavior (IF + River) -> semantic -> context -> risk -> response
            -> enforcement (block | hold | gateway execute) -> audit -> WebSocket

Every caller — the REST endpoint, the scenario runner and the CLI demo — goes through
``EventService.process``. There is no other path to the tool executor.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database.db import Database
from app.database.models import AlertModel, DetectionResultModel, EventModel, ReviewModel
from app.database.repositories import (
    append_audit,
    count_recent_high_severity_blocks,
    find_by_request_id,
    new_id,
    transition_event,
)
from app.detection.behavior_detector import BehaviorDetector
from app.detection.feature_extractor import ActivityRecord, informational_features
from app.detection.intent_validator import IntentValidator
from app.detection.online_detector import OnlineBehaviorDetector
from app.detection.policy_engine import PolicyEngine, PolicyResult
from app.enforcement.gateway import EnforcementGateway, ExecutionRefused
from app.monitoring.activity_monitor import ActivityMonitor, EventValidationError
from app.monitoring.event_normalizer import EventNormalizer, NormalizedAction
from app.registry.agent_registry import AgentRegistry
from app.risk.context_risk import assess_context
from app.risk.response_engine import ResponseEngine
from app.risk.risk_scorer import RiskScorer
from app.schemas.agents import AgentProfile
from app.schemas.common import (
    AgentStatus,
    Decision,
    DetectorStatus,
    ExecutionStatus,
    PolicyStatus,
    RequestStatus,
    SignalResult,
    utcnow,
)
from app.schemas.events import EventResult, EventSubmission
from app.services.websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status_code, self.details = code, message, status_code, details


class EventService:
    def __init__(self, *, settings: Settings, db: Database, registry: AgentRegistry, normalizer: EventNormalizer,
                 monitor: ActivityMonitor, policy: PolicyEngine, behavior: BehaviorDetector,
                 online: OnlineBehaviorDetector, intent: IntentValidator, scorer: RiskScorer,
                 responder: ResponseEngine, gateway: EnforcementGateway, ws: WebSocketManager) -> None:
        self.settings = settings
        self.db = db
        self.registry = registry
        self.normalizer = normalizer
        self.monitor = monitor
        self.policy = policy
        self.behavior = behavior
        self.online = online
        self.intent = intent
        self.scorer = scorer
        self.responder = responder
        self.gateway = gateway
        self.ws = ws
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _agent_lock(self, agent_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(agent_id, threading.Lock())

    # =====================================================================================
    def process(self, sub: EventSubmission, identity: str | None = None) -> EventResult:
        """Run an attempted action through the pipeline.

        ``identity`` is the authenticated agent id resolved from the caller's credential by the
        API layer. The trusted profile is always looked up from it (never from the request
        body). When it is given, the body's ``agent_id`` must match it, so a caller cannot act
        as another agent. ``identity=None`` means an internal, trusted caller (the simulator,
        the review workflow, tests); then the body's ``agent_id`` is used directly."""
        try:
            self.monitor.validate_timestamp(sub)
        except EventValidationError as exc:
            raise ServiceError("invalid_timestamp", exc.message, 422) from exc

        if identity is not None and identity != sub.agent_id:
            raise ServiceError("identity_mismatch",
                               "The authenticated agent does not match the agent_id in the request.", 403)
        trusted_agent_id = identity or sub.agent_id
        profile = self.registry.get(trusted_agent_id)
        action = self.normalizer.normalize(sub)
        if profile is None:
            self._reject_unknown_agent(sub, action)

        with self._agent_lock(profile.agent_id):
            request_id = sub.request_id or new_id("req")
            try:
                with self.db.session() as s:
                    existing = find_by_request_id(s, profile.agent_id, request_id)
                    if existing is not None:
                        append_audit(s, actor=f"agent:{profile.agent_id}", action_type="duplicate_submission",
                                     event_id=existing.event_id, agent_id=profile.agent_id,
                                     details={"request_id": request_id})
                        logger.info("event=%s duplicate request_id; returning original result", existing.event_id)
                        return self.monitor.to_result(s, existing, duplicate=True)
                    ev = self.monitor.record_attempt(s, sub, action, profile, request_id)
                    event_id = ev.event_id
            except IntegrityError as exc:  # concurrent duplicate from another worker
                raise ServiceError("duplicate_request", "This request_id was already submitted", 409) from exc

            try:
                return self._evaluate_and_enforce(event_id, action, profile)
            except ServiceError:
                raise
            except Exception as exc:
                logger.exception("event=%s pipeline failure; failing closed", event_id)
                self._fail_closed(event_id, profile.agent_id)
                raise ServiceError("processing_error", "The action could not be evaluated and was blocked "
                                   "(fail-closed).", 500, {"event_id": event_id}) from exc

    # =====================================================================================
    def _reject_unknown_agent(self, sub: EventSubmission, action: NormalizedAction) -> None:
        policy = self.policy.evaluate(None, action)
        with self.db.session() as s:
            append_audit(s, actor=f"unverified:{sub.agent_id}", action_type="unknown_agent_attempt",
                         agent_id=sub.agent_id,
                         details={"tool": action.tool_name, "operation": action.operation,
                                  "resource": action.resource, "policy": policy.explanation,
                                  "execution": "not executed"})
            alert = AlertModel(alert_id=new_id("alr"), event_id=None, agent_id=sub.agent_id[:64],
                               alert_type="UNKNOWN_AGENT", severity="critical",
                               title="Unregistered agent identity attempted an action",
                               description=policy.explanation)
            s.add(alert)
        self.ws.publish("security_alert", {"alert_type": "UNKNOWN_AGENT", "agent_id": sub.agent_id,
                                           "decision": "BLOCK", "explanations": [policy.explanation],
                                           "timestamp": utcnow().isoformat()})
        raise ServiceError("unknown_agent", f"Agent '{sub.agent_id}' is not registered; the action was "
                           "rejected and not executed.", 404)

    def _fail_closed(self, event_id: str, agent_id: str) -> None:
        try:
            with self.db.session() as s:
                transition_event(s, event_id, [RequestStatus.ATTEMPTED.value, RequestStatus.EVALUATED.value],
                                 request_status=RequestStatus.BLOCKED.value,
                                 execution_status=ExecutionStatus.NOT_EXECUTED.value, decision=Decision.BLOCK.value,
                                 error="internal processing error (fail-closed)", updated_at=utcnow())
                append_audit(s, actor="system", action_type="fail_closed_block", event_id=event_id,
                             agent_id=agent_id, details={"reason": "pipeline error"})
        except Exception:
            logger.exception("event=%s could not record fail-closed state", event_id)

    # =====================================================================================
    def _evaluate_and_enforce(self, event_id: str, action: NormalizedAction, profile: AgentProfile) -> EventResult:
        now = utcnow()
        # ---- 1. policy (fail-closed) ----
        try:
            policy = self.policy.evaluate(profile, action)
        except Exception:
            logger.exception("event=%s policy engine error", event_id)
            policy = PolicyEngine.engine_failure("policy engine error")

        # ---- 2. behavior ----
        with self.db.session() as s:
            history = self.monitor.history(s, profile.agent_id, event_id)
        current = ActivityRecord(action.tool_name, action.operation, action.resource, action.sensitivity.value,
                                 policy.status.value, now)
        behavior, if_res, river_res, features = self._behavior(event_id, profile.role, current, history)

        # ---- 3. semantic ----
        try:
            semantic = self.intent.evaluate(profile.assigned_task, profile.task_description,
                                            action.action_description, action.server_description)
        except Exception:
            logger.exception("event=%s semantic validator error", event_id)
            semantic = SignalResult(detector="semantic", status=DetectorStatus.ERROR,
                                    explanation="Semantic validator failed; its signal was excluded.")

        # ---- 4. context + risk + response ----
        context = assess_context(action, profile, policy)
        risk = self.scorer.score(
            policy.severity_score,
            behavior.score if behavior.available else None,
            semantic.score if semantic.available else None,
            context.score,
            explanations={"behavior": behavior.explanation, "semantic": semantic.explanation,
                          "context": context.explanation},
        )
        response = self.responder.decide(policy, risk, behavior, semantic, context, action.sensitivity.rank)
        decision = response.decision
        reasons = response.reasons + [x for x in (behavior.explanation if behavior.available else None,
                                                  semantic.explanation if semantic.available else None,
                                                  context.explanation) if x]

        # ---- 5. persist evaluation ----
        with self.db.session() as s:
            ok = transition_event(
                s, event_id, [RequestStatus.ATTEMPTED.value],
                request_status=RequestStatus.EVALUATED.value,
                policy_status=policy.status.value, policy_severity=policy.severity.value,
                policy_result=policy.model_dump(mode="json"), behavioral_result=behavior.model_dump(mode="json"),
                semantic_result=semantic.model_dump(mode="json"), context_result=context.model_dump(mode="json"),
                features=features, risk_score=risk.score, risk_level=risk.level.value,
                risk_breakdown={**risk.as_dict(), "overrides": response.overrides},
                recommended_response=response.recommended_response.value, decision=decision.value,
                response_reasons=reasons, updated_at=utcnow())
            if not ok:
                raise RuntimeError("event left ATTEMPTED state unexpectedly")
            for sig in (if_res, river_res, semantic):
                s.add(DetectionResultModel(event_id=event_id, detector=sig.detector, status=sig.status.value,
                                           score=sig.score, details={"explanation": sig.explanation, **sig.details}))
            s.add(DetectionResultModel(event_id=event_id, detector="policy_engine", status=policy.status.value,
                                       score=policy.severity_score, details=policy.model_dump(mode="json")))
            s.add(DetectionResultModel(event_id=event_id, detector="context", status="ok", score=context.score,
                                       details=context.model_dump(mode="json")))
            append_audit(s, actor="system:risk_engine", action_type="action_evaluated", event_id=event_id,
                         agent_id=profile.agent_id,
                         details={"policy": policy.status.value, "risk_score": risk.score,
                                  "risk_level": risk.level.value, "recommended": response.recommended_response.value,
                                  "decision": decision.value, "overrides": response.overrides})
            if decision != Decision.ALLOW:
                s.add(self._alert_for(event_id, profile.agent_id, decision, policy, risk.score, reasons))

        logger.info("event=%s agent=%s tool=%s policy=%s risk=%.1f decision=%s", event_id, profile.agent_id,
                    action.tool_name, policy.status.value, risk.score, decision.value)

        # ---- 6. enforcement ----
        if decision == Decision.BLOCK:
            with self.db.session() as s:
                transition_event(s, event_id, [RequestStatus.EVALUATED.value],
                                 request_status=RequestStatus.BLOCKED.value,
                                 execution_status=ExecutionStatus.NOT_EXECUTED.value, updated_at=utcnow())
                append_audit(s, actor="system:gateway", action_type="action_blocked", event_id=event_id,
                             agent_id=profile.agent_id, details={"reason": reasons[0] if reasons else ""})
            self._maybe_escalate(event_id, profile, policy)
        elif decision == Decision.REVIEW:
            with self.db.session() as s:
                transition_event(s, event_id, [RequestStatus.EVALUATED.value],
                                 request_status=RequestStatus.PENDING_REVIEW.value,
                                 execution_status=ExecutionStatus.AWAITING_REVIEW.value, updated_at=utcnow())
                s.add(ReviewModel(review_id=new_id("rev"), event_id=event_id, agent_id=profile.agent_id,
                                  reason=" ".join(response.reasons[1:]) or response.reasons[0],
                                  expires_at=utcnow() + timedelta(minutes=self.settings.review_ttl_minutes)))
                append_audit(s, actor="system:gateway", action_type="held_for_review", event_id=event_id,
                             agent_id=profile.agent_id, details={"overrides": response.overrides})
        else:
            try:
                outcome = self.gateway.execute(event_id)
            except ExecutionRefused as exc:
                logger.warning("event=%s execution refused: %s", event_id, exc)
                outcome = {"status": "REFUSED"}
            if (features is not None and outcome.get("status") == ExecutionStatus.SUCCEEDED.value):
                trusted = (policy.status == PolicyStatus.ALLOW and decision == Decision.ALLOW
                           and risk.score < self.settings.risk_monitor_threshold
                           and if_res.available and (if_res.score or 0) < 25
                           and features.get("prior_denied_ratio", 1) == 0)
                try:
                    self.online.offer(profile.agent_id, features, trusted)
                except Exception:
                    logger.exception("event=%s River update failed", event_id)

        with self.db.session() as s:
            ev = s.get(EventModel, event_id)
            result = self.monitor.to_result(s, ev)
        self.publish_event(result)
        return result

    # =====================================================================================
    def _behavior(self, event_id: str, role: str, current: ActivityRecord, history: list[ActivityRecord]
                  ) -> tuple[SignalResult, SignalResult, SignalResult, dict[str, float] | None]:
        info = informational_features(current.timestamp or utcnow(), history[-(self.settings.behavior_window_size - 1):])
        try:
            features, problem = self.behavior.features_for(role, current, history)
        except Exception:
            logger.exception("event=%s feature extraction error", event_id)
            features, problem = None, SignalResult(detector="isolation_forest", status=DetectorStatus.ERROR,
                                                   explanation="Feature extraction failed.")
        if problem is not None:
            river = SignalResult(detector="river_half_space_trees", status=problem.status,
                                 explanation="Not evaluated: no behavioral features for this event.")
            combined = SignalResult(detector="behavior", status=problem.status, explanation=problem.explanation,
                                    details={"isolation_forest": problem.model_dump(mode="json"),
                                             "river": river.model_dump(mode="json"), "informational": info})
            return combined, problem, river, None

        try:
            if_res = self.behavior.evaluate(features)
        except Exception:
            logger.exception("event=%s isolation forest error", event_id)
            if_res = SignalResult(detector="isolation_forest", status=DetectorStatus.ERROR,
                                  explanation="Isolation Forest scoring failed.")
        try:
            river = self.online.evaluate(features)
        except Exception:
            logger.exception("event=%s river error", event_id)
            river = SignalResult(detector="river_half_space_trees", status=DetectorStatus.ERROR,
                                 explanation="River scoring failed.")
        w = self.settings.river_weight
        if if_res.available and river.available:
            score, how = (1 - w) * if_res.score + w * river.score, f"{1 - w:.2f} x IsolationForest + {w:.2f} x River"
        elif if_res.available:
            score, how = if_res.score, "IsolationForest only (River not available or not mature)"
        elif river.available:
            score, how = river.score, "River only (Isolation Forest unavailable)"
        else:
            score, how = None, "no behavioral model available"
        status = DetectorStatus.OK if score is not None else (if_res.status if if_res.status != DetectorStatus.OK
                                                              else river.status)
        combined = SignalResult(
            detector="behavior", status=status, score=round(score, 2) if score is not None else None,
            explanation=if_res.explanation if if_res.available else (river.explanation or if_res.explanation),
            details={"combination": how, "isolation_forest": if_res.model_dump(mode="json"),
                     "river": river.model_dump(mode="json"), "features": features, "informational": info})
        return combined, if_res, river, features

    # =====================================================================================
    @staticmethod
    def _alert_for(event_id: str, agent_id: str, decision: Decision, policy: PolicyResult, score: float,
                   reasons: list[str]) -> AlertModel:
        if decision == Decision.BLOCK:
            atype = "POLICY_VIOLATION" if policy.status == PolicyStatus.DENY else "HIGH_RISK"
            severity = policy.severity.value if policy.status == PolicyStatus.DENY else "high"
            title = f"Blocked: {policy.violation_type or 'high risk action'}"
        elif decision == Decision.REVIEW:
            atype, severity, title = "REVIEW_REQUIRED", "high" if score >= 50 else "medium", "Action held for review"
        else:
            atype, severity, title = "ANOMALY_MONITOR", "low", "Action allowed under monitoring"
        return AlertModel(alert_id=new_id("alr"), event_id=event_id, agent_id=agent_id, alert_type=atype,
                          severity=severity, title=title, description=" ".join(reasons[:3]))

    def _maybe_escalate(self, event_id: str, profile: AgentProfile, policy: PolicyResult) -> None:
        cfg = self.settings
        if not cfg.escalation_enabled or policy.severity.rank < 3:
            return
        with self.db.session() as s:
            since = utcnow() - timedelta(minutes=cfg.escalation_window_minutes)
            count = count_recent_high_severity_blocks(s, profile.agent_id, since)
            current = self.registry.get(profile.agent_id)
            if count < cfg.escalation_block_count or current is None or current.status != AgentStatus.ACTIVE:
                return
            reason = (f"Automatically suspended after {count} high-severity blocked actions within "
                      f"{cfg.escalation_window_minutes} minutes. Reactivation requires an authorized analyst.")
            self.registry.set_status(profile.agent_id, AgentStatus.SUSPENDED, reason, "system:escalation", session=s)
            esc = {"action": "agent_suspended", "reason": reason, "blocked_events_in_window": count}
            ev = s.get(EventModel, event_id)
            ev.escalation = esc
            s.add(AlertModel(alert_id=new_id("alr"), event_id=event_id, agent_id=profile.agent_id,
                             alert_type="ESCALATION", severity="critical", title="Agent suspended", description=reason))
            append_audit(s, actor="system:escalation", action_type="escalation", event_id=event_id,
                         agent_id=profile.agent_id, details=esc)
        logger.warning("agent=%s suspended by escalation policy (event=%s)", profile.agent_id, event_id)
        self.ws.publish("agent_status", {"agent_id": profile.agent_id, "status": "suspended", "reason": reason,
                                         "event_id": event_id})

    def publish_event(self, r: EventResult) -> None:
        self.ws.publish("security_event", {
            "event_id": r.event_id, "agent_id": r.agent_id, "timestamp": r.received_at.isoformat(),
            "tool_name": r.tool_name, "action": r.action, "target_resource": r.target_resource,
            "policy_status": (r.policy_result or {}).get("status"), "risk_score": r.risk_score,
            "risk_level": r.risk_level.value if r.risk_level else None,
            "decision": r.decision.value if r.decision else None, "request_status": r.request_status.value,
            "execution_status": r.execution_status.value, "explanations": r.explanations[:4],
            "review_id": r.review_id, "alert_ids": r.alert_ids, "escalation": r.escalation})

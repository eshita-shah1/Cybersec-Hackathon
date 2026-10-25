"""Semantic task-intent validation with Sentence Transformers.

Compares the trusted assigned task (from the registry, never from the request) with the
caller's natural-language ``action_description``. The similarity between the task and a
server-generated description of the concrete tool call (tool + operation + resource class) is
also computed and reported, but only as information: in testing, generic tool descriptions
were not separable enough from misleading descriptions to gate on reliably. A caller can
therefore write a misleading description; this is why the policy engine and contextual risk
always evaluate the actual tool call, not the description.

The mismatch score maps cosine similarity (task vs. description) linearly:
  sim >= aligned (default 0.45)    -> 0
  sim <= misaligned (default 0.10) -> 100
This is an alignment signal for prototype risk scoring, not a probability of malicious intent,
and high similarity never makes a prohibited action acceptable.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from functools import lru_cache
from typing import Any, Protocol

import numpy as np

from app.schemas.common import DetectorStatus, SignalResult

logger = logging.getLogger(__name__)

LOW_CONSISTENCY = 0.05  # informational note only


class _Backend(Protocol):
    name: str

    def similarity(self, a: str, b: str) -> float: ...


class SentenceTransformerBackend:
    def __init__(self, model: Any, model_name: str) -> None:
        self.model = model
        self.name = f"sentence-transformers:{model_name.split('/')[-1]}"
        self._lock = threading.Lock()
        self._embed = lru_cache(maxsize=2048)(self._embed_uncached)

    def _embed_uncached(self, text: str) -> np.ndarray:
        with self._lock:
            return np.asarray(self.model.encode(text, normalize_embeddings=True))

    def similarity(self, a: str, b: str) -> float:
        return float(np.clip(np.dot(self._embed(a), self._embed(b)), -1.0, 1.0))


class LexicalBackend:
    """NOT semantic: token-overlap (Jaccard) fallback, enabled only with SEMANTIC_FALLBACK=lexical.
    It is reported under its own detector name so it is never mistaken for embedding analysis."""

    name = "lexical-jaccard-fallback"
    _stop = {"the", "a", "an", "and", "of", "to", "for", "on", "in", "with", "by", "is", "are", "from", "each"}

    def _tokens(self, text: str) -> set[str]:
        return {t for t in re.findall(r"[a-z]+", text.lower()) if t not in self._stop and len(t) > 2}

    def similarity(self, a: str, b: str) -> float:
        ta, tb = self._tokens(a), self._tokens(b)
        return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


_MODEL_CACHE: dict[str, Any] = {}
_MODEL_LOCK = threading.Lock()


def _load_sentence_transformer(model_name: str, allow_download: bool) -> Any:
    """Load once per process. Tries the local cache first so restarts never re-download."""
    with _MODEL_LOCK:
        if model_name in _MODEL_CACHE:
            return _MODEL_CACHE[model_name]
        from sentence_transformers import SentenceTransformer  # heavy import, deferred

        try:
            model = SentenceTransformer(model_name, local_files_only=True)
            logger.info("Semantic model '%s' loaded from local cache", model_name)
        except Exception:
            if not allow_download:
                raise
            logger.info("Semantic model '%s' not cached; downloading (first run only)...", model_name)
            model = SentenceTransformer(model_name)
        _MODEL_CACHE[model_name] = model
        return model


class IntentValidator:
    def __init__(self, enabled: bool, model_name: str, allow_download: bool, fallback: str,
                 aligned: float, misaligned: float) -> None:
        self.enabled = enabled
        self.model_name = model_name
        self.allow_download = allow_download
        self.fallback = fallback
        self.aligned = aligned
        self.misaligned = misaligned
        self.backend: _Backend | None = None
        self.load_error: str | None = None
        self.load_seconds: float | None = None

    def load(self) -> None:
        if not self.enabled:
            self.load_error = "Semantic validation disabled by configuration (SEMANTIC_ENABLED=false)."
            return
        start = time.perf_counter()
        try:
            self.backend = SentenceTransformerBackend(
                _load_sentence_transformer(self.model_name, self.allow_download), self.model_name)
            self.load_error = None
        except Exception as exc:
            self.load_error = f"Semantic model '{self.model_name}' unavailable: {type(exc).__name__}: {exc}"
            logger.warning(self.load_error)
            if self.fallback == "lexical":
                self.backend = LexicalBackend()
                logger.warning("Using LEXICAL fallback for intent validation (not semantic analysis).")
        self.load_seconds = round(time.perf_counter() - start, 2)

    def status(self) -> dict[str, Any]:
        if self.backend is None:
            return {"status": "disabled" if not self.enabled else "unavailable", "reason": self.load_error}
        return {"status": "ready" if isinstance(self.backend, SentenceTransformerBackend) else "fallback",
                "detector": self.backend.name, "model_name": self.model_name, "load_seconds": self.load_seconds,
                "fallback_reason": self.load_error}

    def mismatch(self, sim: float) -> float:
        span = max(self.aligned - self.misaligned, 1e-9)
        return float(np.clip((self.aligned - sim) / span, 0.0, 1.0) * 100.0)

    def evaluate(self, assigned_task: str, task_description: str, action_description: str,
                 server_description: str) -> SignalResult:
        if self.backend is None:
            status = DetectorStatus.DISABLED if not self.enabled else DetectorStatus.UNAVAILABLE
            return SignalResult(detector="semantic", status=status,
                                explanation=self.load_error or "Semantic validator not loaded.")
        b = self.backend
        task_texts = [t for t in (assigned_task, task_description) if t]
        sim_caller = max(b.similarity(t, action_description) for t in task_texts)
        sim_server = max(b.similarity(t, server_description) for t in task_texts)
        consistency = b.similarity(action_description, server_description)
        used = sim_caller
        score = round(self.mismatch(used), 2)
        if score >= 60:
            expl = "The proposed action appears unrelated to the agent's assigned task."
        elif score >= 25:
            expl = "The proposed action is only partly related to the assigned task."
        else:
            expl = "The proposed action is consistent with the assigned task."
        if consistency < LOW_CONSISTENCY:
            expl += " Note: the description has little overlap with the tool actually requested."
        return SignalResult(
            detector=b.name, status=DetectorStatus.OK, score=score, explanation=expl,
            details={"similarity_task_vs_description": round(sim_caller, 4),
                     "similarity_task_vs_tool_call": round(sim_server, 4),
                     "description_tool_consistency": round(consistency, 4),
                     "similarity_used": round(used, 4),
                     "server_description": server_description,
                     "thresholds": {"aligned": self.aligned, "misaligned": self.misaligned},
                     "interpretation": "cosine similarity alignment signal; not a probability of malicious intent"})

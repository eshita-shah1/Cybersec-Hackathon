"""Secondary, incremental behavioral detector using River Half-Space Trees.

Feature schema: the same 14 bounded [0, 1] features as the Isolation Forest (FEATURE_NAMES).

Update (trust) policy — the model never learns from arbitrary live traffic:
  * An observation is *eligible* only if the event was policy-ALLOW, the final decision was
    ALLOW, the tool executed successfully, the combined risk score was below the monitor
    threshold, the Isolation Forest score was available and below 25, and the agent's window
    contained no prior policy denials.
  * Eligible observations are buffered. The model learns only when RIVER_MIN_TRUSTED_BATCH
    observations from at least two distinct agents have accumulated, and no single agent may
    contribute more than half of a batch (excess is dropped). A single suspicious sequence
    therefore cannot silently redefine "normal".
  * Blocked, reviewed, monitored, failed and denied events are never learned.

The model is pre-trained on the same synthetic baseline as the Isolation Forest. It is
considered mature once it has learned at least 2 x window_size observations; until then its
output is reported but excluded from the risk score.
"""

from __future__ import annotations

import logging
import pickle
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from app.detection.behavior_detector import normalize_anomaly
from app.detection.feature_extractor import FEATURE_NAMES
from app.schemas.common import DetectorStatus, SignalResult

logger = logging.getLogger(__name__)

RIVER_FILE = "river_hst.pkl"
DETECTOR_NAME = "river_half_space_trees"


class OnlineBehaviorDetector:
    def __init__(self, artifacts_dir: Path, enabled: bool, min_trusted_batch: int) -> None:
        self.path = Path(artifacts_dir) / RIVER_FILE
        self.enabled = enabled
        self.min_trusted_batch = min_trusted_batch
        self.model = None
        self.norm: dict[str, float] = {}
        self.learned = 0
        self.window_size = 250
        self.buffer: list[tuple[str, dict[str, float]]] = []
        self.batches_applied = 0
        self.rejected_observations = 0
        self.load_error: str | None = None
        self._lock = threading.Lock()

    # ---- persistence ----
    def load(self) -> bool:
        if not self.enabled:
            self.load_error = "River detector disabled by configuration (RIVER_ENABLED=false)."
            return False
        try:
            with self.path.open("rb") as fh:
                state = pickle.load(fh)  # local artifact written by scripts/train_baseline.py
            self.model, self.norm = state["model"], state["normalization"]
            self.learned, self.window_size = state["learned"], state["window_size"]
            self.batches_applied = state.get("batches_applied", 0)
            self.load_error = None
            return True
        except FileNotFoundError:
            self.load_error = f"No River model at {self.path}; run: python scripts/train_baseline.py"
        except Exception as exc:
            self.load_error = f"Could not load River model: {type(exc).__name__}: {exc}"
        self.model = None
        logger.warning(self.load_error)
        return False

    def _save(self) -> None:
        state = {"model": self.model, "normalization": self.norm, "learned": self.learned,
                 "window_size": self.window_size, "batches_applied": self.batches_applied}
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(state, fh)
        tmp.replace(self.path)

    @property
    def mature(self) -> bool:
        return self.model is not None and self.learned >= 2 * self.window_size

    def status(self) -> dict[str, Any]:
        if self.model is None:
            return {"status": "disabled" if not self.enabled else "unavailable", "reason": self.load_error}
        return {"status": "ready" if self.mature else "warming_up", "model": "river.anomaly.HalfSpaceTrees",
                "observations_learned": self.learned, "mature": self.mature,
                "trusted_buffer": len(self.buffer), "batch_size_required": self.min_trusted_batch,
                "batches_applied": self.batches_applied, "rejected_observations": self.rejected_observations}

    # ---- scoring ----
    def evaluate(self, features: dict[str, float]) -> SignalResult:
        if self.model is None:
            status = DetectorStatus.DISABLED if not self.enabled else DetectorStatus.UNAVAILABLE
            return SignalResult(detector=DETECTOR_NAME, status=status, explanation=self.load_error or "")
        with self._lock:
            raw = float(self.model.score_one({f: features[f] for f in FEATURE_NAMES}))
        score = round(normalize_anomaly(raw, self.norm["q50"], self.norm["q99"]), 2)
        if not self.mature:
            return SignalResult(detector=DETECTOR_NAME, status=DetectorStatus.INSUFFICIENT_DATA, score=None,
                                explanation=f"Online model is warming up ({self.learned} observations); "
                                            "its output is not used in the risk score.",
                                details={"raw_score": round(raw, 5), "unused_score": score})
        return SignalResult(detector=DETECTOR_NAME, status=DetectorStatus.OK, score=score,
                            explanation="Incremental Half-Space Trees score against the trusted baseline.",
                            details={"raw_score": round(raw, 5), "observations_learned": self.learned})

    # ---- trusted updates ----
    def offer(self, agent_id: str, features: dict[str, float], trusted: bool) -> dict[str, Any]:
        """Offer an observation. Only trusted observations are buffered; learning happens in batches."""
        if self.model is None:
            return {"accepted": False, "reason": "model unavailable"}
        if not trusted:
            self.rejected_observations += 1
            return {"accepted": False, "reason": "observation did not satisfy the trust policy"}
        with self._lock:
            self.buffer.append((agent_id, dict(features)))
            if len(self.buffer) < self.min_trusted_batch:
                return {"accepted": True, "buffered": len(self.buffer), "learned": False}
            agents = Counter(a for a, _ in self.buffer)
            if len(agents) < 2 and self.min_trusted_batch > 1:
                # wait for more diverse evidence; cap buffer so one agent can't flood it
                cap = self.min_trusted_batch
                self.buffer = self.buffer[-cap:]
                return {"accepted": True, "buffered": len(self.buffer), "learned": False,
                        "reason": "waiting for observations from more than one agent"}
            limit = max(1, len(self.buffer) // 2)
            per_agent: Counter[str] = Counter()
            batch = []
            for a, x in self.buffer:
                if per_agent[a] < limit:
                    per_agent[a] += 1
                    batch.append(x)
            for x in batch:
                self.model.learn_one({f: x[f] for f in FEATURE_NAMES})
            self.learned += len(batch)
            self.batches_applied += 1
            self.buffer = []
            try:
                self._save()
            except OSError:
                logger.exception("Could not persist River model")
            logger.info("River model learned a trusted batch of %d observations", len(batch))
            return {"accepted": True, "learned": True, "batch_size": len(batch)}

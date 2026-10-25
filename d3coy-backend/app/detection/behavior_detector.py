"""Batch behavioral anomaly detection with scikit-learn Isolation Forest.

Score normalization (documented prototype mapping, NOT a calibrated probability):
  raw  = -IsolationForest.score_samples(x)   (higher = more anomalous, roughly 0.3..0.8)
  q50, q99 = median and 99th percentile of raw over the normal training windows
  score = 0                                  if raw <= q50
        = 50 * (raw - q50) / (q99 - q50)     if q50 < raw <= q99
        = 50 + 50 * min(1, (raw - q99) / (q99 - q50))   above q99
So 50 means "as unusual as the top 1% of baseline behavior" and 100 means "well beyond it".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from app.detection.feature_extractor import (
    FEATURE_DESCRIPTIONS,
    FEATURE_NAMES,
    ActivityRecord,
    RoleBaseline,
    extract_features,
)
from app.schemas.common import DetectorStatus, SignalResult

logger = logging.getLogger(__name__)

MODEL_FILE = "behavior_baseline.joblib"
DETECTOR_NAME = "isolation_forest"


def normalize_anomaly(raw: float, q50: float, q99: float) -> float:
    span = max(q99 - q50, 1e-9)
    if raw <= q50:
        return 0.0
    if raw <= q99:
        return 50.0 * (raw - q50) / span
    return 50.0 + 50.0 * min(1.0, (raw - q99) / span)


def build_window(history: list[ActivityRecord], window_size: int, long_history: int
                 ) -> tuple[list[ActivityRecord], set[str]]:
    """Split chronological prior history into (window prior events, namespaces seen before the window)."""
    k = window_size - 1
    prior = history[-k:] if k > 0 else []
    older = history[-long_history:-k] if len(history) > k else []
    return prior, {r.namespace for r in older}


class BehaviorModel:
    """A fitted Isolation Forest plus everything needed to reproduce its features."""

    def __init__(self, bundle: dict[str, Any]) -> None:
        self.model: IsolationForest = bundle["model"]
        self.feature_names: list[str] = bundle["feature_names"]
        self.baselines = {r: RoleBaseline.from_dict(b) for r, b in bundle["baselines"].items()}
        self.norm = bundle["normalization"]
        self.feature_stats: dict[str, dict[str, float]] = bundle["feature_stats"]
        self.meta: dict[str, Any] = bundle.get("meta", {})
        if self.feature_names != FEATURE_NAMES:
            raise ValueError("Saved model feature schema does not match the current feature extractor; retrain.")

    def raw_score(self, features: dict[str, float]) -> float:
        x = np.array([[features[f] for f in self.feature_names]])
        return float(-self.model.score_samples(x)[0])

    def score(self, features: dict[str, float]) -> tuple[float, float]:
        raw = self.raw_score(features)
        return raw, normalize_anomaly(raw, self.norm["q50"], self.norm["q99"])

    def top_deviations(self, features: dict[str, float], limit: int = 3) -> list[dict[str, Any]]:
        """Features furthest above their baseline 95th percentile, used for explanations."""
        devs = []
        for name in self.feature_names:
            st = self.feature_stats[name]
            excess = features[name] - st["p95"]
            if excess > 0.05:
                devs.append({"feature": name, "value": round(features[name], 3), "baseline_p95": round(st["p95"], 3),
                             "baseline_mean": round(st["mean"], 3), "excess": round(excess, 3),
                             "meaning": FEATURE_DESCRIPTIONS[name]})
        return sorted(devs, key=lambda d: -d["excess"])[:limit]


class BehaviorDetector:
    """Loads the trained baseline at startup and scores live windows. Never trains per event."""

    def __init__(self, artifacts_dir: Path, window_size: int, min_history: int, long_history: int) -> None:
        self.artifacts_dir = Path(artifacts_dir)
        self.window_size = window_size
        self.min_history = min_history
        self.long_history = long_history
        self.model: BehaviorModel | None = None
        self.load_error: str | None = None

    @property
    def model_path(self) -> Path:
        return self.artifacts_dir / MODEL_FILE

    def load(self) -> bool:
        try:
            self.model = BehaviorModel(joblib.load(self.model_path))
            self.load_error = None
            logger.info("Loaded behavioral baseline from %s", self.model_path)
            return True
        except FileNotFoundError:
            self.load_error = f"No baseline model at {self.model_path}; run: python scripts/train_baseline.py"
        except Exception as exc:  # corrupt / incompatible artifact
            self.load_error = f"Could not load baseline model: {type(exc).__name__}: {exc}"
        self.model = None
        logger.warning(self.load_error)
        return False

    def status(self) -> dict[str, Any]:
        if self.model is None:
            return {"status": "unavailable", "reason": self.load_error}
        return {"status": "ready", "model": "IsolationForest", "path": str(self.model_path),
                "trained_at": self.model.meta.get("trained_at"), "training_rows": self.model.meta.get("training_rows"),
                "roles": sorted(self.model.baselines), "window_size": self.window_size,
                "min_history": self.min_history}

    def features_for(self, role: str, current: ActivityRecord, history: list[ActivityRecord]
                     ) -> tuple[dict[str, float] | None, SignalResult | None]:
        """Return (features, None) or (None, non-OK SignalResult explaining why not)."""
        if self.model is None:
            return None, SignalResult(detector=DETECTOR_NAME, status=DetectorStatus.UNAVAILABLE,
                                      explanation=self.load_error or "Behavior model not loaded.")
        baseline = self.model.baselines.get(role)
        if baseline is None:
            return None, SignalResult(detector=DETECTOR_NAME, status=DetectorStatus.UNAVAILABLE,
                                      explanation=f"No behavioral baseline exists for role '{role}'. "
                                                  "Add it to the synthetic baseline and retrain.")
        if len(history) < self.min_history:
            return None, SignalResult(
                detector=DETECTOR_NAME, status=DetectorStatus.INSUFFICIENT_DATA,
                explanation=f"Only {len(history)} prior events for this agent; at least {self.min_history} "
                            "are needed before behavior can be compared with the baseline.",
                details={"history_length": len(history), "required": self.min_history})
        prior, seen = build_window(history, self.window_size, self.long_history)
        return extract_features(current, prior, seen, baseline), None

    def evaluate(self, features: dict[str, float]) -> SignalResult:
        assert self.model is not None
        raw, score = self.model.score(features)
        score = round(score, 2)
        pct = float(np.mean(np.array(self.model.norm["reference_raw"]) <= raw) * 100)
        devs = self.model.top_deviations(features)
        if score >= 50 and devs:
            expl = "Flagged because " + "; ".join(d["meaning"] for d in devs) + "."
        elif score >= 50:
            expl = "Flagged because the combination of recent behaviors is unusual for this role."
        elif devs:
            expl = "Within the normal range overall, though " + devs[0]["meaning"] + "."
        else:
            expl = "Recent behavior is consistent with the role baseline."
        return SignalResult(
            detector=DETECTOR_NAME, status=DetectorStatus.OK, score=score, explanation=expl,
            details={"raw_anomaly": round(raw, 5), "baseline_percentile": round(pct, 1),
                     "normalization": "piecewise-linear on training quantiles (q50->0, q99->50); "
                                      "not a calibrated probability",
                     "top_deviations": devs})


def fit_isolation_forest(X: np.ndarray, seed: int) -> IsolationForest:
    return IsolationForest(n_estimators=200, max_samples=256, contamination="auto", random_state=seed).fit(X)


def save_bundle(path: Path, model: IsolationForest, baselines: dict[str, RoleBaseline], X: np.ndarray,
                meta: dict[str, Any]) -> dict[str, Any]:
    raw = -model.score_samples(X)
    q = np.quantile(raw, [0.5, 0.9, 0.99])
    rng = np.random.default_rng(0)
    reference = np.sort(rng.choice(raw, size=min(2000, len(raw)), replace=False))
    bundle = {
        "model": model,
        "feature_names": list(FEATURE_NAMES),
        "baselines": {r: b.to_dict() for r, b in baselines.items()},
        "normalization": {"q50": float(q[0]), "q90": float(q[1]), "q99": float(q[2]),
                          "reference_raw": reference.round(6).tolist()},
        "feature_stats": {f: {"mean": float(X[:, i].mean()), "p95": float(np.quantile(X[:, i], 0.95))}
                          for i, f in enumerate(FEATURE_NAMES)},
        "meta": {**meta, "trained_at": datetime.now(timezone.utc).isoformat(), "training_rows": int(len(X))},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)
    return bundle

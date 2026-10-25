from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import pytest

from app.detection.baseline_training import windows
from app.detection.behavior_detector import BehaviorDetector, normalize_anomaly
from app.detection.feature_extractor import FEATURE_NAMES, ActivityRecord, RoleBaseline, extract_features
from app.detection.online_detector import OnlineBehaviorDetector
from app.detection.synthetic_baseline import SyntheticWorld
from app.schemas.common import DetectorStatus


@pytest.fixture(scope="module")
def detector(trained_artifacts: Path) -> BehaviorDetector:
    d = BehaviorDetector(trained_artifacts, window_size=10, min_history=3, long_history=200)
    assert d.load()
    return d


def rec(tool: str, op: str, res: str, sens: str = "internal", status: str = "ALLOW") -> ActivityRecord:
    return ActivityRecord(tool, op, res, sens, status)


def test_feature_extraction_produces_all_bounded_features(detector):
    baseline = detector.model.baselines["financial_analyst"]
    prior = [rec("read_expenses", "read", "finance:expenses/2026-01"),
             rec("calculate_totals", "compute", "finance:expenses/2026-01")]
    f = extract_features(rec("generate_financial_summary", "write", "finance:reports/2026-01"), prior, set(), baseline)
    assert list(f) == FEATURE_NAMES
    assert all(0.0 <= v <= 1.0 for v in f.values())


def test_feature_values_reflect_behavior(detector):
    baseline = detector.model.baselines["financial_analyst"]
    normal = extract_features(rec("calculate_totals", "compute", "finance:expenses/2026-01"),
                              [rec("read_expenses", "read", "finance:expenses/2026-01")] * 3, set(), baseline)
    odd_prior = [rec("access_payroll", "read", "finance:payroll", "restricted", "DENY"),
                 rec("read_expenses", "read", "customers:records/1", "restricted", "DENY")]
    odd = extract_features(rec("read_vendor_payments", "read", "finance:vendor_payments/2025-01", "confidential"),
                           odd_prior, set(), baseline)
    assert odd["prior_denied_ratio"] == 1.0 and normal["prior_denied_ratio"] == 0.0
    assert odd["sensitive_ratio"] > normal["sensitive_ratio"]
    assert odd["tool_rarity"] > normal["tool_rarity"]
    assert odd["new_namespace_ratio"] > 0


def test_model_returns_valid_scores_and_separates_drift(detector):
    world = SyntheticWorld()
    rng = random.Random(777)  # different from the training seed
    role = "financial_analyst"
    base = detector.model.baselines[role]
    normal = windows(world.normal_sequence(role, rng, 120), base, 10, 3, 200)
    drift_seq = world.drift_sequence(role, rng, 20, 12)
    drift = windows(drift_seq, base, 10, 3, 200)[-6:]
    n_scores = [detector.evaluate(f).score for f in normal]
    d_scores = [detector.evaluate(f).score for f in drift]
    assert all(0 <= s <= 100 for s in n_scores + d_scores)
    assert np.mean(d_scores) > np.mean(n_scores) + 20  # actual model output, not hardcoded


def test_insufficient_history_is_explicit(detector):
    feats, problem = detector.features_for("financial_analyst", rec("read_expenses", "read", "finance:expenses/x"),
                                           [rec("read_expenses", "read", "finance:expenses/x")])
    assert feats is None
    assert problem.status == DetectorStatus.INSUFFICIENT_DATA and problem.score is None
    assert not problem.available


def test_unknown_role_and_missing_model_are_unavailable(detector, tmp_path):
    _, problem = detector.features_for("pirate", rec("x", "read", "y"), [rec("x", "read", "y")] * 5)
    assert problem.status == DetectorStatus.UNAVAILABLE
    empty = BehaviorDetector(tmp_path, 10, 3, 200)
    assert not empty.load()
    _, p2 = empty.features_for("researcher", rec("x", "read", "y"), [])
    assert p2.status == DetectorStatus.UNAVAILABLE and "train_baseline" in p2.explanation


def test_normalization_is_consistent_and_monotonic():
    q50, q99 = 0.43, 0.66
    assert normalize_anomaly(0.30, q50, q99) == 0
    assert normalize_anomaly(q50, q50, q99) == 0
    assert normalize_anomaly(q99, q50, q99) == pytest.approx(50)
    assert normalize_anomaly(5.0, q50, q99) == 100
    xs = np.linspace(0.2, 1.0, 50)
    ys = [normalize_anomaly(x, q50, q99) for x in xs]
    assert all(b >= a for a, b in zip(ys, ys[1:]))


def test_same_input_gives_same_score(detector):
    f = {k: 0.2 for k in FEATURE_NAMES}
    assert detector.evaluate(f).score == detector.evaluate(f).score


def test_role_baseline_excludes_denied_events():
    seq = [rec("a", "read", "x:y"), rec("b", "delete", "z:w", status="DENY")]
    b = RoleBaseline.fit([seq])
    assert "b" not in b.tool_probs and "delete" not in b.op_probs


# ---- River online detector: trust policy ----------------------------------------
@pytest.fixture
def river(tmp_path, trained_artifacts) -> OnlineBehaviorDetector:
    import shutil

    shutil.copy(trained_artifacts / "river_hst.pkl", tmp_path / "river_hst.pkl")
    d = OnlineBehaviorDetector(tmp_path, enabled=True, min_trusted_batch=4)
    assert d.load()
    return d


def test_river_scores_when_mature(river):
    r = river.evaluate({k: 0.1 for k in FEATURE_NAMES})
    assert river.mature and r.status == DetectorStatus.OK and 0 <= r.score <= 100


def test_river_never_learns_untrusted_observations(river):
    before = river.learned
    for _ in range(20):
        out = river.offer("agent-a", {k: 0.9 for k in FEATURE_NAMES}, trusted=False)
        assert out["accepted"] is False
    assert river.learned == before and river.buffer == []


def test_river_learns_only_in_batches_from_multiple_agents(river):
    before = river.learned
    x = {k: 0.1 for k in FEATURE_NAMES}
    for _ in range(10):  # one agent alone cannot flush a batch
        river.offer("agent-a", x, trusted=True)
    assert river.learned == before
    river.offer("agent-b", x, trusted=True)
    river.offer("agent-b", x, trusted=True)
    assert river.learned > before
    assert river.batches_applied == 1


def test_river_disabled_is_reported(tmp_path):
    d = OnlineBehaviorDetector(tmp_path, enabled=False, min_trusted_batch=5)
    assert not d.load()
    assert d.evaluate({k: 0 for k in FEATURE_NAMES}).status == DetectorStatus.DISABLED

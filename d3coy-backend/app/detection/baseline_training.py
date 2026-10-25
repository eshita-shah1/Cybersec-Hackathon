"""Training pipeline for the behavioral baseline (Isolation Forest + River Half-Space Trees)."""

from __future__ import annotations

import json
import logging
import pickle
import random
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from river import anomaly

from app.detection.behavior_detector import (
    MODEL_FILE,
    build_window,
    fit_isolation_forest,
    normalize_anomaly,
    save_bundle,
)
from app.detection.feature_extractor import FEATURE_NAMES, ActivityRecord, RoleBaseline, extract_features
from app.detection.online_detector import RIVER_FILE
from app.detection.synthetic_baseline import SyntheticWorld

logger = logging.getLogger(__name__)

REPORT_FILE = "baseline_report.json"


def windows(seq: list[ActivityRecord], baseline: RoleBaseline, window_size: int, min_history: int,
            long_history: int) -> list[dict[str, float]]:
    out = []
    for i in range(min_history, len(seq)):
        prior, seen = build_window(seq[:i], window_size, long_history)
        out.append(extract_features(seq[i], prior, seen, baseline))
    return out


def _matrix(rows: list[dict[str, float]]) -> np.ndarray:
    return np.array([[r[f] for f in FEATURE_NAMES] for r in rows], dtype=float)


def train_baseline(artifacts_dir: Path, seed: int = 42, agents_per_role: int = 6, events_per_agent: int = 300,
                   window_size: int = 10, min_history: int = 3, long_history: int = 200,
                   export_dir: Path | None = None) -> dict[str, Any]:
    """Generate synthetic normal behavior, fit both models, save artifacts and return a validation report.

    If ``export_dir`` is given, the generated data is also written there as CSV (see ``_export_csv``)."""
    artifacts_dir = Path(artifacts_dir)
    # (split, role, synthetic_agent, sequence) for optional CSV export; generation order is unchanged
    generated: list[tuple[str, str, str, list[ActivityRecord]]] = []
    world = SyntheticWorld()
    rng = random.Random(seed)

    train_seqs = {role: [world.normal_sequence(role, rng, events_per_agent) for _ in range(agents_per_role)]
                  for role in world.roles}
    baselines = {role: RoleBaseline.fit(seqs) for role, seqs in train_seqs.items()}
    for role, seqs in train_seqs.items():
        generated += [("train", role, f"{role}-train-{i:02d}", sq) for i, sq in enumerate(seqs)]
    rows = [w for role, seqs in train_seqs.items() for s in seqs
            for w in windows(s, baselines[role], window_size, min_history, long_history)]
    X = _matrix(rows)

    model = fit_isolation_forest(X, seed)
    bundle = save_bundle(artifacts_dir / MODEL_FILE, model, baselines, X,
                         {"seed": seed, "window_size": window_size, "min_history": min_history,
                          "agents_per_role": agents_per_role, "events_per_agent": events_per_agent})
    q50, q99 = bundle["normalization"]["q50"], bundle["normalization"]["q99"]

    # ---- River Half-Space Trees, pre-trained on the same normal rows ----
    hst = anomaly.HalfSpaceTrees(n_trees=25, height=8, window_size=250, seed=seed)
    order = np.random.default_rng(seed).permutation(len(rows))
    for i in order:
        hst.learn_one(rows[i])
    river_raw = np.array([hst.score_one(rows[i]) for i in order[:2000]])
    rq50, rq99 = float(np.quantile(river_raw, 0.5)), float(np.quantile(river_raw, 0.99))
    with (artifacts_dir / RIVER_FILE).open("wb") as fh:
        pickle.dump({"model": hst, "normalization": {"q50": rq50, "q99": rq99}, "learned": len(rows),
                     "window_size": 250, "batches_applied": 0}, fh)

    # ---- validation on separately generated data (different seeds) ----
    vrng = random.Random(seed + 1000)

    def if_scores(rows_: list[dict[str, float]]) -> np.ndarray:
        raw = -model.score_samples(_matrix(rows_))
        return np.array([normalize_anomaly(r, q50, q99) for r in raw])

    holdout: list[dict[str, float]] = []
    for role in world.roles:
        for i in range(2):
            sq = world.normal_sequence(role, vrng, 200)
            generated.append(("holdout_normal", role, f"{role}-holdout-{i:02d}", sq))
            holdout += windows(sq, baselines[role], window_size, min_history, long_history)
    hs = if_scores(holdout)
    drng = random.Random(seed + 2000)
    drift_by_step: dict[int, list[float]] = {}
    prefix, drift_len = 20, 12
    for role in world.roles:
        for i in range(20):
            seq = world.drift_sequence(role, drng, prefix, drift_len)
            generated.append(("drift", role, f"{role}-drift-{i:02d}", seq))
            ws = windows(seq, baselines[role], window_size, min_history, long_history)
            sc = if_scores(ws)
            for k in range(drift_len):
                drift_by_step.setdefault(k + 1, []).append(float(sc[prefix - min_history + k]))

    report = {
        "seed": seed,
        "training_windows": len(rows),
        "features": FEATURE_NAMES,
        "isolation_forest": {"raw_q50": q50, "raw_q99": q99},
        "feature_summary": pd.DataFrame(rows, columns=FEATURE_NAMES).describe()
                             .loc[["mean", "50%", "max"]].round(3).to_dict(),
        "river": {"raw_q50": rq50, "raw_q99": rq99, "observations_learned": len(rows)},
        "validation_note": "Synthetic data only. These numbers describe the prototype on its own simulated "
                           "world and are NOT real-world detection accuracy.",
        "holdout_normal": {"windows": len(holdout), "mean_score": round(float(hs.mean()), 2),
                           "share_score_ge_50": round(float((hs >= 50).mean()), 4),
                           "share_score_ge_75": round(float((hs >= 75).mean()), 4)},
        "synthetic_drift": {
            str(k): {"mean_score": round(float(np.mean(v)), 2), "share_ge_50": round(float(np.mean(np.array(v) >= 50)), 3)}
            for k, v in sorted(drift_by_step.items())
        },
    }
    (artifacts_dir / REPORT_FILE).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if export_dir is not None:
        report["exported_files"] = _export_csv(Path(export_dir), generated, baselines, model, q50, q99,
                                               window_size, min_history, long_history, drift_start=prefix)
    return report


def _export_csv(out_dir: Path, generated: list[tuple[str, str, str, list[ActivityRecord]]],
                baselines: dict[str, RoleBaseline], model: Any, q50: float, q99: float, window_size: int,
                min_history: int, long_history: int, drift_start: int) -> dict[str, str]:
    """Write the synthetic data as two CSV files.

    synthetic_events.csv    one row per generated action (train, holdout_normal and drift sequences)
    synthetic_features.csv  one row per scored window: the 14 model features plus the Isolation
                            Forest raw and normalized (0-100) score. Rows with split=train are
                            exactly what the model was trained on.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    events, feats = [], []
    for split, role, agent, seq in generated:
        for i, r in enumerate(seq):
            phase = "drift" if split == "drift" and i >= drift_start else "normal"
            events.append({"split": split, "role": role, "synthetic_agent": agent, "position": i,
                           "phase": phase, "tool_name": r.tool_name, "operation": r.operation,
                           "resource": r.resource, "sensitivity": r.sensitivity, "policy_status": r.policy_status})
        rows = windows(seq, baselines[role], window_size, min_history, long_history)
        if not rows:
            continue
        raw = -model.score_samples(_matrix(rows))
        for k, (row, rw) in enumerate(zip(rows, raw)):
            i = k + min_history
            phase = "drift" if split == "drift" and i >= drift_start else "normal"
            feats.append({"split": split, "role": role, "synthetic_agent": agent, "position": i, "phase": phase,
                          "tool_name": seq[i].tool_name, "resource": seq[i].resource, **row,
                          "if_raw_anomaly": round(float(rw), 6),
                          "if_score_0_100": round(normalize_anomaly(float(rw), q50, q99), 2)})
    paths = {"events": out_dir / "synthetic_events.csv", "features": out_dir / "synthetic_features.csv"}
    pd.DataFrame(events).to_csv(paths["events"], index=False)
    pd.DataFrame(feats).to_csv(paths["features"], index=False)
    return {k: str(v.resolve()) for k, v in paths.items()}

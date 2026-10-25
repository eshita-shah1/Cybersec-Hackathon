"""Train the behavioral baseline (Isolation Forest + River) on reproducible synthetic data.

Usage (from the d3coy-backend directory):
    python scripts/train_baseline.py [--seed 42] [--export-csv [DIR]]

--export-csv writes the generated synthetic data as CSV (default folder: data/synthetic).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.detection.baseline_training import train_baseline  # noqa: E402


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--artifacts", default=settings.artifacts_dir)
    parser.add_argument("--export-csv", nargs="?", const="data/synthetic", default=None, metavar="DIR",
                        help="also export the synthetic events and feature windows as CSV")
    args = parser.parse_args()

    print(f"Training behavioral baseline (seed={args.seed}) into {args.artifacts} ...")
    report = train_baseline(Path(args.artifacts), seed=args.seed, window_size=settings.behavior_window_size,
                            min_history=settings.behavior_min_history, long_history=settings.behavior_long_history,
                            export_dir=Path(args.export_csv) if args.export_csv else None)
    print(f"Training windows: {report['training_windows']}  features: {len(report['features'])}")
    print("Isolation Forest raw-score quantiles:", json.dumps(report["isolation_forest"]))
    h = report["holdout_normal"]
    print(f"Held-out NORMAL windows: {h['windows']}  mean score {h['mean_score']}  "
          f"share >=50: {h['share_score_ge_50']:.2%}  share >=75: {h['share_score_ge_75']:.2%}")
    print("Synthetic DRIFT (score by number of drift steps into the sequence):")
    for step, v in report["synthetic_drift"].items():
        print(f"  step {step:>2}: mean {v['mean_score']:6.2f}   share >=50: {v['share_ge_50']:.0%}")
    print(report["validation_note"])
    print(f"Artifacts written to {Path(args.artifacts).resolve()}")
    for kind, path in report.get("exported_files", {}).items():
        print(f"CSV ({kind}): {path}")


if __name__ == "__main__":
    main()

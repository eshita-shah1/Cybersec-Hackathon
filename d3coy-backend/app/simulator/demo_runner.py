"""Command-line demo runner.

    python -m app.simulator.demo_runner --list
    python -m app.simulator.demo_runner normal_research            # in-process, uses DATABASE_URL
    python -m app.simulator.demo_runner all
    python -m app.simulator.demo_runner all --api http://127.0.0.1:8000   # via the running server
                                                                          # (WebSocket clients see events)

In-process mode builds the same container as the server and calls the same EventService.
API mode calls POST /api/scenarios/{id}/run and needs ADMIN_API_KEY in the environment/.env.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.config import get_settings
from app.simulator.scenarios import SCENARIOS


def _print_result(result: dict[str, Any]) -> None:
    print(f"\n=== {result['title']}  (scenario={result['scenario_id']}, run={result['run_id']}) ===")
    print(f"Agent: {result['agent_id']}")
    print(f"Expected: {result['expected_outcome']}")
    for p in result.get("preconditions", []):
        print(f"Precondition: {p}")
    print(f"{'#':>2} {'tool':<26} {'resource':<34} {'policy':<10} {'B':>6} {'S':>6} {'risk':>6} "
          f"{'decision':<8} {'execution':<16} ok")
    for s in result["steps"]:
        b = "-" if s["behavior_score"] is None else f"{s['behavior_score']:.1f}"
        sem = "-" if s["semantic_score"] is None else f"{s['semantic_score']:.1f}"
        print(f"{s['step']:>2} {s['tool_name']:<26} {s['target_resource'][:34]:<34} {s['policy_status']:<10} "
              f"{b:>6} {sem:>6} {s['risk_score']:>6.1f} {s['decision']:<8} {s['execution_status']:<16} "
              f"{'yes' if s['matches_expectation'] else 'NO'}")
        if s.get("escalation"):
            print(f"   ESCALATION: {s['escalation']['reason']}")
        if s.get("review_id"):
            print(f"   held for review: review_id={s['review_id']}")
    print(f"Decisions: {result['decision_counts']}")
    if result["all_expectations_met"]:
        print("All steps matched their designed expectation.")
    else:
        print(f"Steps that did NOT match the designed expectation: {result['mismatched_steps']} "
              "(reported as-is; detector outputs are not forced)")


def run_in_process(ids: list[str]) -> list[dict[str, Any]]:
    import logging

    from app.services.container import build_container

    logging.basicConfig(level=logging.WARNING)
    container = build_container(get_settings())
    container.startup()
    return [container.simulator.run(i, actor="cli") for i in ids]


def run_via_api(ids: list[str], base: str) -> list[dict[str, Any]]:
    import httpx

    key = get_settings().admin_api_key
    if key is None:
        raise SystemExit("ADMIN_API_KEY must be set to run scenarios through the API")
    out = []
    with httpx.Client(base_url=base, timeout=120) as client:
        for i in ids:
            r = client.post(f"/api/scenarios/{i}/run", headers={"X-API-Key": key.get_secret_value()})
            if r.status_code != 200:
                raise SystemExit(f"Scenario {i} failed: HTTP {r.status_code} {r.text}")
            out.append(r.json())
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Run D3COY demonstration scenarios")
    parser.add_argument("scenario", nargs="?", help="scenario id or 'all'")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--api", help="base URL of a running server, e.g. http://127.0.0.1:8000")
    parser.add_argument("--json", action="store_true", help="print raw JSON results")
    args = parser.parse_args()

    if args.list or not args.scenario:
        for s in SCENARIOS.values():
            print(f"{s.scenario_id:<22} {s.title}\n{'':<22} {s.description}")
        return
    ids = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    unknown = [i for i in ids if i not in SCENARIOS]
    if unknown:
        sys.exit(f"Unknown scenario(s): {unknown}. Use --list.")
    results = run_via_api(ids, args.api) if args.api else run_in_process(ids)
    for r in results:
        print(json.dumps(r, indent=2)) if args.json else _print_result(r)


if __name__ == "__main__":
    main()

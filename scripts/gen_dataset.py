"""Generate the benchmark dataset deterministically.

Outputs:
  data/tasks.json   - synthetic, verifiable tasks across the skill taxonomy.
  data/agents.json  - agent pool with 4 personas per skill.

The pool/calibration lives in ccrlib.scenario (so the sensitivity sweep and the
cloud harness share one source of truth). Defaults here = the headline calibration
(claim_gap = 1.0: liars/stale agents fully over-claim).

Usage:
  python scripts/gen_dataset.py [--tasks N] [--claim-gap G]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ccrlib import scenario  # noqa: E402


def generate_agents(claim_gap: float = 1.0) -> list[dict]:
    return scenario.build_agents(claim_gap=claim_gap)


def generate_tasks(n: int) -> list[dict]:
    return scenario.build_tasks(n)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=int, default=48)
    ap.add_argument("--claim-gap", type=float, default=1.0)
    args = ap.parse_args()

    data_dir = os.path.join(ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)
    agents = generate_agents(args.claim_gap)
    tasks = generate_tasks(args.tasks)
    json.dump(agents, open(os.path.join(data_dir, "agents.json"), "w"), indent=2)
    json.dump(tasks, open(os.path.join(data_dir, "tasks.json"), "w"), indent=2)
    print(f"Wrote {len(agents)} agents (claim_gap={args.claim_gap}) and {len(tasks)} tasks to {data_dir}")


if __name__ == "__main__":
    main()

"""Binding-budget sweep (ICITS camera-ready, reviewer 1): CCR when only a few probes are affordable.

The default factorial uses B = 3 + 4*kappa, which never binds CCR's probing. Here B is swept
from one real execution upwards in three cells (mixed pool, indistinguishable canary).
CCR reserves one real execution before probing (ccrlib/policies.py), so a tight budget buys
fewer canaries instead of none of the task. Budgets are compared exactly (exact_budget=True):
with plain float comparisons a budget such as 1 + 4*0.05 falls just short of the execution it
was sized for, which would measure rounding rather than scarcity.

Usage: python scripts/budget_sweep.py [--seeds 6] [--tasks 24]
Writes analysis/budget_sweep.csv and prints a table.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ccrlib import experiment  # noqa: E402
from ccrlib.scoring import Params  # noqa: E402

import gen_dataset  # noqa: E402
import local_run  # noqa: E402

CELLS = [(5.0, 0.2), (10.0, 0.05), (2.0, 0.05)]
POLICIES = ["ccr", "optimistic_fallback", "embedding", "attestation_only", "random"]


def budgets(kappa: float) -> list[tuple[str, float]]:
    """(label, B): probes x kappa + real executions x 1."""
    return [("1 exec", 1.0), ("1 probe + 1 exec", 1.0 + kappa), ("2 probes + 1 exec", 1.0 + 2 * kappa),
            ("4 probes + 1 exec", 1.0 + 4 * kappa), ("2 execs", 2.0), ("4 probes + 2 execs", 2.0 + 4 * kappa),
            ("default 3 + 4k", 3.0 + 4 * kappa)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--tasks", type=int, default=24)
    args = ap.parse_args()

    specs = local_run.pool_specs(gen_dataset.generate_agents(), "mixed")
    tasks = gen_dataset.generate_tasks(args.tasks)
    out = []
    for V, kappa in CELLS:
        for label, B in budgets(kappa):
            cond = {"name": f"V{int(V)}_k{kappa}_B{B:.2f}", "specs": specs, "indistinguishable": True,
                    "params": Params(value=V, canary_cost=kappa, budget=B, exact_budget=True)}
            rows, _ = experiment.run(tasks, [cond], local_run.build_env, list(range(args.seeds)), POLICIES)
            for r in rows:
                out.append({"V": V, "kappa": kappa, "budget": round(B, 2), "affords": label,
                            "policy": r["policy"], "success": r["success_rate"],
                            "incident": r["incident_rate"], "net_utility": r["mean_net_utility"],
                            "canaries": r["mean_canaries"]})

    path = os.path.join(ROOT, "analysis", "budget_sweep.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    for V, kappa in CELLS:
        print(f"\nV={V:g} kappa={kappa:g}   success / incident / net utility / canaries")
        for label, B in budgets(kappa):
            cells = [o for o in out if o["V"] == V and o["kappa"] == kappa and o["budget"] == round(B, 2)]
            print(f"  B={B:4.2f} ({label:>18}): " + "  ".join(
                f"{o['policy'][:6]} {o['success']:.3f}/{o['incident']:.3f}/{o['net_utility']:.2f}/{o['canaries']:.0f}"
                for o in cells))
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()

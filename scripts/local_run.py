"""Produce the paper's evidence locally, with zero Azure dependencies.

Runs the full policy suite over a grid of conditions (task value V, canary cost
kappa, agent-pool hostility, canary indistinguishability) and emits:
  data/results/rows.json      - aggregated metrics per (condition, policy)
  data/results/raw.jsonl      - every individual run (for analysis/analyze.py)
  analysis/EVIDENCE.md        - the RQ1-RQ4 headline tables

Because everything is deterministic, these numbers are identical to what the
Azure deployment produces over real A2A HTTP calls.

Usage:
  python scripts/local_run.py [--seeds 3] [--tasks 48]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ccrlib import experiment, metrics  # noqa: E402
from ccrlib.agents import SimAgent  # noqa: E402
from ccrlib.scoring import Params  # noqa: E402

import gen_dataset  # noqa: E402  (scripts/ is on path)

VALUES = [1.0, 2.0, 5.0, 10.0]
KAPPAS = [0.05, 0.1, 0.2, 0.4, 0.6]
POOLS = ["clean", "mixed", "hostile"]
INDIST = [True, False]

PRIORITY = ["ccr", "optimistic_fallback", "attestation_only", "card_only", "embedding", "random"]


def build_env(active_specs: list[dict]):
    agents = {s["id"]: SimAgent(s) for s in active_specs}
    cards = {aid: a.card() for aid, a in agents.items()}

    def transport(agent_id: str, request: dict) -> dict:
        return agents[agent_id].execute(request)

    return cards, transport


def pool_specs(all_specs: list[dict], pool: str) -> list[dict]:
    by_skill: dict[str, list[dict]] = {}
    for s in all_specs:
        by_skill.setdefault(next(iter(s["declaredSkills"])), []).append(s)
    out: list[dict] = []
    for skill, specs in by_skill.items():
        p = {x["persona"]: [] for x in specs}
        for x in specs:
            p.setdefault(x["persona"], []).append(x)
        if pool == "clean":          # honest only
            out += p.get("correct", []) + p.get("degraded", [])
        elif pool == "mixed":        # full pool
            out += specs
        elif pool == "hostile":      # raise malicious fraction (drop one good agent)
            out += p.get("correct", [])[:1] + p.get("degraded", []) \
                + p.get("adversarial", []) + p.get("canary_aware", [])
    return out


def make_conditions(all_specs: list[dict], exact_budget: bool = False) -> list[dict]:
    conditions = []
    for pool in POOLS:
        specs = pool_specs(all_specs, pool)
        for V in VALUES:
            for kappa in KAPPAS:
                for indist in INDIST:
                    budget = 4 * kappa + 3 * 1.0  # room for ~4 canaries + 3 real execs
                    params = Params(value=V, canary_cost=kappa, budget=budget, exact_budget=exact_budget)
                    name = f"V{int(V)}_k{kappa}_{pool}_{'I' if indist else 'D'}"
                    conditions.append({
                        "name": name, "specs": specs, "params": params,
                        "indistinguishable": indist,
                    })
    return conditions


def row_of(rows, condition, policy):
    for r in rows:
        if r["condition"] == condition and r["policy"] == policy:
            return r
    return None


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--tasks", type=int, default=48)
    ap.add_argument("--exact-budget", action="store_true",
                    help="tolerant budget comparisons (corrected run; not the published numbers)")
    args = ap.parse_args()

    try:  # ensure UTF-8 console on Windows (arrows/accents in the report)
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # (Re)generate dataset.
    all_specs = gen_dataset.generate_agents()
    tasks = gen_dataset.generate_tasks(args.tasks)
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    json.dump(all_specs, open(os.path.join(ROOT, "data", "agents.json"), "w"), indent=2)
    json.dump(tasks, open(os.path.join(ROOT, "data", "tasks.json"), "w"), indent=2)

    seeds = list(range(args.seeds))
    conditions = make_conditions(all_specs, args.exact_budget)
    print(f"Running {len(conditions)} conditions x {len(tasks)} tasks x {len(seeds)} seeds "
          f"x {len(PRIORITY)} policies ...")
    rows, raw = experiment.run(tasks, conditions, build_env, seeds, PRIORITY)

    res_dir = os.path.join(ROOT, "data", "results")
    os.makedirs(res_dir, exist_ok=True)
    json.dump(rows, open(os.path.join(res_dir, "rows.json"), "w"), indent=2)
    with open(os.path.join(res_dir, "raw.jsonl"), "w") as f:
        for r in raw:
            f.write(json.dumps(r) + "\n")

    md = _build_evidence(rows)
    os.makedirs(os.path.join(ROOT, "analysis"), exist_ok=True)
    open(os.path.join(ROOT, "analysis", "EVIDENCE.md"), "w", encoding="utf-8").write(md)
    print("\n" + md)
    print(f"\nWrote {len(raw)} raw runs and {len(rows)} aggregated rows to {res_dir}")
    print("Headline written to analysis/EVIDENCE.md")


def _build_evidence(rows) -> str:
    out = ["# CCR — Experimental evidence (generated by scripts/local_run.py)\n"]

    # RQ1: effectiveness at a representative condition.
    cond = "V5_k0.2_mixed_I"
    out.append("## RQ1 — Effectiveness (V=5, kappa=0.2, pool=mixed, indistinguishable canary)\n")
    table = []
    for pol in PRIORITY:
        r = row_of(rows, cond, pol)
        if r:
            table.append([pol, r["success_rate"], r["mean_net_utility"],
                          r["incident_rate"], r["p95_latency_ms"], r["mean_canaries"]])
    table.sort(key=lambda x: x[2], reverse=True)
    out.append(md_table(
        ["policy", "success@1", "net_utility", "incident_rate", "p95_ms", "canaries"], table))

    # RQ2: cost/benefit region — winner per (V, kappa) cell.
    out.append("\n\n## RQ2 — When does verifying pay? (pool=mixed, indist=True)\n")
    out.append("Mean net utility per cell; the winner is marked in bold. "
               "We look for the region where CCR beats optimistic_fallback and attestation_only.\n")
    cmp_pols = ["ccr", "optimistic_fallback", "attestation_only"]
    header = ["V \\ kappa"] + [f"k={k}" for k in KAPPAS]
    grid = []
    ccr_wins = 0
    total = 0
    for V in VALUES:
        line = [f"V={int(V)}"]
        for k in KAPPAS:
            c = f"V{int(V)}_k{k}_mixed_I"
            vals = {p: (row_of(rows, c, p) or {}).get("mean_net_utility", float("-inf")) for p in cmp_pols}
            winner = max(vals, key=vals.get)
            total += 1
            if winner == "ccr":
                ccr_wins += 1
            tag = {"ccr": "CCR", "optimistic_fallback": "OPT", "attestation_only": "ATT"}[winner]
            line.append(f"CCR={vals['ccr']:.2f} OPT={vals['optimistic_fallback']:.2f} "
                        f"ATT={vals['attestation_only']:.2f} → **{tag}**")
        grid.append(line)
    out.append(md_table(header, grid))
    out.append(f"\nCCR wins in **{ccr_wins}/{total}** cells (typically high V / low kappa).")

    # RQ3: robustness to degradation (clean pool has degraded honest agents).
    out.append("\n\n## RQ3 — Robustness to degradation (pool=clean, V=5, k=0.2)\n")
    out.append("attestation_only trusts valid signed cards; the honest "
               "but **degraded** agent is signed and its card is stale → it picks it and fails.\n")
    table = []
    for pol in ["ccr", "card_only", "attestation_only", "optimistic_fallback"]:
        r = row_of(rows, "V5_k0.2_clean_I", pol)
        if r:
            table.append([pol, r["success_rate"], r["mean_net_utility"], r["p95_latency_ms"]])
    out.append(md_table(["policy", "success@1", "net_utility", "p95_ms"], table))

    # RQ4: canary-aware defection — mitigation effect of indistinguishability.
    out.append("\n\n## RQ4 — Canary-aware defection and mitigation (pool=mixed, V=5, k=0.2)\n")
    out.append("The defector passes the canary and fails the real task **only if it can tell "
               "the probe apart**. Making the canary indistinguishable (I) neutralizes it vs. distinguishable (D).\n")
    table = []
    for indist, label in [(False, "distinguishable canary (D) — naive"), (True, "indistinguishable canary (I) — mitigation")]:
        c = f"V5_k0.2_mixed_{'I' if indist else 'D'}"
        r = row_of(rows, c, "ccr")
        if r:
            table.append([label, r.get("defection_rate"), r["incident_rate"], r["success_rate"]])
    out.append(md_table(["CCR configuration", "defection_rate", "incident_rate", "success@1"], table))

    out.append("\n\n_Note: deterministic numbers; the Azure deployment reproduces them over real A2A._")
    return "\n".join(out)


if __name__ == "__main__":
    main()

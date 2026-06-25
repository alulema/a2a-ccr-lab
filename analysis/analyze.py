"""Post-process the raw experiment runs into paper-ready CSVs.

Reads data/results/raw.json (cloud blob, a JSON array) or data/results/raw.jsonl
(local JSON Lines) — whichever is newer — and emits timestamped artifacts so each
analysis run is dated and comparable:
  analysis/summary_YYYYMMDDHHMM.csv         - one row per (condition, policy), all metrics
  analysis/rq2_costbenefit_YYYYMMDDHHMM.csv - net utility of CCR vs baselines across V x kappa

Stdlib only (no pandas), so it runs anywhere. For plots, pip install the optional
analysis/requirements.txt and extend this script.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
from collections import defaultdict
from datetime import datetime
from statistics import mean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "data", "results")


def load_raw():
    # Accept either the cloud blob (raw.json: one JSON array, as written by the router)
    # or local JSONL (raw.jsonl: one record per line). Prefer whichever is newer, so you
    # can `az storage blob download ... raw.json` and run this directly — no jq step.
    candidates = [os.path.join(RESULTS_DIR, n) for n in ("raw.json", "raw.jsonl")]
    present = [p for p in candidates if os.path.exists(p)]
    if not present:
        raise SystemExit(f"No results found. Expected one of: {candidates}")
    path = max(present, key=os.path.getmtime)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.lstrip().startswith("["):
        return json.loads(text)  # JSON array (cloud raw.json)
    return [json.loads(line) for line in text.splitlines() if line.strip()]  # JSONL


def summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["condition"], r["policy"])].append(r)
    out = []
    for (cond, pol), recs in sorted(groups.items()):
        out.append({
            "condition": cond,
            "policy": pol,
            "n": len(recs),
            "success_rate": round(mean(r["success"] for r in recs), 4),
            "mean_net_utility": round(mean(r["net_utility"] for r in recs), 4),
            "mean_total_cost": round(mean(r["total_cost"] for r in recs), 4),
            "incident_rate": round(mean(r["incident"] for r in recs), 4),
            "defection_rate": round(mean(r["defection"] for r in recs), 4),
            "mean_canaries": round(mean(r["n_canaries"] for r in recs), 3),
        })
    return out


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in fields})
    print(f"Wrote {path} ({len(rows)} rows)")


_COND_RE = re.compile(r"^V(\d+)_k([\d.]+)_(clean|hostile|mixed)_(I|D)$")


def pareto(summary):
    """Per condition, flag Pareto-optimal policies over (success_rate up, incident_rate
    down, mean_total_cost down). Parameter-FREE: it shows which policies are non-dominated
    without picking a weight between success, risk, and cost — that trade-off is exactly
    what lambda_r prices (see analysis/lambda_r_sweep.py). The honest reading of the caveat
    cells: CCR and optimistic_fallback are BOTH on the frontier (CCR buys fewer incidents
    with canary cost; optimistic buys cheapness with incidents), so choosing between them
    REQUIRES pricing an incident — it is not a free win for either."""
    by_cond = defaultdict(list)
    for s in summary:
        by_cond[s["condition"]].append(s)
    out = []
    for cond, recs in sorted(by_cond.items()):
        for a in recs:
            doms = []
            for b in recs:
                if b["policy"] == a["policy"]:
                    continue
                ge = (b["success_rate"] >= a["success_rate"]
                      and b["incident_rate"] <= a["incident_rate"]
                      and b["mean_total_cost"] <= a["mean_total_cost"])
                strict = (b["success_rate"] > a["success_rate"]
                          or b["incident_rate"] < a["incident_rate"]
                          or b["mean_total_cost"] < a["mean_total_cost"])
                if ge and strict:
                    doms.append(b["policy"])
            out.append({
                "condition": cond, "policy": a["policy"],
                "success_rate": a["success_rate"], "incident_rate": a["incident_rate"],
                "mean_total_cost": a["mean_total_cost"],
                "pareto_optimal": int(not doms),
                "dominated_by": ";".join(sorted(doms)),
            })
    return out


def rq2_costbenefit(summary):
    by = {(s["condition"], s["policy"]): s for s in summary}
    pols = ["ccr", "optimistic_fallback", "attestation_only", "card_only", "embedding"]
    # Discover the (V, kappa) grid from the mixed_I condition names actually present.
    pat = re.compile(r"^V(\d+)_k([\d.]+)_mixed_I$")
    cells = sorted({
        (int(m.group(1)), float(m.group(2)))
        for s in summary if (m := pat.match(s["condition"]))
    })
    rows = []
    for V, k in cells:
        cond = f"V{V}_k{k}_mixed_I"
        row = {"V": V, "kappa": k}
        best, best_u = None, float("-inf")
        for p in pols:
            u = by.get((cond, p), {}).get("mean_net_utility")
            row[p] = u
            if u is not None and u > best_u:
                best, best_u = p, u
        row["winner"] = best
        rows.append(row)
    return rows


# --- Uncertainty (optional, behind --ci) --------------------------------------
# The n=144 runs per cell are 24 tasks x 6 seeds over a FIXED agent pool — NOT 144
# independent draws. We therefore CLUSTER-bootstrap by task (resample the 24 tasks with
# replacement, keeping each task's seeds together), so the interval reflects task-to-task
# heterogeneity, the dominant and most defensible source of variation. The CI is "within
# this benchmark"; it does NOT speak to generalization across different pools/task sets.

def _percentile_ci(samples, alpha=0.05):
    samples.sort()
    b = len(samples)
    return (round(samples[int(alpha / 2 * b)], 4),
            round(samples[min(b - 1, int((1 - alpha / 2) * b))], 4))


def _clusters(records, valfn):
    g = defaultdict(list)
    for r in records:
        g[r["task"]].append(valfn(r))
    return list(g.values())


def _boot_mean_ci(clusters, rng, n_boot):
    if not clusters:
        return (None, None)
    k = len(clusters)
    out = []
    for _ in range(n_boot):
        flat = []
        for _ in range(k):
            flat.extend(clusters[rng.randrange(k)])
        out.append(mean(flat))
    return _percentile_ci(out)


def confidence(rows, analysis_dir, stamp, n_boot=1000):
    rng = random.Random(20260624)
    by_cell = defaultdict(list)
    for r in rows:
        by_cell[(r["condition"], r["policy"])].append(r)

    # (a) Per-policy 95% CIs for the representative RQ1 cell.
    rep = "V5_k0.2_mixed_I"
    print(f"\n=== 95% CIs (cluster bootstrap by task, B={n_boot}) — {rep} ===")
    print(f"{'policy':20} {'success':>16} {'net_utility':>18} {'incident':>16}")
    for pol in ["ccr", "optimistic_fallback", "attestation_only", "card_only",
                "embedding", "random"]:
        recs = by_cell.get((rep, pol))
        if not recs:
            continue
        s = _boot_mean_ci(_clusters(recs, lambda r: 1.0 if r["success"] else 0.0), rng, n_boot)
        u = _boot_mean_ci(_clusters(recs, lambda r: r["net_utility"]), rng, n_boot)
        i = _boot_mean_ci(_clusters(recs, lambda r: 1.0 if r["incident"] else 0.0), rng, n_boot)
        print(f"{pol:20} [{s[0]:.3f},{s[1]:.3f}] [{u[0]:.3f},{u[1]:.3f}] [{i[0]:.3f},{i[1]:.3f}]")

    # (b) Paired significance of CCR's net-utility advantage over the best baseline,
    # per condition (paired by task — the right test for "is the advantage real?").
    base_set = ["optimistic_fallback", "attestation_only", "card_only", "embedding"]
    out = []
    for c in sorted({cc for (cc, _) in by_cell}):
        ccr = by_cell.get((c, "ccr"))
        if not ccr:
            continue
        bb, bbv = None, None
        for b in base_set:
            recs = by_cell.get((c, b))
            if not recs:
                continue
            m = mean(r["net_utility"] for r in recs)
            if bbv is None or m > bbv:
                bb, bbv = b, m
        if bb is None:
            continue
        ccr_by, base_by = defaultdict(list), defaultdict(list)
        for r in ccr:
            ccr_by[r["task"]].append(r["net_utility"])
        for r in by_cell[(c, bb)]:
            base_by[r["task"]].append(r["net_utility"])
        tasks = sorted(set(ccr_by) & set(base_by))
        diffs = []
        for _ in range(n_boot):
            cf, bf = [], []
            for _ in range(len(tasks)):
                t = tasks[rng.randrange(len(tasks))]
                cf.extend(ccr_by[t])
                bf.extend(base_by[t])
            diffs.append(mean(cf) - mean(bf))
        lo, hi = _percentile_ci(diffs)
        out.append({"condition": c, "best_baseline": bb,
                    "ccr_minus_best": round(mean(r["net_utility"] for r in ccr) - bbv, 4),
                    "ci_lo": lo, "ci_hi": hi, "significant": int(lo > 0 or hi < 0)})
    write_csv(os.path.join(analysis_dir, f"significance_{stamp}.csv"), out,
              ["condition", "best_baseline", "ccr_minus_best", "ci_lo", "ci_hi", "significant"])

    foc = [o for o in out if (m := _COND_RE.match(o["condition"]))
           and m.group(4) == "I" and m.group(3) in ("hostile", "mixed")
           and int(m.group(1)) >= 5 and float(m.group(2)) <= 0.2]
    sig_pos = sum(1 for o in foc if o["significant"] and o["ccr_minus_best"] > 0)
    print(f"\nPaired significance (high-stakes indistinguishable hostile/mixed, {len(foc)} cells): "
          f"CCR's net-utility advantage is significant & positive in {sig_pos}/{len(foc)}.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ci", action="store_true",
                    help="also compute bootstrap CIs + paired CCR-vs-baseline significance")
    ap.add_argument("--boot", type=int, default=1000, help="bootstrap resamples")
    args = ap.parse_args()
    # Stamp outputs with the analysis launch time (local) so each run is a dated,
    # comparable artifact: analysis/summary_YYYYMMDDHHMM.csv, etc. One stamp per run so
    # both CSVs from the same invocation share it.
    stamp = datetime.now().strftime("%Y%m%d%H%M")
    analysis_dir = os.path.join(ROOT, "analysis")
    rows = load_raw()
    summary = summarize(rows)
    write_csv(os.path.join(analysis_dir, f"summary_{stamp}.csv"), summary,
              ["condition", "policy", "n", "success_rate", "mean_net_utility",
               "mean_total_cost", "incident_rate", "defection_rate", "mean_canaries"])
    cb = rq2_costbenefit(summary)
    write_csv(os.path.join(analysis_dir, f"rq2_costbenefit_{stamp}.csv"), cb,
              ["V", "kappa", "ccr", "optimistic_fallback", "attestation_only", "card_only",
               "embedding", "winner"])
    ccr_wins = sum(1 for r in cb if r["winner"] == "ccr")
    print(f"RQ2: CCR is the net-utility winner in {ccr_wins}/{len(cb)} (V, kappa) cells.")

    # Parameter-free Pareto view (success up, incident down, cost down).
    par = pareto(summary)
    write_csv(os.path.join(analysis_dir, f"pareto_{stamp}.csv"), par,
              ["condition", "policy", "success_rate", "incident_rate", "mean_total_cost",
               "pareto_optimal", "dominated_by"])
    counts = defaultdict(lambda: [0, 0])      # policy -> [pareto_optimal_cells, total]
    front = defaultdict(set)                   # condition -> {policies on frontier}
    for r in par:
        counts[r["policy"]][1] += 1
        if r["pareto_optimal"]:
            counts[r["policy"]][0] += 1
            front[r["condition"]].add(r["policy"])
    ihm = [c for c in front if (m := _COND_RE.match(c))
           and m.group(4) == "I" and m.group(3) in ("hostile", "mixed")]
    ccr_front = sum(1 for c in ihm if "ccr" in front[c])
    both = sum(1 for c in ihm if {"ccr", "optimistic_fallback"} <= front[c])
    print(f"Pareto: CCR is non-dominated in {ccr_front}/{len(ihm)} indistinguishable "
          f"hostile/mixed cells; CCR & optimistic_fallback are BOTH on the frontier in "
          f"{both} of them (incomparable without pricing incidents -> lambda_r sweep).")
    for p in sorted(counts, key=lambda p: -counts[p][0]):
        print(f"  {p:20} Pareto-optimal in {counts[p][0]:3}/{counts[p][1]} cells")

    if args.ci:
        confidence(rows, analysis_dir, stamp, n_boot=args.boot)


if __name__ == "__main__":
    main()

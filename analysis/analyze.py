"""Post-process the raw experiment runs into paper-ready CSVs.

Reads data/results/raw.jsonl and emits:
  analysis/summary.csv        - one row per (condition, policy) with all metrics
  analysis/rq2_costbenefit.csv- net utility of CCR vs baselines across V x kappa

Stdlib only (no pandas), so it runs anywhere. For plots, pip install the optional
analysis/requirements.txt and extend this script.
"""
from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
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


def rq2_costbenefit(summary):
    by = {(s["condition"], s["policy"]): s for s in summary}
    pols = ["ccr", "optimistic_fallback", "attestation_only", "card_only"]
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


def main():
    rows = load_raw()
    summary = summarize(rows)
    write_csv(os.path.join(ROOT, "analysis", "summary.csv"), summary,
              ["condition", "policy", "n", "success_rate", "mean_net_utility",
               "mean_total_cost", "incident_rate", "defection_rate", "mean_canaries"])
    cb = rq2_costbenefit(summary)
    write_csv(os.path.join(ROOT, "analysis", "rq2_costbenefit.csv"), cb,
              ["V", "kappa", "ccr", "optimistic_fallback", "attestation_only", "card_only", "winner"])
    ccr_wins = sum(1 for r in cb if r["winner"] == "ccr")
    print(f"RQ2: CCR is the net-utility winner in {ccr_wins}/{len(cb)} (V, kappa) cells.")


if __name__ == "__main__":
    main()

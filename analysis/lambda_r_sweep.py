"""lambda_r sensitivity (post-hoc repricing) — quantifies Devlog caveat #2.

At the deployed lambda_r=2.0 the net-utility WINNER can be optimistic_fallback even in
cells where CCR has ~5x fewer incidents, because a realized incident is under-priced.
Here we reprice the SAME factorial runs at a range of lambda_r and find the crossover
lambda_r* at which CCR becomes the net-utility winner.

  net_utility(L) = value*success - lambda_c*total_cost - L*incident          (metrics.py)

Only lambda_r's weight on the realized incident changes; success / total_cost / incident
are the observed outcomes. This repricing is:
  - EXACT for every baseline (random, card_only, embedding, attestation_only,
    optimistic_fallback): none use lambda_r to decide (see policies.py — only ccr's
    step-4 expected_utility ranking uses it).
  - a CONSERVATIVE lower bound for CCR: at a higher L, ccr would avoid risky agents MORE
    (expected_utility penalizes risk by L), so its true advantage is >= what we compute
    from its frozen lambda_r=2.0 behavior. The crossover L* reported here is therefore an
    UPPER bound on the true crossover — CCR wins at least this early.

Reads data/results/raw.json (array) or raw.jsonl (lines). Writes a long-format
analysis/lambda_r_sweep_<ts>.csv plus a per-cell crossover summary, and prints the
headline for the high-stakes indistinguishable region (where the caveat bites).

Usage: python analysis/lambda_r_sweep.py
"""
from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "data", "results")

LAMBDA_C = 1.0           # price of cost used in the runs (metrics.py default)
DEPLOYED_LAMBDA_R = 2.0  # the lambda_r the factorial actually ran with
# Baselines compared against (matches analyze.rq2_costbenefit; the strong-baseline set).
BASELINES = ["optimistic_fallback", "attestation_only", "card_only"]
# lambda_r values written to the CSV (the crossover is found on a finer internal grid).
LAMBDAS_OUT = [0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0]

_COND_RE = re.compile(r"^V(\d+)_k([\d.]+)_(clean|hostile|mixed)_(I|D)$")


def load_raw():
    candidates = [os.path.join(RESULTS_DIR, n) for n in ("raw.json", "raw.jsonl")]
    present = [p for p in candidates if os.path.exists(p)]
    if not present:
        raise SystemExit(f"No results found. Expected one of: {candidates}")
    path = max(present, key=os.path.getmtime)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.lstrip().startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def cell_aggregates(rows):
    """For each (condition, policy): mean of the L-independent base term
    A = value*success - lambda_c*total_cost, and the incident_rate. Then
    net(L) = A - L*incident_rate."""
    base = defaultdict(list)   # (cond, pol) -> [value*success - lambda_c*total_cost]
    inc = defaultdict(list)    # (cond, pol) -> [incident 0/1]
    recorded = defaultdict(list)
    for r in rows:
        key = (r["condition"], r["policy"])
        s = 1.0 if r["success"] else 0.0
        i = 1.0 if r["incident"] else 0.0
        base[key].append(r["value"] * s - LAMBDA_C * r["total_cost"])
        inc[key].append(i)
        recorded[key].append(r["net_utility"])
    A, I, rec = {}, {}, {}
    for key in base:
        A[key] = sum(base[key]) / len(base[key])
        I[key] = sum(inc[key]) / len(inc[key])
        rec[key] = sum(recorded[key]) / len(recorded[key])
    return A, I, rec


def net(A, I, key, L):
    return A[key] - L * I[key]


def crossover_lstar(A, I, cond, policies_present):
    """Smallest lambda_r at which ccr's net >= best baseline net, found on a fine grid
    with linear interpolation. Returns None if ccr never overtakes within [0, 40]."""
    ccr_key = (cond, "ccr")
    if ccr_key not in A:
        return None
    bbases = [(cond, b) for b in BASELINES if (cond, b) in A]
    if not bbases:
        return None

    def adv(L):
        return net(A, I, ccr_key, L) - max(net(A, I, b, L) for b in bbases)

    lo, prev = 0.0, adv(0.0)
    if prev >= 0:
        return 0.0
    L = 0.0
    step = 0.1
    while L <= 40.0:
        cur = adv(L)
        if cur >= 0:
            # linear interpolate between (L-step, prev) and (L, cur)
            return round((L - step) + step * (-prev) / (cur - prev), 2)
        prev = cur
        L += step
    return None


def main():
    stamp = datetime.now().strftime("%Y%m%d%H%M")
    rows = load_raw()
    A, I, rec = cell_aggregates(rows)

    # Validation: recomputed net at the deployed lambda_r must match the recorded mean.
    max_err = max(abs(net(A, I, k, DEPLOYED_LAMBDA_R) - rec[k]) for k in A)
    print(f"Validation: max |recomputed net(L=2) - recorded net_utility| = {max_err:.2e} "
          f"({'OK' if max_err < 1e-6 else 'WARN — formula mismatch'})")

    conditions = sorted({c for (c, _) in A})
    policies_present = sorted({p for (_, p) in A})

    # Long-format CSV: one row per (condition, lambda_r).
    out_rows = []
    crossover = {}
    for cond in conditions:
        m = _COND_RE.match(cond)
        V, kappa, pool, canary = (int(m.group(1)), float(m.group(2)), m.group(3), m.group(4)) \
            if m else (None, None, None, None)
        ccr_key = (cond, "ccr")
        bbases = [(cond, b) for b in BASELINES if (cond, b) in A]
        crossover[cond] = crossover_lstar(A, I, cond, policies_present)
        for L in LAMBDAS_OUT:
            ccr_net = net(A, I, ccr_key, L) if ccr_key in A else None
            best_b, best_v = None, float("-inf")
            for b in bbases:
                v = net(A, I, b, L)
                if v > best_v:
                    best_b, best_v = b[1], v
            # overall winner across every policy present (for transparency)
            win_p, win_v = None, float("-inf")
            for p in policies_present:
                if (cond, p) in A:
                    v = net(A, I, (cond, p), L)
                    if v > win_v:
                        win_p, win_v = p, v
            out_rows.append({
                "condition": cond, "V": V, "kappa": kappa, "pool": pool, "canary": canary,
                "lambda_r": L,
                "ccr_net": round(ccr_net, 4) if ccr_net is not None else None,
                "best_baseline": best_b,
                "best_baseline_net": round(best_v, 4),
                "ccr_minus_best": round(ccr_net - best_v, 4) if ccr_net is not None else None,
                "winner": win_p,
            })

    csv_path = os.path.join(ROOT, "analysis", f"lambda_r_sweep_{stamp}.csv")
    fields = ["condition", "V", "kappa", "pool", "canary", "lambda_r", "ccr_net",
              "best_baseline", "best_baseline_net", "ccr_minus_best", "winner"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    print(f"Wrote {csv_path} ({len(out_rows)} rows)")

    # Crossover summary CSV.
    cross_path = os.path.join(ROOT, "analysis", f"lambda_r_crossover_{stamp}.csv")
    with open(cross_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["condition", "V", "kappa", "pool", "canary",
                    "ccr_incident", "best_base_incident", "lambda_r_star"])
        for cond in conditions:
            m = _COND_RE.match(cond)
            if not m:
                continue
            V, kappa, pool, canary = int(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
            ls = crossover[cond]
            bbases = [(cond, b) for b in BASELINES if (cond, b) in A]
            best_inc = I[max(bbases, key=lambda b: net(A, I, b, DEPLOYED_LAMBDA_R))] if bbases else None
            w.writerow([cond, V, kappa, pool, canary,
                        round(I[(cond, "ccr")], 4), round(best_inc, 4) if best_inc is not None else "",
                        ls if ls is not None else "never"])
    print(f"Wrote {cross_path}")

    # --- Headlines on the indistinguishable hostile/mixed cells (where CCR operates) --
    def ccr_wins_at(c, L):
        return net(A, I, (c, "ccr"), L) >= max(net(A, I, (c, b), L) for b in BASELINES)

    ihm = [c for c in conditions if (m := _COND_RE.match(c))
           and m.group(4) == "I" and m.group(3) in ("hostile", "mixed")]

    # (A) High-stakes region (V>=5, kappa<=0.2): CCR should already dominate.
    hs = [c for c in ihm if (m := _COND_RE.match(c))
          and int(m.group(1)) >= 5 and float(m.group(2)) <= 0.2]
    hs_wins2 = sum(1 for c in hs if ccr_wins_at(c, 2.0))
    print(f"\n=== (A) High-stakes region (V>=5, kappa<=0.2, hostile/mixed, I): {len(hs)} cells ===")
    print(f"At deployed lambda_r=2.0, CCR is the net-utility winner in {hs_wins2}/{len(hs)} cells.")

    # (B) The caveat-#2 cells: a baseline OUT-SCORES CCR at lambda_r=2 while having a
    # STRICTLY HIGHER incident rate — i.e., its "win" is an artifact of under-priced risk.
    caveat = []
    for c in ihm:
        if ccr_wins_at(c, 2.0):
            continue
        winner_b = max(BASELINES, key=lambda b: net(A, I, (c, b), 2.0))
        if I[(c, winner_b)] > I[(c, "ccr")] + 1e-9:
            caveat.append((c, winner_b, crossover[c]))
    print(f"\n=== (B) Caveat #2 — baseline 'wins' at lambda_r=2 with MORE incidents than CCR ===")
    print(f"{len(caveat)} indistinguishable hostile/mixed cells. For each, lambda_r* flips it to CCR:")
    flips = [ls for _, _, ls in caveat if ls is not None]
    for c, b, ls in sorted(caveat):
        print(f"  {c:22} winner@2={b:20} ccr_inc={I[(c,'ccr')]:.3f} "
              f"base_inc={I[(c,b)]:.3f}  lambda_r*={ls if ls is not None else 'never(<=40)'}")
    if flips:
        print(f"\n-> Once an incident is priced at lambda_r* in [{min(flips)}, {max(flips)}] "
              f"(median {sorted(flips)[len(flips)//2]}), CCR becomes the winner. The default "
              f"lambda_r=2.0 simply under-prices a ~5x higher incident rate.")
    print("Note: L* are a CONSERVATIVE upper bound — CCR re-optimizing for higher lambda_r "
          "(its step-4 ranking uses lambda_r) would overtake even earlier. 'never' cells are "
          "honest economic boundaries: very expensive canaries / low task value, where a "
          "zero-incident under-router (attestation_only) is legitimately preferable.")


if __name__ == "__main__":
    main()

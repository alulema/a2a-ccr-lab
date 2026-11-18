"""Are CCR's estimates calibrated probabilities? (ICITS camera-ready, reviewer 1)

For every top-k candidate in the headline cell (V=5, kappa=0.2, indistinguishable canary) we
compare CCR's success estimate p_hat = alpha*Card + (1-alpha)*Canary with the outcome of a real
call to that candidate, and its Risk score with whether the agent is malicious. Reports Brier,
expected calibration error (5 bins) and ROC-AUC. CCR only ranks by these quantities, so AUC is
what it relies on; ECE says whether they can be read as probabilities.

Usage: python analysis/calibration.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "scripts")]

from ccrlib import metrics, scoring  # noqa: E402
from ccrlib.policies import Context, _canary_call, _candidates, _real_call, _tie  # noqa: E402
from ccrlib.scoring import Params  # noqa: E402

import gen_dataset  # noqa: E402
import local_run  # noqa: E402

BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0001)]


def report(name: str, pred: list[float], obs: list[bool]) -> None:
    n = len(pred)
    base = sum(obs) / n
    brier = sum((p - o) ** 2 for p, o in zip(pred, obs)) / n
    brier0 = sum((base - o) ** 2 for o in obs) / n
    ece = 0.0
    for lo, hi in BINS:
        idx = [i for i in range(n) if lo <= pred[i] < hi]
        if idx:
            ece += len(idx) / n * abs(sum(pred[i] for i in idx) / len(idx) - sum(obs[i] for i in idx) / len(idx))
    pos = [p for p, o in zip(pred, obs) if o]
    neg = [p for p, o in zip(pred, obs) if not o]
    auc = (sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))
           if pos and neg else float("nan"))
    print(f"  {name:<28} Brier {brier:.3f} (base rate {brier0:.3f})  ECE {ece:.3f}  AUC {auc:.3f}")


def main() -> None:
    all_specs = gen_dataset.generate_agents()
    tasks = gen_dataset.generate_tasks(24)
    p = Params(value=5.0, canary_cost=0.2, budget=3.8)
    for pool in ["mixed", "hostile", "clean"]:
        specs = local_run.pool_specs(all_specs, pool)
        by_id = {s["id"]: s for s in specs}
        cards, transport = local_run.build_env(specs)
        p_hat, card, risk, ok, mal = [], [], [], [], []
        gate = 0
        for task in tasks:
            for seed in range(6):
                ctx = Context(task, cards, transport, p, seed, True)
                topk = sorted(_candidates(ctx), key=lambda a: (scoring.card_score(cards[a], task, p),
                                                               _tie(ctx, a)), reverse=True)[:p.k_candidates]
                for a in topk:
                    cs0 = scoring.card_score(cards[a], task, p)
                    gate += scoring.value_of_information(cs0, scoring.risk_score(cards[a], None, p), p) > p.voi_threshold
                    can, _, _ = _canary_call(ctx, a)
                    p_hat.append(p.alpha * cs0 + (1 - p.alpha) * can)
                    card.append(cs0)
                    risk.append(scoring.risk_score(cards[a], {"_canary_score": can}, p))
                    ok.append(_real_call(ctx, a, 0)[0])
                    mal.append(metrics._is_malicious(by_id[a]))
        print(f"pool={pool}: {len(ok)} top-k candidates; VoI gate fires for {gate}")
        report("p_hat vs real success", p_hat, ok)
        report("card alone vs real success", card, ok)
        if any(mal):
            report("Risk vs malicious", risk, mal)


if __name__ == "__main__":
    main()

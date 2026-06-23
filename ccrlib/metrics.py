"""Evaluation: label each decision with ground truth (incident/defection) and
aggregate into the operational metrics the paper reports.

Ground-truth persona is used HERE only (the evaluator), never inside a policy.
"""
from __future__ import annotations

from statistics import mean
from typing import Any

from .policies import Decision
from .scoring import Params

MALICIOUS = {"adversarial", "canary_aware"}


def _is_malicious(spec: dict) -> bool:
    return (spec.get("persona") in MALICIOUS) or bool(spec.get("poison"))


def evaluate(dec: Decision, spec_by_id: dict[str, dict], p: Params) -> dict[str, Any]:
    chosen = dec.chosen
    persona = spec_by_id.get(chosen, {}).get("persona") if chosen else None

    # Side-effect incident: ANY REAL execution that touched a malicious agent —
    # whether or not the task ultimately succeeded. A real A2A call ships your task
    # (and data) to that agent, so harm/exposure happens at invocation time. This
    # is the key asymmetry: a canary uses synthetic data and is side-effect-free,
    # so probing a liar is safe while *executing* on it is not — it is what makes
    # "probe before delegate" worth more than "just retry on failure".
    incident = any(_is_malicious(spec_by_id.get(a, {})) for a in dec.real_attempts)

    # Strategic defection: a malicious agent that PASSED the canary and then failed
    # its real attempt — a successful probe evasion (the case prior "lazy peer"
    # work does not capture).
    defection = any(_is_malicious(spec_by_id.get(a, {})) for a in dec.canary_pass_real_fail)

    net = (
        p.value * (1.0 if dec.success else 0.0)
        - p.lambda_c * dec.total_cost
        - p.lambda_r * (1.0 if incident else 0.0)
    )
    return {
        "policy": dec.policy,
        "chosen": chosen,
        "chosen_persona": persona,
        "success": dec.success,
        "total_cost": dec.total_cost,
        "cost_canary": dec.cost_canary,
        "cost_exec": dec.cost_exec,
        "latency_ms": dec.latency_ms,
        "n_canaries": len(dec.canaried),
        "n_real_attempts": len(dec.real_attempts),
        "incident": incident,
        "defection": defection,
        "chose_malicious": _is_malicious(spec_by_id.get(chosen, {})) if chosen else False,
        "net_utility": net,
    }


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(q * len(s)))
    return s[idx]


def aggregate(records: list[dict], keys: tuple[str, ...] = ("condition", "policy")) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        groups.setdefault(tuple(r[k] for k in keys), []).append(r)

    rows = []
    for key, recs in sorted(groups.items()):
        n = len(recs)
        lat = [r["latency_ms"] for r in recs]
        row = {k: v for k, v in zip(keys, key)}
        row.update(
            n=n,
            success_rate=round(mean(r["success"] for r in recs), 4),
            mean_total_cost=round(mean(r["total_cost"] for r in recs), 4),
            mean_net_utility=round(mean(r["net_utility"] for r in recs), 4),
            p50_latency_ms=round(_percentile(lat, 0.50)),
            p95_latency_ms=round(_percentile(lat, 0.95)),
            incident_rate=round(mean(r["incident"] for r in recs), 4),
            # Probe-evasion (defection) rate: fraction of runs where a malicious
            # agent passed the canary yet failed the real task.
            defection_rate=round(mean(r["defection"] for r in recs), 4),
            mean_canaries=round(mean(r["n_canaries"] for r in recs), 3),
        )
        rows.append(row)
    return rows

"""Experiment driver: runs the policy suite across a grid of conditions and
returns raw per-run records plus aggregated rows.

`build_env(active_specs) -> (cards, transport)` abstracts the transport so the
SAME experiment runs in-process locally (SimAgent calls) or over HTTP in Azure
(A2A JSON-RPC). Determinism guarantees identical numbers across both.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from . import metrics
from .policies import POLICIES, Context, Decision
from .scoring import Params


def run(
    tasks: list[dict],
    conditions: list[dict],
    build_env: Callable[[list[dict]], tuple[dict, Callable]],
    seeds: list[int],
    policies: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    policies = policies or list(POLICIES.keys())
    raw: list[dict] = []

    for cond in conditions:
        active_specs: list[dict] = cond["specs"]
        params: Params = cond["params"]
        indistinguishable: bool = cond.get("indistinguishable", True)
        cards, transport = build_env(active_specs)
        spec_by_id = {s["id"]: s for s in active_specs}

        for task in tasks:
            for seed in seeds:
                ctx = Context(task, cards, transport, params, seed, indistinguishable)
                for pol in policies:
                    dec: Decision = POLICIES[pol](ctx)
                    rec = metrics.evaluate(dec, spec_by_id, params)
                    rec.update(
                        condition=cond["name"],
                        task=task["id"],
                        skill=task["skill"],
                        seed=seed,
                        value=params.value,
                        canary_cost=params.canary_cost,
                        indistinguishable=indistinguishable,
                    )
                    raw.append(rec)

    rows = metrics.aggregate(raw, keys=("condition", "policy"))
    return rows, raw


def with_params(base: Params, **overrides) -> Params:
    return replace(base, **overrides)

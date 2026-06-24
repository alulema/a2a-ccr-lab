"""Routing policies: the proposed CCR and five baselines.

A policy receives a `Context` and returns a `Decision`. It interacts with agents
ONLY through `ctx.transport`, and verifies success by comparing returned outputs
to the known true answer. It has no access to agents' hidden capability or
persona — those are used only by the post-hoc evaluator (experiment.py) to label
incidents/defections.

Baselines
---------
- random              : control.
- card_only           : argmax declarative card score.
- embedding           : argmax cosine similarity (semantic match), ignores trust.
- attestation_only    : among signature-valid agents, argmax card score. Models the
                        "attested routing" line (Provenance Paradox): identity is
                        verified, but current capability is NOT — so honest-but-
                        degraded agents are still chosen.
- optimistic_fallback : try the real task on the best card; on failure fall to the
                        next, up to budget. No canary. The HARD baseline for RQ2.
- ccr                 : the proposed Card+Canary Routing with VoI-gated probing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import domain, embeddings, scoring
from .scoring import Params

Transport = Callable[[str, dict], dict]


@dataclass
class Context:
    task: dict
    cards: dict[str, dict]
    transport: Transport
    params: Params
    run_seed: int
    indistinguishable: bool = True

    @property
    def expected(self) -> str:
        return domain.true_answer(self.task["payload"])

    @property
    def canary_payload(self) -> str:
        return f"canary::{self.task['skill']}::{self.task['id']}"

    @property
    def canary_expected(self) -> str:
        return domain.true_answer(self.canary_payload)


@dataclass
class Decision:
    policy: str
    chosen: str | None
    canaried: list[str] = field(default_factory=list)
    real_attempts: list[str] = field(default_factory=list)
    success: bool = False
    cost_canary: float = 0.0
    cost_exec: float = 0.0
    latency_ms: int = 0
    # Per-chosen-agent canary outcome (for defection labeling by the evaluator).
    chosen_passed_canary: bool | None = None
    # Agents that PASSED the canary (score >= 0.5) yet FAILED their real attempt:
    # a successful probe evasion. The evaluator checks their persona to label
    # strategic defection regardless of whether fallback later recovered. (RQ4/C3)
    canary_pass_real_fail: list[str] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        return self.cost_canary + self.cost_exec


# --- low-level calls -----------------------------------------------------------

def _real_call(ctx: Context, agent_id: str, attempt: int) -> tuple[bool, int]:
    resp = ctx.transport(agent_id, {
        "kind": "task",
        "taskId": ctx.task["id"],
        "skill": ctx.task["skill"],
        "payload": ctx.task["payload"],
        "runSeed": ctx.run_seed,
        "attempt": attempt,
        "canaryIndistinguishable": ctx.indistinguishable,
    })
    return resp["output"] == ctx.expected, resp["latencyMs"]


def _canary_call(ctx: Context, agent_id: str) -> tuple[float, int, dict]:
    resp = ctx.transport(agent_id, {
        "kind": "canary",
        "taskId": ctx.task["id"],
        "skill": ctx.task["skill"],
        "payload": ctx.canary_payload,
        "runSeed": ctx.run_seed,
        "attempt": 0,
        "canaryIndistinguishable": ctx.indistinguishable,
    })
    cs = scoring.canary_score(resp, ctx.canary_expected)
    resp["_canary_score"] = cs
    return cs, resp["latencyMs"], resp


def _candidates(ctx: Context) -> list[str]:
    return [aid for aid, c in ctx.cards.items() if scoring.hard_compatible(c, ctx.task)]


def _tie(ctx: Context, a: str) -> float:
    """Seeded tie-break for card-score selection. Two validly-signed agents can declare
    the SAME capability (e.g. a stale `degraded` and a `canary_aware` defector both at the
    ceiling); a router cannot tell them apart, so resolving the tie by agent insertion
    order is an artifact. We break ties uniformly at random, seeded for reproducibility,
    so the choice averages over the indistinguishable options across seeds."""
    return domain.det_unit(ctx.run_seed, ctx.task["id"], a, "tiebreak")


def _single_real(ctx: Context, policy: str, chosen: str | None) -> Decision:
    d = Decision(policy=policy, chosen=chosen)
    if chosen is None:
        return d
    ok, lat = _real_call(ctx, chosen, 0)
    d.real_attempts.append(chosen)
    d.success = ok
    d.cost_exec += ctx.params.exec_cost
    d.latency_ms += lat
    return d


# --- baselines -----------------------------------------------------------------

def random_policy(ctx: Context) -> Decision:
    cands = _candidates(ctx)
    if not cands:
        return Decision(policy="random", chosen=None)
    idx = int(domain.det_unit(ctx.run_seed, ctx.task["id"], "random") * len(cands)) % len(cands)
    return _single_real(ctx, "random", sorted(cands)[idx])


def card_only(ctx: Context) -> Decision:
    cands = _candidates(ctx)
    if not cands:
        return Decision(policy="card_only", chosen=None)
    chosen = max(cands, key=lambda a: (scoring.card_score(ctx.cards[a], ctx.task, ctx.params),
                                       _tie(ctx, a)))
    return _single_real(ctx, "card_only", chosen)


def embedding_policy(ctx: Context) -> Decision:
    cands = _candidates(ctx)
    if not cands:
        return Decision(policy="embedding", chosen=None)
    q = embeddings.embed(ctx.task.get("text", ctx.task["skill"]))

    def sim(a: str) -> float:
        c = ctx.cards[a]
        text = c.get("description", "") + " " + " ".join(s["id"] for s in c["skills"])
        return embeddings.cosine(q, embeddings.embed(text))

    return _single_real(ctx, "embedding", max(cands, key=lambda a: (sim(a), _tie(ctx, a))))


def attestation_only(ctx: Context) -> Decision:
    cands = [a for a in _candidates(ctx) if scoring.verify_signature(ctx.cards[a]) == "valid"]
    if not cands:
        return Decision(policy="attestation_only", chosen=None)
    chosen = max(cands, key=lambda a: (scoring.card_score(ctx.cards[a], ctx.task, ctx.params),
                                       _tie(ctx, a)))
    return _single_real(ctx, "attestation_only", chosen)


def optimistic_fallback(ctx: Context) -> Decision:
    p = ctx.params
    d = Decision(policy="optimistic_fallback", chosen=None)
    cands = _candidates(ctx)
    if not cands:
        return d
    order = sorted(cands, key=lambda a: (scoring.card_score(ctx.cards[a], ctx.task, p),
                                         _tie(ctx, a)), reverse=True)
    budget = p.budget
    attempt = 0
    for a in order:
        if budget < p.exec_cost:
            break
        ok, lat = _real_call(ctx, a, attempt)
        d.real_attempts.append(a)
        d.cost_exec += p.exec_cost
        d.latency_ms += lat
        budget -= p.exec_cost
        d.chosen = a
        attempt += 1
        if ok:
            d.success = True
            break
    return d


# --- proposed: CCR -------------------------------------------------------------

def ccr(ctx: Context) -> Decision:
    p = ctx.params
    d = Decision(policy="ccr", chosen=None)
    cands = _candidates(ctx)
    if not cands:
        return d

    # 1-2. soft card scoring (signature already folded into card_score).
    scored = sorted(cands, key=lambda a: (scoring.card_score(ctx.cards[a], ctx.task, p),
                                          _tie(ctx, a)), reverse=True)
    topk = scored[: p.k_candidates]

    budget = p.budget
    canary_cs: dict[str, float] = {}
    canary_passed: dict[str, bool] = {}

    # 3. VoI-gated canary on preselected candidates, riskiest first.
    def base_risk(a: str) -> float:
        return scoring.risk_score(ctx.cards[a], None, p)

    for a in sorted(topk, key=base_risk, reverse=True):
        cs0 = scoring.card_score(ctx.cards[a], ctx.task, p)
        voi = scoring.value_of_information(cs0, base_risk(a), p)
        if voi > p.voi_threshold and budget >= p.canary_cost:
            cs, lat, _ = _canary_call(ctx, a)
            canary_cs[a] = cs
            canary_passed[a] = cs >= 0.5
            d.canaried.append(a)
            d.cost_canary += p.canary_cost
            d.latency_ms += lat
            budget -= p.canary_cost

    # 4. expected-utility ranking using dual evidence.
    def utility(a: str) -> float:
        card_sc = scoring.card_score(ctx.cards[a], ctx.task, p)
        if a in canary_cs:
            p_hat = p.alpha * card_sc + (1 - p.alpha) * canary_cs[a]
            metrics = {"_canary_score": canary_cs[a]}
        else:
            p_hat = card_sc
            metrics = None
        risk = scoring.risk_score(ctx.cards[a], metrics, p)
        return scoring.expected_utility(p_hat, risk, p.exec_cost, p)

    plan = sorted(topk, key=lambda a: (utility(a), _tie(ctx, a)), reverse=True)

    # 5. delegate real task; budget-bounded fallback to the next-best by utility.
    attempt = 0
    for a in plan:
        if budget < p.exec_cost:
            break
        ok, lat = _real_call(ctx, a, attempt)
        d.real_attempts.append(a)
        d.cost_exec += p.exec_cost
        d.latency_ms += lat
        budget -= p.exec_cost
        d.chosen = a
        d.chosen_passed_canary = (canary_cs.get(a, 1.0) >= 0.5) if a in canary_cs else None
        if not ok and canary_passed.get(a):
            d.canary_pass_real_fail.append(a)  # this agent beat the probe
        attempt += 1
        if ok:
            d.success = True
            break
    return d


POLICIES: dict[str, Callable[[Context], Decision]] = {
    "random": random_policy,
    "card_only": card_only,
    "embedding": embedding_policy,
    "attestation_only": attestation_only,
    "optimistic_fallback": optimistic_fallback,
    "ccr": ccr,
}

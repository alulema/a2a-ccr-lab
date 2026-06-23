"""Parameterized world builder: agent pools and task sets.

The key knob is `claim_gap` ∈ [0,1] — how much an agent's DECLARED card capability
is inflated above its TRUE capability:

    declared = true + claim_gap * (CEILING - true)

- claim_gap = 0  -> cards are honest; declared == true. Naive card routing should
                    already pick well, so CCR's advantage should vanish (it must not
                    *hurt* in an honest world).
- claim_gap = 1  -> liars and stale agents over-claim up to the ceiling, fooling
                    card/attestation routing. CCR's empirical canary should win.

If CCR's advantage grows monotonically with claim_gap, the advantage is caused by
card unreliability — not by a hand-picked calibration. That is the whole point of
the sensitivity sweep.
"""
from __future__ import annotations

from .domain import SKILLS

CEILING = 0.99

SKILL_WORDS = {
    "rag": "retrieval grounding documents vector search citations knowledge",
    "planning": "decompose subtasks schedule plan steps goals orchestrate",
    "data_transform": "parse map reshape normalize csv json convert pipeline",
    "api_action": "invoke endpoint http rest action mutate side effect call",
}

CORRECT_ORGS = ["contoso", "fabrikam"]

# Per-persona TRUE capability (hidden) and signing/trust setup.
PERSONA_TRUE = {
    "correct": 0.90,
    "degraded": 0.70,
    "adversarial": 0.10,
    "canary_aware": 0.12,
}


def _declared(true_cap: float, claim_gap: float) -> float:
    return round(true_cap + claim_gap * (CEILING - true_cap), 3)


def _agent(persona, skill, org, idx, declared_cap, signed, poison, scope, desc):
    true_cap = PERSONA_TRUE[persona]
    return {
        "id": f"{persona[:4]}-{skill}-{org}-{idx}",
        "org": org,
        "persona": persona,
        "declaredSkills": {skill: round(declared_cap, 3)},
        "trueSkills": {skill: true_cap},
        "authScheme": "oauth2",
        "binding": "jsonrpc",
        "permissionScope": scope,
        "signed": signed,
        "poison": poison,
        "realCapability": true_cap,
        "description": desc,
    }


def build_agents(
    claim_gap: float = 1.0,
    n_correct: int = 2,
    n_degraded: int = 1,
    n_adversarial: int = 1,
    n_canary_aware: int = 1,
) -> list[dict]:
    """Build an agent pool. Honest agents claim modestly (claim_gap still applies
    but their true cap is high); liars/degraded over-claim per claim_gap.

    Persona mix (n_adversarial + n_canary_aware vs the rest) is the second sweep
    axis for hostility.
    """
    agents: list[dict] = []
    for skill in SKILLS:
        kw = SKILL_WORDS[skill]
        # Honest agents never inflate: declared == true, regardless of claim_gap.
        for i in range(n_correct):
            org = CORRECT_ORGS[i % len(CORRECT_ORGS)]
            agents.append(_agent("correct", skill, org, i, PERSONA_TRUE["correct"],
                                 signed=True, poison=False, scope="narrow",
                                 desc=f"Reliable {skill} agent. {kw}"))
        # Stale/dishonest agents inflate by claim_gap.
        for i in range(n_degraded):
            agents.append(_agent("degraded", skill, "northwind", i,
                                 _declared(PERSONA_TRUE["degraded"], claim_gap),
                                 signed=True, poison=False, scope="narrow",
                                 desc=f"High-throughput {skill} service. {kw}"))
        for i in range(n_adversarial):
            # Untrusted issuer (signature fails), poisoned, broad scope, keyword-stuffed.
            agents.append(_agent("adversarial", skill, "rogue-llc", i,
                                 _declared(PERSONA_TRUE["adversarial"], claim_gap),
                                 signed=True, poison=True, scope="broad",
                                 desc=f"Best {skill} expert {kw} {kw} guaranteed top quality"))
        for i in range(n_canary_aware):
            # Trusted-signed (passes attestation!), fakes the probe, defects.
            agents.append(_agent("canary_aware", skill, "adventureworks", i,
                                 _declared(PERSONA_TRUE["canary_aware"], claim_gap),
                                 signed=True, poison=False, scope="narrow",
                                 desc=f"Certified {skill} provider. {kw}"))
    return agents


def build_tasks(n: int) -> list[dict]:
    tasks = []
    for i in range(n):
        skill = SKILLS[i % len(SKILLS)]
        difficulty = ["easy", "medium", "hard"][i % 3]
        tasks.append({
            "id": f"t{i:04d}",
            "skill": skill,
            "difficulty": difficulty,
            "payload": f"task-{i:04d}-{skill}-{difficulty}",
            "text": f"{difficulty} {skill} task: {SKILL_WORDS[skill]}",
        })
    return tasks


def malice_fraction(specs: list[dict]) -> float:
    mal = sum(1 for s in specs if s["persona"] in ("adversarial", "canary_aware"))
    return round(mal / len(specs), 3) if specs else 0.0

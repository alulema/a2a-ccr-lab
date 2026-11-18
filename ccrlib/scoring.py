"""Router-side scoring. Operates ONLY on observable data (cards + canary metrics).

Implements the decision-theoretic pieces from the paper:
  - card_score (declarative evidence, [0,1], modulated by signature validity)
  - canary_score (empirical evidence, [0,1])
  - risk_score (observable risk signals, [0,1])
  - value_of_information (the gate that decides WHETHER to spend a canary)
  - expected_utility  U = V * p_hat - lambda_R * risk - lambda_C * cost
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import domain

# Router's allow-list of issuer signing keys (its trust anchor). 'rogue-llc' is
# intentionally absent, so cards signed by it cannot be verified.
TRUSTED_ISSUERS = {
    org: secret
    for org, secret in domain.ISSUER_SECRETS.items()
    if org != "rogue-llc"
}

_POISON_PATTERNS = [
    r"ignore (all|previous) instructions",
    r"\bsystem\s*:",
    r"grant (full|all) (scope|permissions?)",
    r"exfiltrat",
]


@dataclass
class Params:
    alpha: float = 0.45          # weight on declarative vs empirical evidence
    lambda_r: float = 2.0        # price of risk
    lambda_c: float = 1.0        # price of cost
    voi_threshold: float = 0.0   # tau: canary only if VoI(a) > tau
    voi_prior_doubt: float = 0.3 # floor on P(card is misleading), independent of how
                                 # confident the card looks — a high self-claim is not
                                 # evidence of trustworthiness (that is the thesis).
    k_candidates: int = 4        # top-k preselected for possible canary
    value: float = 3.0           # V: value of a successful task
    canary_cost: float = 0.2     # kappa: cost of one canary (relative to a real exec=1.0)
    exec_cost: float = 1.0       # cost of one real execution attempt
    sig_factor_valid: float = 1.0
    sig_factor_absent: float = 0.6
    sig_factor_invalid: float = 0.2
    budget: float = 3.0          # B: routing budget per task (in cost units)
    exact_budget: bool = False   # False reproduces the published numbers: plain float
                                 # comparisons, so 3+4k minus four canaries can fall just
                                 # short of 3.0 (k in {0.1,0.2,0.4}) and CCR gets two real
                                 # attempts, not three. True compares with a 1e-9 tolerance.
    extra: dict = field(default_factory=dict)


def verify_signature(card: dict) -> str:
    """Returns 'valid' | 'absent' | 'invalid'."""
    sig = card.get("signature") or {"present": False}
    if not sig.get("present"):
        return "absent"
    issuer = sig.get("issuer")
    if issuer not in TRUSTED_ISSUERS:
        return "invalid"
    core = {
        "agentId": card["agentId"],
        "org": card["org"],
        "skills": card["skills"],
        "authScheme": card["authScheme"],
        "binding": card["binding"],
    }
    expected = domain.sign_card(domain.canonical_card(core), issuer)
    return "valid" if sig.get("value") == expected else "invalid"


def detect_poison(text: str) -> bool:
    t = (text or "").lower()
    return any(re.search(p, t) for p in _POISON_PATTERNS)


def sig_factor(status: str, p: Params) -> float:
    return {
        "valid": p.sig_factor_valid,
        "absent": p.sig_factor_absent,
        "invalid": p.sig_factor_invalid,
    }[status]


def card_score(card: dict, task: dict, p: Params) -> float:
    """Declarative compatibility in [0,1]."""
    skill = task["skill"]
    claimed = 0.0
    for s in card["skills"]:
        if s["id"] == skill:
            claimed = s["capabilityClaimed"]
            break
    status = verify_signature(card)
    return max(0.0, min(1.0, claimed * sig_factor(status, p)))


def hard_compatible(card: dict, task: dict) -> bool:
    """Hard filter: agent must declare the skill and use a supported binding/auth."""
    declares = any(s["id"] == task["skill"] for s in card["skills"])
    binding_ok = card.get("binding") == "jsonrpc"
    auth_ok = card.get("authScheme") in {"oauth2", "mtls", "apikey"}
    return declares and binding_ok and auth_ok


def canary_score(metrics: dict, expected_answer: str) -> float:
    """Empirical evidence in [0,1] from a verified canary result."""
    success = 1.0 if metrics.get("output") == expected_answer else 0.0
    schema = 1.0 if metrics.get("schemaOk") else 0.0
    status_ok = 1.0 if metrics.get("httpStatus", 500) < 400 else 0.0
    # Latency factor: 1.0 at 0ms, decaying to ~0 around 800ms.
    lat = metrics.get("latencyMs", 800)
    lat_factor = max(0.0, 1.0 - lat / 800.0)
    return max(0.0, min(1.0, 0.6 * success + 0.2 * schema + 0.1 * status_ok + 0.1 * lat_factor))


def risk_score(card: dict, canary_metrics: dict | None, p: Params) -> float:
    """Observable risk signals in [0,1]."""
    r = 0.0
    if verify_signature(card) == "invalid":
        r += 0.40
    if detect_poison(card.get("description", "")):
        r += 0.35
    if card.get("permissionScope") == "broad":
        r += 0.15
    if canary_metrics is not None:
        # Inconsistency: card claims competence but the canary failed.
        claimed = max((s["capabilityClaimed"] for s in card["skills"]), default=0.0)
        canary_failed = canary_metrics.get("_canary_score", 1.0) < 0.5
        if claimed > 0.7 and canary_failed:
            r += 0.30
    return min(1.0, r)


def value_of_information(card_sc: float, base_risk: float, p: Params) -> float:
    """Expected utility gain from resolving uncertainty about an agent, minus the
    canary cost. We canary agent a iff VoI(a) > tau and the budget allows.

    Uncertainty is the MAX of (a) a prior floor of doubt about any declarative
    claim, (b) the mid-range card ambiguity 4*x*(1-x), and (c) observable risk.
    The prior floor is essential: without it, a confident-looking liar (card≈0.99)
    appears certain and evades verification — exactly the poisoning failure mode.
    """
    uncertainty = max(p.voi_prior_doubt, 4.0 * card_sc * (1.0 - card_sc), base_risk)
    expected_gain = p.value * uncertainty
    return expected_gain - p.lambda_c * p.canary_cost


def expected_utility(p_hat: float, risk: float, cost: float, p: Params) -> float:
    return p.value * p_hat - p.lambda_r * risk - p.lambda_c * cost

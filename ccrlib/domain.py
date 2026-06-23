"""Shared, non-secret domain primitives: skills, deterministic RNG, verifiable
synthetic tasks, signature crypto (simulated), and a deterministic embedding.

Everything here is computable by BOTH the agent host and the router. The hidden
part of an agent (its *true* capability) lives in agents.py and is never exposed.
"""
from __future__ import annotations

import hashlib
import math
from typing import Iterable

# Skill taxonomy used across the benchmark.
SKILLS = ["rag", "planning", "data_transform", "api_action"]

# Simulated issuer signing keys. "Trusted" orgs are the ones whose public key the
# router has in its allow-list (see scoring.TRUSTED_ISSUERS). A lying agent that
# signs with an unknown org key cannot be verified -> signature invalid.
ISSUER_SECRETS = {
    "contoso": "k_contoso_9f12",
    "fabrikam": "k_fabrikam_77aa",
    "northwind": "k_northwind_31cd",
    "adventureworks": "k_adv_5bb0",
    # 'rogue-llc' deliberately absent from the router allow-list.
    "rogue-llc": "k_rogue_unknown",
}


def _h(*parts: object) -> int:
    """Stable 256-bit hash of the parts, as an int."""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest(), "big")


def det_unit(*parts: object) -> float:
    """Deterministic pseudo-random float in [0, 1) from arbitrary parts.

    This replaces random.random() so runs are perfectly reproducible across
    machines, processes, and the local/cloud boundary.
    """
    return (_h(*parts) % 10_000_000) / 10_000_000.0


# --- Verifiable synthetic tasks -------------------------------------------------
# A task payload is a synthetic string. The "correct answer" is a deterministic
# function of the payload that a *capable* agent can compute and an *incapable*
# one cannot. The router verifies outputs against this — it never trusts an
# agent's self-reported success.

def true_answer(payload: str) -> str:
    return hashlib.sha256(("ANS::" + payload).encode("utf-8")).hexdigest()[:16]


def agent_output(payload: str, correct: bool) -> str:
    """What an agent returns. Correct agents return the true answer; incorrect
    ones return a deterministic wrong answer (so retries of an incapable agent
    keep failing, as in reality)."""
    if correct:
        return true_answer(payload)
    return hashlib.sha256(("WRONG::" + payload).encode("utf-8")).hexdigest()[:16]


# --- Simulated AgentCard signature ---------------------------------------------

def sign_card(canonical: str, issuer: str) -> str:
    secret = ISSUER_SECRETS.get(issuer, "k_unknown")
    return hashlib.sha256((canonical + "::" + secret).encode("utf-8")).hexdigest()[:24]


def canonical_card(card: dict) -> str:
    """Stable serialization of the signed core of a card (mirrors A2A's
    canonicalization-before-JWS idea)."""
    skills = ",".join(f"{s['id']}:{s['capabilityClaimed']:.3f}" for s in card["skills"])
    return f"{card['agentId']}|{card['org']}|{skills}|{card['authScheme']}|{card['binding']}"


# --- Deterministic embedding (for the embedding-routing baseline) ---------------

_EMBED_DIM = 64


def embed(text: str) -> list[float]:
    """A hashing bag-of-words embedding. Dependency-free and deterministic, so the
    embedding baseline is reproducible without a model server. (Azure OpenAI
    embeddings can be swapped in via config for the cloud variant.)"""
    vec = [0.0] * _EMBED_DIM
    for tok in _tokenize(text):
        idx = _h("emb", tok) % _EMBED_DIM
        sign = 1.0 if _h("sgn", tok) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _tokenize(text: str) -> Iterable[str]:
    cur = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            yield "".join(cur)
            cur = []
    if cur:
        yield "".join(cur)

"""LLM judge + canary generator for the indistinguishability experiment (adversary #2).

Needs a chat deployment on the same Azure OpenAI resource:
    export AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o-mini
    export AZURE_OPENAI_ENDPOINT=...            # falls back to AZURE_OPENAI_EMBEDDINGS_ENDPOINT

- make_generator() -> fn(qa_items) -> [canary question strings]: the chat model writes a
  question matched to the real distribution whose answer is a known fact (so the router can
  still grade it). This is the *synthesized-but-disguised* canary — the realistic case.
- judge_auc(texts, labels) -> {auc, ...}: the chat model scores P(query is a synthetic probe)
  for each query; the ROC-AUC of those scores vs the true canary labels is how well a
  *reasoning* adversary separates probes from real tasks. AUC~0.5 => it cannot.

Calls are bounded (JUDGE_CAP) so a run does not fan out to thousands of chat requests.
"""
from __future__ import annotations

import os
import re

import numpy as np

JUDGE_CAP = 300              # max queries actually scored by the judge (subsampled, seeded)
_client = None
_GEN_SYS = ("You write short factual questions for a reading-comprehension benchmark. "
            "Given a target ANSWER, write ONE natural question, similar in style and length "
            "to typical such questions, whose correct answer is exactly that target. "
            "Output ONLY the question text, nothing else.")
_JUDGE_SYS = ("A stream of genuine user questions has had synthetic 'verification probe' "
              "questions secretly mixed in. For the given question, output ONLY a number in "
              "[0,1]: the probability it is a SYNTHETIC probe rather than a genuine user "
              "question. Output the number only.")


def _chat():
    global _client
    if _client is None:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        from openai import AzureOpenAI
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT") or os.environ["AZURE_OPENAI_EMBEDDINGS_ENDPOINT"]
        _client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return _client


def _ask(system: str, user: str, budget: int = 512) -> str:
    """One chat turn. Robust to API shape differences: gpt-5/o-series reject a custom
    `temperature` and use `max_completion_tokens` (and spend reasoning tokens, so the budget
    must be generous); older models use `max_tokens`. We try the modern shape first, fall back
    to the legacy one, and never pass temperature (default is fine and universally accepted)."""
    dep = os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"]
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for kw in ({"max_completion_tokens": budget}, {"max_tokens": budget}):
        try:
            r = _chat().chat.completions.create(model=dep, messages=msgs, **kw)
            return (r.choices[0].message.content or "").strip()
        except Exception:                           # noqa: BLE001 — try the other param shape
            continue
    return ""


def make_generator():
    def gen(qa_items: list[dict]) -> list[str]:
        out = []
        for q in qa_items:
            try:
                out.append(_ask(_GEN_SYS, f"ANSWER: {q['answer']}") or q["question"])
            except Exception:                       # noqa: BLE001 — fall back to a real question
                out.append(q["question"])
        return out
    return gen


_NUM = re.compile(r"(?<![\d.])(0(?:\.\d+)?|1(?:\.0+)?|\.\d+)(?![\d])")


def _score(text: str) -> float:
    """P(query is a synthetic probe). Extract the first [0,1] number anywhere in the reply
    (reasoning models may wrap the answer); default to 0.5 (no information) if none."""
    m = _NUM.search(_ask(_JUDGE_SYS, text))
    if not m:
        return 0.5
    return min(1.0, max(0.0, float(m.group(1))))


def _auc(y_pos: np.ndarray, score: np.ndarray) -> float:
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), float)
    ranks[order] = np.arange(1, len(score) + 1)
    npos, nneg = int(y_pos.sum()), int((y_pos == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    return (ranks[y_pos == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg)


def judge_auc(texts: list[str], labels: np.ndarray) -> dict:
    """labels: 1=real, 0=canary. Returns AUC of P(probe) at separating canaries (positives)."""
    idx = np.arange(len(texts))
    if len(texts) > JUDGE_CAP:                      # subsample, seeded, class-balanced enough
        rng = np.random.default_rng(0)
        idx = np.sort(rng.choice(idx, JUDGE_CAP, replace=False))
    scores = np.array([_score(texts[i]) for i in idx])
    is_canary = (labels[idx] == 0).astype(int)      # judge should give canaries high P(probe)
    return {"auc": round(_auc(is_canary, scores), 4), "n_scored": int(len(idx)),
            "mean_score_real": round(float(scores[is_canary == 0].mean()), 3),
            "mean_score_canary": round(float(scores[is_canary == 1].mean()), 3)}

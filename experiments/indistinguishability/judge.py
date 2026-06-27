"""LLM judge + canary generator for the indistinguishability experiment (adversary #2).

Needs a chat deployment on the same Azure OpenAI resource:
    export AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-5.4-mini
    export AZURE_OPENAI_ENDPOINT=...            # falls back to AZURE_OPENAI_EMBEDDINGS_ENDPOINT

- make_generator() -> fn(qa_items) -> [canary question strings]: the chat model writes a
  question matched to the real distribution whose answer is a known fact (so the router can
  still grade it). This is the *synthesized-but-disguised* canary — the realistic case.
- judge_auc(texts, labels) -> {auc, ...}: the chat model scores P(query is a synthetic probe)
  for each query; the ROC-AUC of those scores vs the true canary labels is how well a
  *reasoning* adversary separates probes from real tasks. AUC~0.5 => it cannot.

Both the generated canaries (keyed by qid) and the judge scores (keyed by query text) are
CACHED to disk, so re-runs are cheap, deterministic, and let us recompute the AUC offline
without re-calling the model. Judge work is bounded and class-balanced (JUDGE_CAP).
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

JUDGE_CAP = 200             # queries actually scored by the judge (subsampled, class-balanced)
# Concurrency must stay UNDER the deployment's rate limit, or requests get 429'd and (after the
# client's retries) come back empty -> silent fallback. 4 is safe for a sku-capacity~10 chat
# deployment; raise INDIST_WORKERS if you provisioned more capacity.
WORKERS = int(os.environ.get("INDIST_WORKERS", "4"))
HERE = os.path.dirname(os.path.abspath(__file__))
_GEN_CACHE = os.path.join(HERE, "gen_cache.json")     # qid -> generated canary question
_SCORE_CACHE = os.path.join(HERE, "judge_cache.json")  # query text -> P(probe)
_client = None

_GEN_SYS = ("You write short factual questions for a reading-comprehension benchmark. "
            "Given a target ANSWER, write ONE natural question, similar in style and length "
            "to typical such questions, whose correct answer is exactly that target. "
            "Output ONLY the question text, nothing else.")
_JUDGE_SYS = ("A stream of genuine user questions has had synthetic 'verification probe' "
              "questions secretly mixed in. For the given question, output ONLY a number in "
              "[0,1]: the probability it is a SYNTHETIC probe rather than a genuine user "
              "question. Output the number only.")


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(path: str, d: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f)


def _pmap(fn, items, label):
    """Parallel map over chat calls (I/O bound), in order, with coarse progress."""
    out = [None] * len(items)
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fn, x): i for i, x in enumerate(items)}
        for f in as_completed(futs):
            out[futs[f]] = f.result()
            done += 1
            if done % 50 == 0 or done == len(items):
                print(f"      {label} {done}/{len(items)}", flush=True)
    return out


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
            max_retries=8,      # exponential backoff on 429/5xx — the real cause of empty replies
            timeout=60.0,
        )
    return _client


def _ask(system: str, user: str, budget: int = 2000) -> str:
    """One chat turn, returning a NON-EMPTY reply or "".

    gpt-5/o-series are REASONING models: they spend the token budget on hidden reasoning and
    return EMPTY content if the cap is too small — which silently broke both the generator
    (fell back to the real question) and the judge (defaulted to 0.5). So we force
    `reasoning_effort=minimal` and a generous `max_completion_tokens`, fall back across API
    shapes (older models reject those params and want `max_tokens`), never pass a custom
    temperature, and accept only a non-empty reply."""
    dep = os.environ["AZURE_OPENAI_CHAT_DEPLOYMENT"]
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for kw in ({"max_completion_tokens": budget, "reasoning_effort": "minimal"},
               {"max_completion_tokens": budget},
               {"max_tokens": budget}):
        try:
            r = _chat().chat.completions.create(model=dep, messages=msgs, **kw)
            txt = (r.choices[0].message.content or "").strip()
            if txt:
                return txt
        except Exception:                           # noqa: BLE001 — try the next API shape
            continue
    return ""


# ------------------------------------------------------------------------- generator
def make_generator():
    def one(q: dict) -> str:
        try:
            return _ask(_GEN_SYS, f"ANSWER: {q['answer']}")
        except Exception:                           # noqa: BLE001
            return ""

    def gen(qa_items: list[dict]) -> list[str]:
        cache = _load(_GEN_CACHE)
        todo = [q for q in qa_items if q["qid"] not in cache]
        if todo:
            outs = _pmap(one, todo, "generated")
            nfb = sum(1 for o in outs if not o)
            for q, o in zip(todo, outs):
                cache[q["qid"]] = o if o else q["question"]   # echo the real Q only on failure
            if nfb:
                print(f"      WARNING: generator returned empty -> fell back to the REAL question "
                      f"for {nfb}/{len(todo)} ({100 * nfb / len(todo):.0f}%). Those canaries are "
                      f"NOT synthetic — the llm_generated result would be invalid. Raise the "
                      f"token budget / check reasoning_effort.", flush=True)
            _save(_GEN_CACHE, cache)
        return [cache[q["qid"]] for q in qa_items]
    return gen


# ----------------------------------------------------------------------------- judge
_NUM = re.compile(r"(?<![\d.])(0(?:\.\d+)?|1(?:\.0+)?|\.\d+)(?![\d])")


def _score_raw(text: str):
    """P(query is a synthetic probe). Extract the first [0,1] number anywhere in the reply
    (reasoning models may wrap the answer); return None if there is no parseable number, so the
    caller can count abstentions instead of silently treating them as a real 0.5."""
    m = _NUM.search(_ask(_JUDGE_SYS, text))
    return min(1.0, max(0.0, float(m.group(1)))) if m else None


def _auc(y_pos: np.ndarray, score: np.ndarray) -> float:
    """ROC-AUC via the rank (Mann-Whitney U) identity, AVERAGING tied ranks — essential here
    because the judge emits many identical scores; without it, ties resolve by index order and
    fabricate separation (e.g. a same-distribution control scoring ~0.86 instead of ~0.5)."""
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), float)
    ranks[order] = np.arange(1, len(score) + 1)
    s_sorted = score[order]
    i = 0
    while i < len(s_sorted):
        j = i
        while j + 1 < len(s_sorted) and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    npos, nneg = int(y_pos.sum()), int((y_pos == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    return (ranks[y_pos == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg)


def judge_auc(texts: list[str], labels: np.ndarray) -> dict:
    """labels: 1=real, 0=canary. AUC of P(probe) at separating canaries (positives). Subsample
    is CLASS-BALANCED; scores are cached by text so re-runs don't re-call the model."""
    labels = np.asarray(labels)
    rng = np.random.default_rng(0)
    per = JUDGE_CAP // 2
    real_idx = np.where(labels == 1)[0]
    can_idx = np.where(labels == 0)[0]
    if len(real_idx) > per:
        real_idx = rng.choice(real_idx, per, replace=False)
    if len(can_idx) > per:
        can_idx = rng.choice(can_idx, per, replace=False)
    idx = np.concatenate([real_idx, can_idx])
    sel = [texts[i] for i in idx]

    cache = _load(_SCORE_CACHE)
    todo = [t for t in dict.fromkeys(sel) if t not in cache]
    if todo:
        raw = _pmap(_score_raw, todo, "judged")
        ndef = sum(1 for s in raw if s is None)
        for t, s in zip(todo, raw):
            cache[t] = 0.5 if s is None else s
        if ndef:
            print(f"      WARNING: judge gave no parseable score for {ndef}/{len(todo)} "
                  f"({100 * ndef / len(todo):.0f}%) -> defaulted to 0.5.", flush=True)
        _save(_SCORE_CACHE, cache)
    scores = np.array([cache[t] for t in sel])
    is_canary = (labels[idx] == 0).astype(int)      # judge should give canaries high P(probe)
    return {"auc": round(_auc(is_canary, scores), 4), "n_scored": int(len(idx)),
            "n_real": int((is_canary == 0).sum()), "n_canary": int(is_canary.sum()),
            "mean_score_real": round(float(scores[is_canary == 0].mean()), 3),
            "mean_score_canary": round(float(scores[is_canary == 1].mean()), 3),
            "scores": [round(float(s), 3) for s in scores],
            "is_canary": [int(c) for c in is_canary]}

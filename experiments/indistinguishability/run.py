"""Level-B experiment: is a canary indistinguishable from a real task?

Moves canary indistinguishability from an *assumed* flag (paper §8.4) to a *measured*
property. We build real-vs-canary query sets in a QA domain (RAG) under several canary
CONSTRUCTION strategies and ask an adversary to tell them apart. The adversary's ROC-AUC is
the metric:

    AUC ~ 0.5  -> indistinguishable (the property holds for that construction)
    AUC -> 1.0 -> distinguishable   (the property fails; CCR's security would collapse, §8.4)

Two adversaries (both requested):
  1. EMBEDDING CLASSIFIER  - embed each query with text-embedding-3-small, cross-validated
     logistic regression, ROC-AUC + a label-permutation null (so a high-dim spurious split
     is not mistaken for a real one). Pure numpy; deterministic.
  2. LLM JUDGE             - a chat model scores P(query is a synthetic probe); AUC over its
     scores. Needs a chat deployment (see judge.py). Stronger, reasoning adversary.

Construction strategies (a distinguishability gradient grounded in real constructions):
  - marked        : explicit probe markers   -> expect AUC ~ 1.0   (trivially distinguishable)
  - templated     : synthetic keyword-bag     -> expect high AUC    (naive synthesis)
  - llm_generated : chat model paraphrases a real-style question with a known answer
                                              -> the key unknown: can synthesis be hidden?
  - dist_matched  : canary = a held-out REAL labelled question the router can grade
                                              -> expect AUC ~ 0.5   (the achievable reference)

Honest reading: dist_matched is ~0.5 *by construction* (same distribution) — it shows the only
provably-indistinguishable canary is a real task whose answer you happen to know. The question
the experiment answers is how close a *synthesized* canary (llm_generated) gets to that floor.

Run on a machine with Azure OpenAI access (the NUC):
    pip install -r requirements.txt
    export AZURE_OPENAI_EMBEDDINGS_ENDPOINT=...  AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT=text-embedding-3-small
    export AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o-mini   # optional, enables the LLM judge
    python run.py --squad data/dev-v1.1.json --n 400

Self-test the pipeline (no Azure, no dataset):  python run.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)  # so `ccrlib` (embeddings/auth) is importable when run from here

STRATEGIES = ["marked", "templated", "llm_generated", "dist_matched"]
_EMB_CACHE = os.path.join(HERE, "embcache.json")


# --------------------------------------------------------------------------- data
def load_qa(path: str, limit: int) -> list[dict]:
    """Load SQuAD-format JSON -> [{qid, question, answer, context}]. Robust to v1.1/v2.0."""
    with open(path, encoding="utf-8") as f:
        blob = json.load(f)
    out = []
    for art in blob["data"]:
        for para in art["paragraphs"]:
            ctx = para.get("context", "")
            for qa in para["qas"]:
                ans = qa.get("answers") or qa.get("plausible_answers") or []
                if not ans:
                    continue
                out.append({"qid": qa["id"], "question": qa["question"].strip(),
                            "answer": ans[0]["text"].strip(), "context": ctx})
                if len(out) >= limit:
                    return out
    return out


# ---------------------------------------------------------------- canary construction
_MARKERS = ["[verification probe] ", "(test query) ", "CANARY: ", "[automated check] "]
_TEMPLATE_SKILLS = {
    "rag": "retrieval grounding documents vector search citations knowledge",
    "planning": "decompose subtasks schedule plan steps goals orchestrate",
    "data_transform": "parse map reshape normalize csv json convert pipeline",
    "api_action": "invoke endpoint http rest action mutate side effect call",
}


def build_pair(qa: list[dict], strategy: str, n: int, llm_gen=None) -> tuple[list[str], np.ndarray]:
    """Return (texts, labels) — label 1 = real task, 0 = canary — balanced, n per class."""
    reals = [q["question"] for q in qa[:n]]
    if strategy == "dist_matched":          # canary = a DIFFERENT real labelled question
        canaries = [q["question"] for q in qa[n:2 * n]]
    elif strategy == "marked":              # real question + an explicit probe marker
        canaries = [_MARKERS[i % len(_MARKERS)] + q["question"] for i, q in enumerate(qa[n:2 * n])]
    elif strategy == "templated":           # synthetic keyword-bag (the lab's naive style)
        sk = list(_TEMPLATE_SKILLS.items())
        diff = ["easy", "medium", "hard"]
        canaries = [f"{diff[i % 3]} {sk[i % len(sk)][0]} task: {sk[i % len(sk)][1]}"
                    for i in range(n)]
    elif strategy == "llm_generated":       # chat model paraphrases to match the distribution
        if llm_gen is None:
            return [], np.array([])         # skipped when no chat deployment
        canaries = llm_gen([q for q in qa[n:2 * n]])
    else:
        raise ValueError(strategy)
    texts = reals + canaries
    labels = np.array([1] * len(reals) + [0] * len(canaries))
    return texts, labels


# ------------------------------------------------------------------------- embeddings
def _load_cache() -> dict:
    try:
        with open(_EMB_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def embed_texts(texts: list[str], batch: int = 64) -> np.ndarray:
    """Embed with the REAL text-embedding-3-small (reusing ccrlib's managed-identity auth),
    BATCHED (the embeddings API takes a list) and cached locally so the AUC is replayable.
    Requires AOAI to be configured."""
    from ccrlib import embeddings as E
    if not E._use_aoai():
        raise SystemExit("This run needs real embeddings. Set AZURE_OPENAI_EMBEDDINGS_ENDPOINT "
                         "and _DEPLOYMENT (a hash backend would separate by keywords trivially).")
    cache = _load_cache()
    todo = [t for t in dict.fromkeys(texts) if t not in cache]   # unique, not-yet-cached
    if todo:
        client = E._aoai_client()
        dep = os.environ["AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT"]
        for i in range(0, len(todo), batch):
            chunk = todo[i:i + batch]
            resp = client.embeddings.create(model=dep, input=chunk)
            for t, d in zip(chunk, resp.data):
                cache[t] = list(d.embedding)
            print(f"    embedded {min(i + batch, len(todo))}/{len(todo)} new", flush=True)
        with open(_EMB_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    return np.asarray([cache[t] for t in texts], dtype=float)


# ------------------------------------------------------ adversary 1: embedding classifier
def _auc(y: np.ndarray, score: np.ndarray) -> float:
    """ROC-AUC via the rank (Mann-Whitney U) identity; average-ranks for ties."""
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), float)
    ranks[order] = np.arange(1, len(score) + 1)
    # average tied ranks
    s_sorted = score[order]
    i = 0
    while i < len(s_sorted):
        j = i
        while j + 1 < len(s_sorted) and s_sorted[j + 1] == s_sorted[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    npos, nneg = int((y == 1).sum()), int((y == 0).sum())
    if npos == 0 or nneg == 0:
        return float("nan")
    return (ranks[y == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg)


def _logreg_fit(X, y, l2, iters=250, lr=0.5):
    n, d = X.shape
    w = np.zeros(d); b = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(X @ w + b, -30, 30)))
        g = p - y
        w -= lr * (X.T @ g / n + l2 * w / n)
        b -= lr * g.mean()
    return w, b


def cv_auc(X: np.ndarray, y: np.ndarray, folds=5, l2=1.0, seed=0) -> float:
    """Out-of-fold ROC-AUC of an L2 logistic regression (standardized per train fold, so no
    leakage). Cross-validation is what keeps a 1536-dim classifier honest with few samples."""
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(y))
    scores = np.zeros(len(y))
    for f in range(folds):
        te = idx[f::folds]
        tr = np.setdiff1d(idx, te, assume_unique=False)
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-8
        w, b = _logreg_fit((X[tr] - mu) / sd, y[tr].astype(float), l2)
        scores[te] = (X[te] - mu) / sd @ w + b
    return _auc(y, scores)


def _pca(X: np.ndarray, k: int) -> np.ndarray:
    """Reduce to the top-k principal components (unsupervised, label-free -> no leakage). Keeps
    the adversary a clean *linear probe on the dominant directions* and makes the per-fold
    logistic regression well-posed (n >> p) and ~12x faster than full 1536-dim."""
    k = min(k, X.shape[1], X.shape[0] - 1)
    Xc = X - X.mean(0)
    _, _, Vt = np.linalg.svd(Xc, full_matrices=False)
    return Xc @ Vt[:k].T


def classifier_verdict(X, y, perms=100, seed=0, k=128) -> dict:
    """Observed CV-AUC plus a label-PERMUTATION null: shuffle labels, recompute CV-AUC. If the
    observed AUC is inside the null band, the two sets are statistically indistinguishable to
    this adversary (any apparent split was high-dim noise). Classifier = L2 logistic regression
    on the top-k principal components of the embeddings."""
    Xr = _pca(X, k)
    obs = cv_auc(Xr, y, seed=seed)
    rng = np.random.default_rng(seed + 1)
    null = np.array([cv_auc(Xr, rng.permutation(y), seed=seed) for _ in range(perms)])
    p = float((null >= obs).mean())            # one-sided: is obs above the null?
    return {"auc": round(obs, 4), "pca_k": int(min(k, X.shape[1], X.shape[0] - 1)),
            "null_mean": round(float(null.mean()), 4), "null_std": round(float(null.std()), 4),
            "p_value": round(p, 4),
            "distinguishable": bool(obs > null.mean() + 2 * null.std() and p < 0.05)}


# ------------------------------------------------------------- orchestration / output
def run(squad_path: str, n: int, perms: int) -> dict:
    qa = load_qa(squad_path, limit=2 * n + 5)
    if len(qa) < 2 * n:
        raise SystemExit(f"Need >= {2 * n} QA items, got {len(qa)} from {squad_path}.")
    llm_gen = None
    try:
        from judge import make_generator, judge_auc           # noqa: E402
        if os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT"):
            llm_gen = make_generator()
    except Exception as e:                                    # noqa: BLE001
        print(f"[judge] LLM judge/generator unavailable: {e}")
        judge_auc = None

    results = {}
    use_judge = bool(os.environ.get("AZURE_OPENAI_CHAT_DEPLOYMENT") and judge_auc)
    for strat in STRATEGIES:
        print(f"\n[{strat}] building query sets ({n}/class)...", flush=True)
        texts, y = build_pair(qa, strat, n, llm_gen=llm_gen)
        if not texts:
            print(f"[{strat}] skipped (needs chat deployment for llm_generated).", flush=True)
            continue
        print(f"[{strat}] embedding {len(texts)} queries (batched)...", flush=True)
        X = embed_texts(texts)
        print(f"[{strat}] embedding classifier + {perms}-permutation null...", flush=True)
        emb = classifier_verdict(X, y, perms=perms)
        row = {"n_per_class": n, "embedding_classifier": emb}
        if use_judge:
            print(f"[{strat}] LLM judge (parallel)...", flush=True)
            row["llm_judge"] = judge_auc(texts, y)
        results[strat] = row
        j = row.get("llm_judge", {}).get("auc", "-")
        print(f"[{strat:13}] emb-AUC={emb['auc']:.3f} (null {emb['null_mean']:.3f}±{emb['null_std']:.3f}, "
              f"p={emb['p_value']:.3f}, {'DISTINGUISHABLE' if emb['distinguishable'] else 'indistinguishable'})"
              f" | judge-AUC={j}", flush=True)
    out = os.path.join(HERE, "results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote {out}")
    figure(results)
    return results


def figure(results: dict):
    """Bar chart of adversary AUC per construction strategy (0.5 = indistinguishable)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:                                # noqa: BLE001
        return
    strat = [s for s in STRATEGIES if s in results]
    if not strat:
        return
    emb = [results[s]["embedding_classifier"]["auc"] for s in strat]
    has_judge = any("llm_judge" in results[s] for s in strat)
    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    x = np.arange(len(strat)); w = 0.38
    ax.bar(x - (w / 2 if has_judge else 0), emb, w if has_judge else 0.6, label="embedding classifier")
    if has_judge:
        jud = [results[s].get("llm_judge", {}).get("auc", np.nan) for s in strat]
        ax.bar(x + w / 2, jud, w, label="LLM judge")
    ax.axhline(0.5, color="gray", ls="--", lw=1)
    ax.text(len(strat) - 0.5, 0.508, "indistinguishable (0.5)", ha="right", va="bottom",
            fontsize=8, color="gray")
    ax.set_xticks(x, strat, rotation=12)
    ax.set_ylim(0.45, 1.03); ax.set_ylabel("adversary ROC-AUC")
    ax.set_title("Canary indistinguishability by construction strategy")
    ax.legend(fontsize=8, loc="upper right")
    p = os.path.join(HERE, "fig_indistinguishability.png")
    fig.savefig(p, dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"Wrote {p}")


# --------------------------------------------------------------------------- self-test
def selftest():
    rng = np.random.default_rng(0)
    # (a) two random halves of one distribution -> AUC ~ 0.5, indistinguishable
    X = rng.normal(size=(400, 64)); y = np.array([1] * 200 + [0] * 200)
    same = classifier_verdict(X, y, perms=100)
    # (b) a real shift in 3 dims -> AUC ~ 1.0, distinguishable
    X2 = X.copy(); X2[y == 0, :3] += 3.0
    diff = classifier_verdict(X2, y, perms=100)
    # (c) canary construction shapes
    qa = [{"qid": str(i), "question": f"What year did event {i} happen?",
           "answer": str(1900 + i), "context": ""} for i in range(20)]
    tm, _ = build_pair(qa, "marked", 5)
    td, yd = build_pair(qa, "dist_matched", 5)
    assert same["auc"] == same["auc"] and 0.40 <= same["auc"] <= 0.60, same
    assert diff["auc"] > 0.95 and diff["distinguishable"], diff
    assert tm[5].startswith("[verification probe]") and td[5].startswith("What year"), (tm[5], td[5])
    assert _auc(np.array([1, 1, 0, 0]), np.array([0.9, 0.8, 0.2, 0.1])) == 1.0
    print("selftest OK:")
    print(f"  same-distribution -> AUC {same['auc']} (null {same['null_mean']}±{same['null_std']}, "
          f"{'DISTINGUISHABLE' if same['distinguishable'] else 'indistinguishable'})")
    print(f"  shifted 3 dims    -> AUC {diff['auc']} ({'DISTINGUISHABLE' if diff['distinguishable'] else 'indistinguishable'})")
    print("  canary shapes + AUC rank identity OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--squad", help="path to a SQuAD-format JSON (e.g. dev-v1.1.json)")
    ap.add_argument("--n", type=int, default=400, help="queries per class (real / canary)")
    ap.add_argument("--perms", type=int, default=100, help="label permutations for the null")
    ap.add_argument("--selftest", action="store_true", help="verify the pipeline (no Azure/data)")
    a = ap.parse_args()
    if a.selftest:
        selftest(); return
    if not a.squad:
        ap.error("--squad is required (or use --selftest). See the module docstring for setup.")
    run(a.squad, a.n, a.perms)


if __name__ == "__main__":
    main()

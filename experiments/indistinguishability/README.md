# Level-B: is a canary *measurably* indistinguishable from a real task?

The paper treats canary **indistinguishability** as an exogenous flag (§8.4) and names
*constructing a provably-indistinguishable canary against an adaptive adversary* as the
central open problem (§10). This experiment turns that flag into a **measurement**: it builds
real-vs-canary query sets in a QA (RAG) domain under several canary **construction
strategies** and asks an adversary to tell them apart. The adversary's **ROC-AUC** is the
metric:

- **AUC ≈ 0.5** → indistinguishable (the property holds for that construction)
- **AUC → 1.0** → distinguishable (it fails — CCR's security would collapse, §8.4)

## Two adversaries

1. **Embedding classifier** — embed each query with `text-embedding-3-small`, cross-validated
   L2 logistic regression, ROC-AUC, plus a **label-permutation null** so a high-dimensional
   spurious split is not mistaken for a real one. Deterministic, pure NumPy.
2. **LLM judge** — a chat model scores `P(query is a synthetic probe)`; AUC over those scores.
   A *reasoning* adversary. Needs a chat deployment (`judge.py`).

## Construction strategies (the distinguishability gradient)

| strategy | canary is… | expectation |
|---|---|---|
| `marked` | a real question + an explicit probe marker | AUC → 1.0 (trivially distinguishable) |
| `templated` | a synthetic keyword-bag query (the lab's naive style) | high AUC |
| `llm_generated` | a chat-model question matched to the real style, with a known answer | **the key unknown** |
| `dist_matched` | a held-out **real** labelled question the router can grade | AUC ≈ 0.5 (achievable reference) |

**Honest reading.** `dist_matched` is ≈0.5 *by construction* — it shows the only
provably-indistinguishable canary is a real task whose answer you happen to know (i.e. you
need labelled real traffic). The experiment's real question is **how close a *synthesized*
canary (`llm_generated`) gets to that 0.5 floor** while still being side-effect-free and
gradeable. If it stays well above 0.5, that is an honest negative that *bounds* the property.

## Run (on a machine with Azure OpenAI access — e.g. the NUC)

```bash
pip install -r requirements.txt

# 1. Get a public QA dataset (real questions + known answers). SQuAD v1.1 dev:
curl -o data/dev-v1.1.json https://rajpurkar.github.io/SQuAD-explorer/dataset/dev-v1.1.json

# 2. Embeddings (same resource as the embedding baseline):
export AZURE_OPENAI_EMBEDDINGS_ENDPOINT="https://<your-aoai>.openai.azure.com/"
export AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT="text-embedding-3-small"

# 3. (optional, enables adversary #2) a chat deployment on the same resource:
export AZURE_OPENAI_CHAT_DEPLOYMENT="gpt-4o-mini"

python run.py --squad data/dev-v1.1.json --n 400
```

Outputs `results.json` (AUC + permutation null per strategy/adversary) and
`fig_indistinguishability.png`. Without a chat deployment, `llm_generated` is skipped and the
LLM judge is omitted; the embedding classifier still runs on the other three strategies.

Verify the pipeline with no Azure and no dataset:

```bash
python run.py --selftest
```

## Notes

- The embedding step caches vectors to `embcache.json` (committable → replayable AUC).
- The embedding classifier **needs real embeddings**; with the hash backend it would separate
  by keywords trivially, so the script refuses to run without `AZURE_OPENAI_EMBEDDINGS_*`.
- LLM calls are bounded (`JUDGE_CAP` in `judge.py`); raise `--n` for a stronger classifier,
  the judge subsamples to stay cheap.
- Feeds paper §8.4 / §10: an AUC of *X* corresponds to an adversary that recognises the probe
  with probability ~*X*, which maps onto the simulation's distinguishability axis.
```

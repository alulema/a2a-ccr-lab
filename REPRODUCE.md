# Reproducing the paper's numbers

Two layers, by design:

1. **Hermetic core (no Azure, no network, dependency-free).** Reproduces every result
   **except** the `embedding` baseline *bit-for-bit*, deterministically, on any machine.
   This covers all of the paper's central claims (CCR vs attestation / optimistic / card /
   random; RQ1–RQ4; the λ_r and Pareto analyses).
2. **Real-embeddings layer (optional, needs Azure OpenAI).** Re-computes only the
   `embedding` baseline with real `text-embedding-3-small` vectors. Deterministic given the
   pinned model snapshot.

Everything is a pure function of `(run_seed, task, agent)` — no `random`, no wall-clock (see
`ccrlib/domain.det_unit`) — so the in-process run and the Azure A2A run produce identical
numbers; the cloud is a *distributed validation*, not a separate result.

---

## 0. Environment

- **Python 3.11+** (the cloud Functions run 3.11).
- **Hermetic core:** *no third-party packages required.* `ccrlib` is standard-library
  only; `scripts/local_run.py`, `analysis/analyze.py`, `analysis/lambda_r_sweep.py` are too.
- **Real-embeddings layer only:** `pip install azure-identity openai`.
- The dataset is regenerated deterministically by `local_run.py` (claim_gap = 1.0); no
  seeds or data files need to be downloaded.

---

## 1. Hermetic core — the headline numbers (≈ 1–2 min)

```bash
python scripts/local_run.py --seeds 6 --tasks 24    # full 120-cell factorial, n=144/cell
python analysis/analyze.py --ci                     # summaries, RQ2 winners, Pareto, 95% CIs
python analysis/lambda_r_sweep.py                   # incident-pricing crossover
```

Writes (all reproducible, hence git-ignored): `data/results/raw.jsonl` (103,680 runs),
`analysis/EVIDENCE.md` (RQ1–RQ4 tables), and timestamped
`analysis/{summary,rq2_costbenefit,pareto,significance,lambda_r_*}_<ts>.csv`.

### Expected headline (with the hermetic hash-embedding backend)

- **RQ1** (representative cell `V5_k0.2_mixed_I`): `ccr` success **0.944**, incident
  **0.139**, net **2.35**; `optimistic_fallback` 0.972 / **0.722** / 1.42;
  `attestation_only` = `card_only` 0.347 / 0.451 / −0.17; `random` 0.514 / 0.410 / 0.75.
  (Hash `embedding`: ≈ 0.52 / 0.083 / 1.44 — see §2 for the real-vector value.)
- **RQ2:** `RQ2: CCR is the net-utility winner in 7/20 (V, kappa) cells.`
- **High-value region** (V≥5, κ≤0.2, hostile/mixed, I): CCR wins **12/12** at λ_r=2;
  **paired significance 10/12**.
- **RQ4:** distinguishable probe → defection **0.854**, incident **1.000**, success 0.812;
  indistinguishable → **0.111 / 0.139 / 0.944**.
- **λ_r:** 4 caveat cells (all vs `optimistic_fallback`), flip to CCR at λ_r* ∈ **[2.45,
  3.35]**.
- **Pareto frontier:** `optimistic_fallback` 112/120, `embedding` 80/120, `ccr` 50/120;
  `attestation_only` = `card_only` **0/120** (dominated).

> **Which figures need §2 (real embeddings) to match exactly.** The numbers above are the
> paper's, i.e. with real embeddings. §1 alone (hermetic, hash embeddings) reproduces every
> *embedding-independent* quantity bit-for-bit — `ccr`, `optimistic_fallback`,
> `attestation_only`, `card_only`, `random` (so all of RQ1 except `embedding`'s row, RQ3,
> RQ4, and the high-value 12/12 + significance). The *embedding-dependent* aggregates — the
> RQ2 winner count (7/20), the Pareto counts, and the λ_r caveat-cell count — depend on how
> strong the `embedding` competitor is, so they match the paper only after §2. With the hash
> backend they are close but not identical (e.g. CCR wins more RQ2 cells because the hash
> `embedding` is weaker). Run §1 **then** §2 for the paper's exact figures.

## 1b. Robustness sweep (hardens RQ2 against "you calibrated to win")

```bash
python scripts/sensitivity.py --seeds 6 --tasks 24
```

Writes `analysis/SENSITIVITY.md` + `analysis/sensitivity.csv`. Includes the `claim_gap`
sweep (CCR's advantage should grow monotonically with card unreliability and vanish in an
honest world) and the decision-coupled `lambda_r` sweep.

---

## 2. Real Azure OpenAI embeddings (optional — only the `embedding` baseline)

Needed only to reproduce the `embedding` baseline with real semantic vectors. The
subscription must have **Azure OpenAI access + `text-embedding-3-small` quota** in the
region.

```bash
pip install azure-identity openai

# 2.1 Provision AOAI (idempotent; also (re)deploys the Function Apps). If a previous AOAI
#     account is soft-deleted, purge it first:
#     az cognitiveservices account purge -l eastus -g a2a-ccr-lab -n oai-<token>
bash scripts/deploy.sh eastus

# 2.2 Grant YOUR az identity data-plane access (Bicep only grants the router's identity):
ME=$(az ad signed-in-user show --query id -o tsv)
OAI=$(az cognitiveservices account list -g a2a-ccr-lab --query "[0].id" -o tsv)
az role assignment create --assignee "$ME" --role "Cognitive Services OpenAI User" --scope "$OAI"
# (RBAC can take 1–2 min to propagate.)

# 2.3 Point ccrlib at AOAI (DefaultAzureCredential uses your `az login`) and re-run:
export AZURE_OPENAI_EMBEDDINGS_ENDPOINT="$(az cognitiveservices account list -g a2a-ccr-lab --query '[0].properties.endpoint' -o tsv)"
export AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT="text-embedding-3-small"
python -c "from ccrlib import embeddings; print(embeddings.backend())"   # must print: azure_openai
python scripts/local_run.py --seeds 6 --tasks 24
python analysis/analyze.py --ci
python analysis/lambda_r_sweep.py
```

With real embeddings, the `embedding` baseline becomes **success ≈ 0.556, incident
0.000** in `V5_k0.2_mixed_I` (vs hash ≈ 0.52 / 0.083): real semantic routing steers away
from the crudely keyword-stuffed adversarial cards. To return to the hermetic backend,
`unset AZURE_OPENAI_EMBEDDINGS_ENDPOINT AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT`.

---

## 3. Distributed validation over real A2A (optional)

Shows the distributed A2A path reproduces the in-process numbers. The cloud router runs a
representative **10-cell subset** (`_VALIDATION` in `src/router/function_app.py`) that fits
under the ~230 s synchronous-POST gateway limit — the full 120-cell factorial is the
in-process job of §1.

```bash
bash scripts/deploy.sh eastus            # provisions infra + publishes both Function Apps
bash scripts/run_experiment.sh           # POSTs the validation subset, prints aggregated rows
az group delete --name a2a-ccr-lab --yes # tear everything down when done
```

---

## 4. Determinism, environment, and gotchas

- **Determinism:** the hermetic core is bit-identical across machines/OS. The real
  `embedding` baseline is deterministic only for the **pinned model** (`text-embedding-3-
  small`, api-version `2024-10-21`); record the model version with the run.
- **Windows / WSL checkouts:** set `git config core.autocrlf false` and `core.filemode
  false` (a committed `.gitattributes` enforces LF). Without this the whole tree shows as
  modified — it is line-ending / exec-bit noise, not content.
- **Generated artifacts** (`analysis/*_<ts>.csv`, `data/results/`) are git-ignored and
  reproduced by the commands above; only code, infra, and `paper/` are versioned.
- **Calibration (state when citing):** the agent pool is synthetically calibrated at
  `claim_gap = 1.0` so declared capability is a poor signal (the paper's premise). §1b's
  sweep shows the conclusions are not a calibration artifact. The `embedding` baseline's
  zero-incident result is contingent on adversaries being *semantically* separable (naive
  keyword-stuffing); CCR's capability-probe is invariant to card wording.

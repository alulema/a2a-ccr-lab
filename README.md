# a2a-ccr-lab — Evidence harness for Card+Canary Routing (CCR) in A2A

Reproducible benchmark + Azure reference deployment that produces the empirical
evidence for the paper *"When Is It Worth Verifying? Cost-Aware, Hybrid Capability
Routing for A2A Agent Ecosystems"* (see `../paper/Paper.A2A.v0.2.md`).

It answers the paper's four research questions with hard numbers:

- **RQ1 — Effectiveness.** CCR vs five baselines on heterogeneous tasks.
- **RQ2 — When does verifying pay off?** Net-utility cost/benefit across task value
  `V` and canary cost `κ` (the headline contribution, C2).
- **RQ3 — Robustness** to honest-but-degraded agents with stale cards.
- **RQ4 — Canary-aware defection** and the mitigation effect of indistinguishable
  probes (C3 — the case prior "lazy peer" work does not model).

## Why you can trust the numbers

1. **The router never sees ground truth.** Routing policies (`ccrlib/policies.py`)
   interact with agents only through a `transport(agent_id, request)` callable and
   verify success by comparing returned outputs to a known answer. They cannot read
   an agent's hidden capability or persona. Ground truth is used only by the
   *evaluator* (`ccrlib/metrics.py`) to label incidents/defections after the fact.
2. **Everything is deterministic.** No `random`/wall-clock — outcomes are a pure
   function of `(run_seed, task, agent)`. The local run and the Azure run produce
   identical numbers; the cloud is a *distributed validation*, not a separate result.
3. **Success is verified, never self-reported.** Agents return outputs; the router
   checks them. A lying agent cannot claim success it didn't earn.

## Layout

```
ccrlib/            Pure-Python scientific core (no Azure deps)
  domain.py        skills, deterministic RNG, verifiable tasks, signatures, hash embedding
  embeddings.py    embedding provider — real Azure OpenAI vectors (env) or hash fallback
  scenario.py      parameterized world builder (claim_gap, persona mix) — sweep axis
  agents.py        SimAgent: HIDDEN capability model + 4 personas (router never imports)
  scoring.py       card/canary/risk scores, value-of-information, expected utility
  policies.py      CCR + 5 baselines (random, card_only, embedding, attestation_only,
                   optimistic_fallback)
  metrics.py       evaluation + aggregation (side-effect incident / defection labeling)
  experiment.py    grid driver (transport-agnostic: in-process or HTTP)
src/agents/        Azure Function App — A2A agent host (well-known card + JSON-RPC)
src/router/        Azure Function App — CCR router / experiment harness over A2A HTTP
infra/             Bicep: Flex Consumption Functions, Storage, App Insights, RBAC
scripts/           gen_dataset, local_run, sensitivity, sync_lib, seed_data, run_experiment
analysis/          analyze.py (+ --ci), lambda_r_sweep.py -> CSVs; EVIDENCE/SENSITIVITY.md
data/              tasks.json, agents.json, results/
```

## Reproduce the evidence

Full step-by-step, expected numbers, and the optional real-embeddings / distributed-cloud
paths live in **[`REPRODUCE.md`](REPRODUCE.md)**. The hermetic core needs no Azure and no
third-party packages:

```bash
python scripts/local_run.py --seeds 6 --tasks 24    # full 120-cell factorial, n=144/cell
python analysis/analyze.py --ci                      # summaries, RQ2 winners, Pareto, 95% CIs
python analysis/lambda_r_sweep.py                    # incident-pricing crossover
python scripts/sensitivity.py --seeds 6 --tasks 24   # robustness sweep (RQ2 hardening)
```

Generated outputs (`analysis/*_<ts>.csv`, `data/results/`, `analysis/EVIDENCE.md`) are
git-ignored and recreated by the commands above. The paper's written analysis is
`paper/evidence-section.md`; the engineering log is `Devlog.md`.

### The incident model (why probing beats retrying)

The hard objection to the whole paper: *if a real execution is cheap and retryable,
why probe at all — just retry on failure (`optimistic_fallback`)?* The answer is the
**side-effect asymmetry**: a real A2A call ships your task and data to the agent, so
invoking a malicious agent causes harm **at invocation time, whether or not it later
"succeeds"**. A canary uses synthetic data and is side-effect-free. So
`optimistic_fallback` *executes* the liar before falling back (incident); CCR *probes*
it safely (no incident). An incident is therefore charged for **any real execution
that touches a malicious agent** (`ccrlib/metrics.py`).

### Headline result (full 120-cell factorial, real embeddings)

- **RQ1** (`V5_k0.2_mixed_I`): CCR is the only policy high on **both** axes — success
  **0.944**, incident **0.139**. `optimistic_fallback` recovers success (0.97) but
  *executes* liars (incident **0.72**); `attestation_only`/`card_only` collapse (0.35 /
  0.45) — a verified identity gives no signal to separate a signed defector from an honest
  agent; real `embedding` is the safest baseline (incident **0.00**) but completes only
  **0.56**.
- **RQ2:** CCR wins the high-value region (V≥5, κ≤0.2) **12/12** at λ_r=2, significant in
  **10/12** (paired bootstrap); it concedes low-value / expensive-probe cells, honestly.
- **RQ3 (degradation):** CCR robust (0.99) vs card/attestation (0.56), which pick the
  stale-card degraded agent and fail.
- **RQ4 (defection):** indistinguishable probes cut canary-aware defection 0.85 → **0.11**
  (incident 1.00 → **0.14**).

Full tables, confidence intervals, the λ_r crossover and the Pareto view are in
`paper/evidence-section.md`; exact reproduction numbers in `REPRODUCE.md`.

### Sensitivity sweep — `python scripts/sensitivity.py`

Hardens RQ2 against "you calibrated to win": it reports CCR's net-utility advantage over
the best baseline in the high-stakes region (V≥5, κ≤0.2) as the world and the method's
hyperparameters (`claim_gap`, `malice`, `alpha`, `lambda_c`, `lambda_r`, `voi_threshold`)
vary. The decisive test is **`claim_gap`**: CCR's advantage grows *monotonically* with
card unreliability and ~vanishes in an honest world (gap=0) — so the win comes from cards
being unreliable, not from a hand-tuned calibration. Tables and the robustness verdict are
written to `analysis/SENSITIVITY.md`.

> These are simulation numbers from a calibrated agent pool; the sweep shows the
> conclusions are not a calibration artifact. State the calibration when citing (see
> "Threats to validity").

## Deploy to Azure (distributed A2A validation)

`bash scripts/deploy.sh eastus` provisions the full stack and publishes both Function Apps;
`bash scripts/run_experiment.sh` then runs the validation subset over real A2A HTTP. Full
steps — including the AOAI / real-embeddings path and teardown — are in
[`REPRODUCE.md`](REPRODUCE.md) §2–§3. Provisions (see `infra/`):

- **Storage** (keyless — managed identity only) with `data` + `results` containers.
- **App Insights + Log Analytics** for tracing routing decisions and canary calls.
- Two **Function Apps**, each on its own **Flex Consumption** plan:
  - `agents` — hosts every persona at `/agents/{id}/.well-known/agent-card.json` and
    `/agents/{id}/a2a` (JSON-RPC).
  - `router` — discovers cards over HTTP and runs the policy suite via real A2A calls.
- **Azure OpenAI** (`text-embedding-3-small`) backing the real-embedding baseline.
- **RBAC**: managed-identity Storage Blob Data Owner + Queue Data Contributor + Cognitive
  Services OpenAI User. No account keys anywhere.

Validate the Bicep before deploying: `az bicep build --file infra/main.bicep`.

## Scaling to "irrefutable" size

- **More agents:** raise `CORRECT_PER_SKILL`/add personas in `scripts/gen_dataset.py`,
  or add skills to `ccrlib.domain.SKILLS`. Infra and policies pick them up unchanged.
- **More tasks/seeds:** `--tasks`/`--seeds` on `local_run.py`.
- **Wider grid:** edit `VALUES`/`KAPPAS`/`POOLS` in `scripts/local_run.py`.

## Threats to validity (read before citing the numbers)

- The agent pool is **synthetically calibrated** so that declared capability is a
  poor signal (the paper's premise). Report this and run a sensitivity sweep over
  claim gaps, persona mix, and `α/λ_R/λ_C/τ`.
- Canary representativeness vs cost is modeled abstractly via `κ` and the
  distinguishability flag; a real deployment must measure actual probe cost.
- Success is a verifiable synthetic function; real tasks need task-specific oracles.
- The `embedding` baseline's zero-incident result is contingent on adversaries being
  *semantically* separable (naive keyword-stuffing); a sophisticated adversary with a
  natural description would defeat semantic routing — but not CCR's capability-probe,
  which is invariant to card wording.

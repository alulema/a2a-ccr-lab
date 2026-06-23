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
  domain.py        skills, deterministic RNG, verifiable tasks, signatures, embeddings
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
analysis/          analyze.py -> CSVs; EVIDENCE.md + SENSITIVITY.md (generated tables)
data/              tasks.json, agents.json, results/
```

## Run the evidence locally (no Azure, ~10s)

```bash
python scripts/local_run.py --seeds 3 --tasks 48   # RQ1-RQ4 headline grid
python analysis/analyze.py                          # CSV summaries
python scripts/sensitivity.py --seeds 3 --tasks 36  # robustness sweep (RQ2 hardening)
```

Outputs `analysis/EVIDENCE.md` (RQ1–RQ4 tables), `analysis/SENSITIVITY.md` (sweep),
`data/results/raw.jsonl`, and `analysis/summary.csv` + `analysis/rq2_costbenefit.csv`
+ `analysis/sensitivity.csv`.

### The incident model (why probing beats retrying)

The hard objection to the whole paper: *if a real execution is cheap and retryable,
why probe at all — just retry on failure (`optimistic_fallback`)?* The answer is the
**side-effect asymmetry**: a real A2A call ships your task and data to the agent, so
invoking a malicious agent causes harm **at invocation time, whether or not it later
"succeeds"**. A canary uses synthetic data and is side-effect-free. So
`optimistic_fallback` *executes* the liar before falling back (incident); CCR *probes*
it safely (no incident). An incident is therefore charged for **any real execution
that touches a malicious agent** (`ccrlib/metrics.py`).

### What the current run shows (seeds=3, tasks=48)

- **RQ1 (mixed pool, V=5, κ=0.2):** CCR has the best net utility (**2.53**) at low
  incident rate (**0.09**). `card_only`/`attestation_only` are fooled by a
  trusted-signed liar (success **0.51**). `optimistic_fallback` recovers success
  (0.96) but **executes liars** → incident rate **0.49**. *Attestation alone is not
  enough; cheap retry is not safe.*
- **RQ2:** CCR is the net-utility winner in **8/20** `(V, κ)` cells — high task value
  and cheap probes. It loses when κ is large or V is low: verifying can cost more than
  it saves. **This boundary is the paper's central, defensible result.**
- **RQ3 (degradation):** CCR robust vs card/attestation routing (which pick the
  stale-card degraded agent and fail).
- **RQ4 (defection):** a canary-aware defector beats a *distinguishable* probe in
  **87.5%** of runs (incident 0.99); making the probe **indistinguishable drops
  defection to 5.6%** (incident 0.09) and lifts success 0.76 → 0.95.

### Sensitivity sweep — `python scripts/sensitivity.py`

Hardens RQ2 against "you calibrated to win". Reports CCR's net-utility advantage over
the best baseline in the **high-stakes region** (V≥5, κ≤0.2) as the world and the
method's hyperparameters vary (`analysis/SENSITIVITY.md`, `analysis/sensitivity.csv`):

- **claim_gap (decisive):** CCR's region advantage grows **monotonically** with card
  unreliability — from **−0.59 in an honest world (gap=0)** to **+0.72 with region
  win-rate 1.0 (gap=1)**. CCR wins *because cards are unreliable*, not because of a
  hand-tuned calibration; in an honest world it isn't needed and barely hurts.
- **malice:** advantage grows with hostility, non-monotonically — the *threat type*
  matters: a bad-signature liar is filtered cheaply by attestation; only the
  trusted-signed defector needs the canary, and that's where CCR pulls ahead.
- **alpha:** region win-rate is **1.0 across α ∈ [0.1, 0.9]** — robust to its main
  hyperparameter.
- **lambda_c / voi_threshold:** behave as designed (cost price and probe-coverage
  trade-offs). A *prior floor of doubt* in the VoI gate (`voi_prior_doubt`) is what
  stops a confident-looking liar (card≈0.99) from evading verification.

> These are simulation numbers from a calibrated agent pool. They demonstrate the
> *mechanism and the regimes*; the sweep shows the conclusions are not a calibration
> artifact. The paper must still state the calibration (see "Threats to validity").

## Deploy to Azure

Prerequisites: `azd`, `az`, an Azure subscription, Python 3.11.

```bash
azd auth login
azd up                 # provisions infra, vendors ccrlib, deploys both Function Apps
                       # postprovision seeds data/*.json into the 'data' container
pwsh scripts/run_experiment.ps1 -Seeds 2 -Tasks 12
```

`azd up` provisions (see `infra/`):
- **Storage** (keyless — managed identity only) with `data` + `results` containers.
- **App Insights + Log Analytics** for tracing routing decisions and canary calls.
- A shared **Flex Consumption** plan and two **Function Apps**:
  - `agents` — hosts every persona at `/agents/{id}/.well-known/agent-card.json` and
    `/agents/{id}/a2a` (JSON-RPC).
  - `router` — discovers cards over HTTP and runs the policy suite via real A2A calls.
- **RBAC**: each app's managed identity gets Storage Blob Data Owner + Queue Data
  Contributor. No account keys anywhere.

Validate the Bicep before deploying:
```bash
az bicep build --file infra/main.bicep
```

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

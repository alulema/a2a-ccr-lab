# Devlog — a2a-ccr-lab

Engineering journal of the **distributed** evidence harness (router + agent host as
separate Azure Functions, real A2A HTTP between them). Records difficulties hit while
bringing the cloud deployment up, their root causes, and — where relevant — the
**implication for the paper** (reproducibility, threats to validity, and the
operational realities of running A2A capability-routing over a real network).

The local, in-process path (`scripts/local_run.py`) was already deterministic and
green; everything below is specific to the *distributed* validation, which is the
whole point of the cloud lab (proving the numbers hold over real A2A transport, not
just in a single process).

---

## 2026-06-23 — Bringing up the distributed run on Flex Consumption

### D1 — Deployment outputs read as `null` → published to an app named "null"
- **Symptom:** `deploy.sh` printed `agents=null router=null`, then
  `func azure functionapp publish` failed with `Can't find app with name "null"`.
- **Root cause:** ARM returns `properties.outputs` **keys in lowercase**. The script
  read them with their declared casing (`.SERVICE_AGENTS_NAME.value`), so every lookup
  missed and `jq -r` yielded the string `"null"`.
- **Resolution:** normalize keys before reading —
  `jq 'with_entries(.key |= ascii_downcase)'` — and read `.service_agents_name.value`.
  Added a guard that aborts with a clear message if outputs come back null instead of
  blindly publishing to `null` (commit `59a696c`).
- **Implication for paper:** a one-line, silent casing mismatch produced a failure two
  steps downstream with a misleading message. Worth a sentence in *reproducibility*:
  deployment scripts should **fail fast and validate** infra outputs, not propagate
  empty values into later stages.

### D2 — `func publish` can't determine project language
- **Symptom:** `Can't determine project language from files` /
  `Worker runtime cannot be 'None'`.
- **Root cause:** `local.settings.json` is gitignored and absent on a fresh clone, so
  Core Tools had no `FUNCTIONS_WORKER_RUNTIME` to infer Python from. The v2 project
  files alone were not enough for auto-detection in this toolchain version.
- **Resolution:** pass the runtime explicitly — `func azure functionapp publish <app>
  --python` — in `deploy.sh`.
- **Implication for paper:** minor, but a reproducibility footnote: a clean checkout
  must publish without relying on developer-local, gitignored settings.

### D3 — `/experiment/run` returns HTTP 500 after ~153s (the hard one)
- **Symptom:** first cloud run (default `seeds=2 tasks=12`) ran for **153s** then
  returned **HTTP 500 with an empty body**. `health` was 200 and fast.
- **Misleading trails while diagnosing:**
  - `func ... logstream` is **not supported on Flex Consumption** (Linux/Flex) — it
    refused immediately and pointed to the portal Live Stream.
  - `az monitor app-insights ...` **hung for 30+ min** on first use: the dynamic
    install of the `application-insights` CLI extension waited on a confirmation prompt
    that never surfaced in the shell. Fixed with
    `az config set extension.use_dynamic_install=yes_without_prompt`.
  - First App Insights queries returned **all tables empty** (exceptions, traces,
    requests) — not because telemetry was missing, but because the default query window
    was too narrow for a run that had since aged out. A **fresh** invocation plus
    `--offset 1h` immediately surfaced full telemetry.
- **First (wrong) hypothesis — initial cold start:** I initially blamed a cold-start
  transient, since the first failure was right after deploy with both apps cold. A
  warm `seeds=1 tasks=2` run then succeeded, seeming to confirm it. **This was wrong.**
- **Refutation:** re-running the default `seeds=2 tasks=12` *warm* still failed at
  ~149s — two failures at ~150s (149s, 153s) regardless of warm/cold state. A fixed
  wall-clock failure independent of warm-up is a **timeout**, not initial cold start.
- **Actual root cause (confirmed by telemetry):** a **single A2A `message/send` call
  stalled for ~60s and hit the router's client timeout**. App Insights shows the last
  successful `Functions.a2a` at 15:48:21.39, then a **60.1s gap**, then the exception
  at 15:49:21.49 — exactly the `timeout=60` of `_http_transport`
  (`src/router/function_app.py`). `urllib` raised `URLError`/timeout, unhandled →
  function `Failed` (Duration=147233ms) → HTTP 500 with empty body. All *other* a2a
  calls completed in 3–16 ms.
- **Why one call stalls:** the agent host (one Function App for all agents) **scales
  out under the burst** of the larger run; a request lands on a **freshly-spun instance
  still cold-starting**, and that exceeds the 60s client timeout. This reconciles every
  observation: the small run (few calls, no scale-out) passes; the large run reliably
  triggers scale-out → one stalled call → failure at ~150s. `DrainMode`/`StopAsync`
  traces right after the failure corroborate instance churn.
- **Not the cause (ruled out):** RBAC/managed identity (a warm run wrote both result
  blobs with 201), App Insights wiring (rich telemetry present), and the data path.
- **Implication for paper (the important one):** in a **distributed** A2A system,
  **mid-experiment autoscale events** inject multi-second-to-timeout latency spikes into
  individual A2A calls, and a naive **synchronous** router converts a single such spike
  into a **hard failure** — or, more dangerously, into a *false incident/defection*
  attributable to the platform rather than to the routing policy or the agent's
  behavior. Serverless transport latency is therefore a **first-class correctness
  concern**, not a latency footnote. This is a genuine **threat to validity** for any
  cloud A2A measurement and an argument that:
  1. transient transport failures (timeouts, scale-out stalls, 5xx) must be **separated
     from policy-attributable incidents** in the evaluator — we already read
     ground-truth personas only in the evaluator, never in a policy; by the same
     principle a transport timeout must never be mislabeled as a defection;
  2. a robust A2A client must **retry transient transport faults with backoff** (a real
     deployment would); without it, a single autoscale event corrupts a data point;
  3. the protocol should reduce scale-out churn (pre-warmed / always-ready agent-host
     instances) and/or report the transport-failure rate alongside the policy metrics so
     platform noise is visible rather than silently folded into incidents.

### D4 — Synchronous POST hits the ~230s gateway ceiling (confirmed)
- **Symptom:** with D3's retry in place, `seeds=2 tasks=12` no longer 500s at ~150s —
  instead it runs to **HTTP 504 GatewayTimeout at ~242s**. Azure Functions' front end
  caps a single HTTP request at **~230s**; the run exceeds it.
- **First fix attempt — pre-warm, FAILED:** set `always-ready http=2` on the agent host
  (`az functionapp scale config always-ready set`) to kill scale-out stalls, then re-ran
  warm. **Still 504 at ~242s.** This rules out stalls as the dominant cost.
- **Actual cost — per-call connection setup:** the router issued **~1000 sequential A2A
  calls per run**, and stdlib `urllib` opened a **fresh TCP+TLS connection on every
  call**, against the agent host's **public ingress**. Server-side each `a2a` executes
  in 3–16 ms (per App Insights), so the wall time is dominated by ~1000 TLS handshakes
  over the internet-facing front end — which alone exceeds 230s. (This also amplifies
  D3: more connections → more scale-out pressure → more stalls.)
- **Resolution:** pool a single **keep-alive `requests.Session`** in the router, reused
  across all calls (`src/router/function_app.py`; added `requests` to the router
  `requirements.txt`). ~1000 handshakes collapse to a handful. **Measured: `seeds=2
  tasks=12` went from 504 @ ~242s → HTTP 200 @ 14.9s — a ~16× speedup**, 576 runs,
  n=24 per condition×policy cell. Confirms per-call TLS setup was the entire ceiling.
- **Implication for paper:** a subtle but important systems result for distributed A2A —
  **the transport client, not the routing method, set the experiment's scale ceiling.**
  Naive per-call connection setup over public ingress (a) dominates wall time, (b)
  amplifies autoscale churn (feeding D3's false-incident risk), and (c) silently caps how
  much evidence one synchronous request can gather. Connection pooling is a one-line
  architectural change with an outsized effect. Worth stating explicitly: measured A2A
  latency/throughput is a property of the **client transport** as much as the protocol,
  and must be reported as such. (If even pooled runs approach 230s at higher
  seeds×tasks, fall back to chunking by seed across POSTs, or move the run off the
  synchronous request path entirely — async 202 + poll Blob.)

---

## 2026-06-24 — First evidence run, RQ2 result, and the V×kappa grid

### R1 — First full-grid distributed evidence run
- **Run:** `seeds=10 tasks=24` over the 4 headline conditions × 6 policies = **5760
  runs in 134s** (HTTP 200, n=24 per condition×policy cell), results persisted to
  `results/{rows,raw}.json` and analyzed via `analysis/analyze.py`.
- **Headline (RQ2):** *"CCR is the net-utility winner in 1/2 (V, kappa) cells."* CCR
  wins at high value / cheap canary (`V10_k0.05`, net 8.3 vs ~4.8 baselines, incidents
  0.125) and loses at low value / expensive canary (`V3_k0.2`, where the canary spend is
  overhead). **This conditional result is the point** — "CCR always wins" would be a red
  flag; the value/cost regime decides.
- **Boundary conditions (RQ2/RQ4) hold:** CCR collapses when canaries are
  **distinguishable** (`V3_k0.2_mixed_D`: net −2.4, incident 1.0, defection 0.875 — the
  adversary detects the probe and defects only on real tasks), and in a **clean pool**
  the canary cost is pure overhead (`optimistic_fallback` edges CCR). Honest limits, not
  failures of the method.
- **To investigate:** `attestation_only` / `card_only` show incident_rate 0 with
  success ~0.58 in *mixed* (adversarial) pools — i.e. they avoid incidents by
  fail-safe/under-routing rather than by detection. Worth explaining explicitly in the
  analysis so the CCR comparison is fair (CCR achieves success 0.92–1.0 *and* low
  incidents; the cheap baselines only get low incidents by also failing more tasks).

### R2 — Methodology note: the routing budget formula
- The two original cells used `budget` 3.8 (k=0.2) and 3.2 (k=0.05). Reverse-engineered
  to **`B = 2·exec_cost + k_candidates·kappa + margin` = 3.0 + 4·kappa** (defaults
  exec_cost=1, k_candidates=4, margin=1). It guarantees the router can always afford to
  canary all `k_candidates` and still execute + retry, so the **budget never artificially
  binds** CCR's probing — the canary count is gated by `k_candidates`/VoI, not by running
  out of budget. The grid uses this formula, reproducing the two original cells exactly.

### R3 — RQ2 cost-benefit grid (design)
- Replaced the 4 hand-picked conditions with a sweep: **V ∈ {3,5,8,10} × kappa ∈
  {0.05,0.1,0.2}** over the mixed pool, indistinguishable canaries (**12 cells**), plus 2
  retained boundary anchors (`V3_k0.2_mixed_D`, `V3_k0.2_clean_I`) = **14 conditions**.
  `analyze.py` auto-discovers the grid from `*_mixed_I` names and reports the per-cell
  winner — this surface (where CCR crosses from net-negative to net-positive) is the
  empirical contribution for RQ2.
- **Sizing constraint (from D4):** wall time ≈ runs × ~23 ms (pooled). The ~230s gateway
  ceiling ⇒ keep `conditions × tasks × seeds × 6 ≲ 9000`. For 14 conditions that means
  e.g. `tasks=24 seeds=3` (≈6k runs, ~140s, n=72/cell) or `tasks=24 seeds=4` (~190s,
  n=96/cell). Bigger ⇒ chunk by seed across POSTs (needs a `seedStart` API param) or go
  async (202 + poll Blob).

### R4 — Reproducibility convention for results & analysis artifacts
- **Decision:** keep the *intermediate* evidence, not just the final answer. Raw runs
  (`data/results/raw.json`) are **versioned in git** — committed per run, so git history
  is the lineage of every dataset behind a number. Analysis outputs are written with a
  **launch-time stamp** (`analysis/summary_YYYYMMDDHHMM.csv`,
  `rq2_costbenefit_YYYYMMDDHHMM.csv`, local time) and tracked, so each analysis is a
  dated, comparable artifact. The transient local JSONL conversion stays untracked
  (`analyze.py` reads the blob `raw.json` array directly).
- **Why it matters for the paper:** every reported figure is traceable to the exact raw
  run that produced it (git commit + matching CSV timestamp), which is the bar for a
  reproducible evidence claim.

### R5 — Full factorial results (the headline evidence)
- **Design (expanded beyond R3):** V∈{1,2,5,10} × kappa∈{0.05,0.1,0.2,0.4,0.6} ×
  pool∈{clean,hostile,mixed} × canary∈{I,D} = **120 conditions × 6 policies, n=144/cell**
  (720 summary rows). `rq2_costbenefit.csv` projects the mixed_I net-utility winner over
  the 20-cell V×kappa grid.
- **RQ2 — the crossover frontier (mixed_I net utility):** CCR is the winner in **8/20**
  cells, and the boundary is clean: CCR wins when value is high relative to canary cost.
    - V=1: CCR never wins (task worth too little to justify any probe).
    - V=2: CCR wins only at cheap canaries (kappa ≤ 0.1).
    - V=5 and V=10: CCR wins for kappa ≤ 0.2; at kappa ≥ 0.4 `optimistic_fallback` wins.
  This frontier (where CCR crosses from net-negative to net-positive) **is** the RQ2
  contribution.
- **RQ4 — the security result (incidents):** CCR's incident rate is governed almost
  entirely by canary **indistinguishability**, not by V/kappa:
    - hostile/mixed + **D** (distinguishable): incident ≈ **0.993**, defection 0.875 — the
      adversary detects the probe and defects only on real tasks. CCR's failure mode.
    - hostile/mixed + **I** (indistinguishable): incident ≈ **0.09–0.19**, defection 0.056
      — CCR catches the adversary. **~10× fewer incidents** purely from indistinguishability.
    - clean: incident 0 (no adversary; canary is overhead).
- **CRITICAL caveats for honest reporting:**
  1. **Cheap baselines buy "0 incidents" by under-routing.** `attestation_only` /
     `card_only` show incident_rate 0 *everywhere* — but success only **0.514** (they
     refuse ~half the tasks). CCR achieves success **0.95–1.0 AND** low incidents (under
     I). The comparison must say this: low incidents alone is trivial if you also fail
     half the work.
  2. **The net-utility "winner" metric undersells CCR's security.**
     `optimistic_fallback` wins net utility at high kappa, but its incident_rate under
     threat is **0.486** vs CCR's **~0.09** — a ~5× worse security posture. Net utility
     alone (with lambda_r=2.0) doesn't price realized incidents enough to surface this.
     **Report incident_rate alongside net utility**, and/or add a risk-weighted ranking;
     a lambda_r sensitivity sweep (see `analysis/sensitivity.py`) would show where CCR
     becomes the winner once incidents are priced realistically.

### R6 — lambda_r sensitivity: quantifying caveat #2 (the under-priced incident)
- **Method (`analysis/lambda_r_sweep.py`):** reprice the SAME factorial runs at varying
  lambda_r — `net(L) = value*success - lambda_c*total_cost - L*incident` — and find the
  crossover lambda_r* at which CCR becomes the net-utility winner. Exact for all baselines
  (none use lambda_r to decide). **Verified exact for CCR too on this pool:** a
  decision-coupled re-run (CCR re-deciding at each lambda_r) gives net utility *identical*
  to the post-hoc frozen value in 20/20 tested high-kappa cells — CCR's routing is
  **invariant to lambda_r over [2,8]** here (the canary already separates capability from
  risk, so the expected_utility argmax is dominated by p_hat). Hence the crossover L* are
  exact, not bounds; the earlier "conservative lower bound" framing was imprecise and is
  retracted. (Invariance is a property of *this* pool; with a capable-but-risky agent
  lambda_r would move CCR's choice and post-hoc would be a true bound.) Validated:
  recomputed net at L=2 matches the recorded net_utility to 9e-16.
- **(A) High-stakes region** (V≥5, kappa≤0.2, hostile/mixed, I): CCR is already the
  net-utility winner in **11/12 cells at the deployed lambda_r=2.0**.
- **(B) The caveat-#2 cells:** 9 indistinguishable hostile/mixed cells where
  `optimistic_fallback` out-scores CCR at lambda_r=2 **despite incident 0.486 vs CCR's
  0.09–0.19 (~5×)**. The "win" is a pricing artifact:
    - **6 flip to CCR at lambda_r\* ∈ [2.64, 5.8] (median 4.31)** — i.e. pricing an
      incident just above the default 2 makes CCR the winner.
    - **3 never flip within [0,40]** (V5_k0.4_hostile_I, V5_k0.6_hostile_I,
      V5_k0.6_mixed_I) — an **honest economic boundary**: at moderate value with very
      expensive canaries the strongest baseline becomes `attestation_only` (incident 0 by
      under-routing), and CCR's residual ~0.17 incident + canary cost can't beat a
      zero-incident abstainer. CCR is correctly *not* the tool there.
- **Decision-coupled complement** added to `scripts/sensitivity.py` (`lambda_r` sweep):
  re-runs the grid so the router re-decides per lambda_r; CCR's advantage should grow with
  lambda_r as it avoids risk more while baselines can't adapt. (Empirically CCR did NOT
  re-decide over [2,8] on this pool — see above — so the post-hoc result is exact here, not
  merely a bound; the re-run remains the right check on pools where risk and capability
  trade off.)
- **Paper takeaway:** the single-lambda_r "net-utility winner" table is misleading — it
  rewards `optimistic_fallback` for cheap success while ignoring its 5× incident rate.
  Report **incident_rate beside net utility**, and show the **lambda_r crossover**: under
  any realistic incident price (≳3), CCR wins everywhere it has materially fewer incidents.

### R7 — Parameter-free Pareto view (in `analyze.py`)
- **Why:** a risk-weighted ranking would reintroduce an arbitrary weight (the very
  "calibrated to win" objection the sensitivity suite exists to rebut). Pareto dominance
  over (success ↑, incident ↓, cost ↓) gives a "robust winner" view **without choosing a
  weight** — the weight is exactly what lambda_r prices (R6).
- **Results (per-cell non-domination across 120 cells):**
    - **CCR is non-dominated in 37/40 indistinguishable hostile/mixed cells** — on the
      frontier exactly where it is meant to operate.
    - In **28/40** of those, **CCR and optimistic_fallback are BOTH non-dominated**
      (CCR buys fewer incidents with canary cost; optimistic buys cheapness with
      incidents) → they are **incomparable without pricing an incident**, which is what
      the lambda_r crossover (R6) then resolves in CCR's favour under realistic pricing.
      Pareto + R6 are the two halves of one honest argument.
    - **`embedding` is Pareto-optimal in 0/120 cells** (always dominated) and `random`
      in 40/120 — justifies treating the 3 strong baselines (optimistic_fallback,
      attestation_only, card_only) as the real comparison set.
    - CCR is on the frontier in **58/120 overall**: it is correctly **dominated in clean
      pools** (canary = pure overhead, no threat) and low-value cells. That it is *not*
      everywhere is the honest, self-consistent result — CCR earns the frontier only
      where verification pays.
- Output: `analysis/pareto_<ts>.csv` (per condition×policy: the 3 objectives,
  `pareto_optimal`, `dominated_by`).

### R8 — Confidence intervals (devil's-advocate hardening)
- **Method (`analyze.py --ci`):** the n=144 per cell are 24 tasks x 6 seeds over a FIXED
  pool — not 144 independent draws — so we **cluster-bootstrap by task** (resample the 24
  tasks with replacement, seeds kept together) and also run a **paired** bootstrap of
  CCR − best-baseline net utility (paired by task — the correct test for "is the advantage
  real?"). CIs are within-benchmark; they do not speak to cross-pool generalization.
- **RQ1 cell (V5_k0.2_mixed_I), 95% CI:** CCR success [0.910, 0.986] vs optimistic
  [0.924, 0.986] — **overlapping (success parity, not superiority)**; CCR incident
  [0.049, 0.139] vs optimistic [0.382, 0.590] — **disjoint (incident reduction is
  significant)**.
- **The sobering result:** CCR's net-utility advantage over the best baseline is
  **significant & positive in only 7/12** high-stakes indistinguishable hostile/mixed
  cells (paired bootstrap). So part of the headline "8/20 net-utility wins" is **within
  task-level noise**.
- **Consequence for the paper's claims:** the robust, CI-backed finding is **fewer
  incidents at statistically matched success**, not a universal net-utility win. Net
  utility is regime-dependent (high V) and incident-price-dependent (lambda_r, R6). Lead
  with the security metric; state net-utility wins with their significance.
- Output: `analysis/significance_<ts>.csv` (per condition: best baseline, CCR−best,
  CI, significant flag).

### R9 — Honest tie-break for card-score selection (#3) — INVALIDATES R5–R8 numbers
- **Fix:** card-score selection broke ties by agent insertion order, so `attestation_only`
  / `card_only` always landed on the benign `degraded` over the malicious-but-identically-
  carded `canary_aware` (both declare 0.99) — verified earlier (5760/5760 → degraded).
  A router cannot distinguish two identical signed cards, so we now break ties **uniformly
  at random, seeded** (`_tie` in `policies.py`), applied to `card_only`, `attestation_only`,
  `optimistic_fallback` ordering, and CCR's scoring/plan sorts (uniformly, so no policy
  benefits from insertion order).
- **Validated effect (V5_k0.2_mixed_I, in-process):** `attestation_only` success
  0.514→**0.347**, incident **0→0.451**, net 1.57→**−0.167**. It is no longer a free
  zero-incident abstainer — it routes to the `canary_aware` defector ~half the time. CCR
  becomes the **only** high-success/low-incident policy (0.944 / 0.139 / net 2.35).
- **Direction:** the old artifact was **conservative against CCR** (it made attestation
  look safe); fixing it **strengthens** CCR — the R6 "never-flip" cells (CCR couldn't beat
  a zero-incident abstainer) and the R7 Pareto counts should shift in CCR's favour.
- **CONSEQUENCE:** R5–R8 headline numbers are now stale. **Re-run the full factorial**
  (cloud + analysis) after #2 (real embeddings) lands, then refresh RQ1–RQ4, R6, R7, R8 and
  the evidence section. Until then, treat current numbers as pre-fix.

### R10 — Real Azure OpenAI embeddings for the `embedding` baseline (#2)
- **Why:** `domain.embed` was a hashing bag-of-words — a toy a reviewer dismisses. The
  `embedding` baseline now uses real AOAI embeddings in the cloud variant.
- **Code:** `ccrlib/embeddings.py` — config-driven provider, process-cached
  (`lru_cache`), managed-identity auth (DefaultAzureCredential, no keys). Uses AOAI when
  `AZURE_OPENAI_EMBEDDINGS_ENDPOINT`/`_DEPLOYMENT` are set, else falls back to the offline
  hash so local/CI stay hermetic (lazy SDK imports). `policies.embedding_policy` rewired to
  it; `openai` added to `src/router/requirements.txt`. Validated: local fallback path runs
  (backend `hash_bow`, embedding 0.521/0.083 unchanged; CCR unchanged).
- **Infra:** `modules/openai.bicep` (AOAI account, `disableLocalAuth`, `text-embedding-3-
  small` deployment) + `modules/openaiRbac.bicep` (router identity → "Cognitive Services
  OpenAI User"); wired in `resources.bicep` (module, router app settings, RBAC, output).
  `az bicep build` passes.
- **Prerequisite:** the subscription needs Azure OpenAI access + `text-embedding-3-small`
  quota in the region (eastus). If unavailable, leave the AOAI env vars unset to keep the
  hash fallback (or make the module toggleable).
- **Determinism:** AOAI embeddings are deterministic per model snapshot; the only loss of
  hermeticity is the model version, pinned via deployment + api-version.

### R11 — Final numbers: post-#3 tie-break + real AOAI embeddings + embedding-in-baselines
- **Run:** full 120-cell factorial via `local_run.py --seeds 6 --tasks 24` (n=144/cell)
  with the honest random tie-break (#3) AND real `text-embedding-3-small` vectors (#2,
  local via `az login` + DefaultAzureCredential; AOAI account `oai-mbsr5z56tjkhy`). Then
  `analyze.py --ci` and `lambda_r_sweep.py` with **`embedding` added to the baseline set**.
- **Real embeddings changed the embedding baseline a lot:** incident 0.083→**0.000**,
  success 0.52→0.556 — the strongest *apparent* safety baseline, so we added it to the
  rq2/lambda_r/significance baseline sets (excluding it was no longer honest). **Devil's-
  advocate (Task 1) verified the mechanism against the data:** the real embedding routes
  **100% of tasks to the single `degraded` agent** (17280/17280, never any other) — its
  zero incidents are an *artifact of fixation* on a benign nearest-neighbour card, not
  adversary detection. (My earlier "avoids keyword-stuffed adversaries" was wrong;
  corrected in all docs.) Caveat for the paper: safety is contingent on the adversary's
  card wording (which it controls); a card worded as the nearest neighbour would draw 100%
  of traffic → incident 1.0, whereas CCR's
  capability-probe is invariant to card wording.
- **Final headline (V5_k0.2_mixed_I):** ccr 0.944/inc 0.139, optimistic 0.972/inc 0.722,
  embedding 0.556/inc 0.000, attestation 0.347/inc 0.451. CCR is the only policy high on
  both success and safety.
- **Against the hardest baseline set, CCR holds its region:** RQ2 7/20 mixed_I, but in the
  high-value region (V≥5, κ≤0.2) **CCR wins 12/12** and is **significant in 10/12**
  (paired bootstrap). λ_r caveat now just **4 cells** (all vs optimistic), flip at
  [2.45, 3.35]. Pareto frontier = {optimistic (112/120), embedding (80/120), ccr (50/120)};
  attestation/card **0/120** (dominated). CCR loses only at low value / expensive probe,
  to a genuinely-safer embedding — an honest boundary, not a pricing artifact.
- **Devil's-advocate verdict: satisfied.** CCR survives the strongest baseline (real
  semantic routing with zero incidents) in its intended high-value region, with CIs and an
  honest account of where and why it loses. `paper/evidence-section.md` updated to these
  numbers (RQ1–RQ4, λ_r, Pareto) + the semantic-baseline contingency.

### R12 — Devil's-advocate pass on the final results + paper figures
- **Devil's advocate (verdict: clean, 1 fix).** Cross-checked every number in
  `evidence-section.md` against the pasted script outputs — all consistent, no
  hallucinations. (i) Re-verified the λ_r "exact by invariance" claim *post-#3*
  (decision-coupled re-run, 0/24 violations — holds). (ii) **Caught one unsupported
  inference:** I had claimed `embedding` avoids the keyword-stuffed adversaries; the data
  shows it routes 100% to `degraded` (fixation), so its zero incidents are an artifact, not
  detection — corrected in evidence-section (RQ1 + Pareto), README, REPRODUCE, R11.
- **Figures (`analysis/figures.py`, matplotlib).** `fig_rq2_advantage` = V×κ heatmap of
  CCR's net-utility advantage over the best baseline (winner annotated); `fig_lambda_r_
  crossover` = CCR−best vs λ_r for the genuine crossover cells (those where the leading
  baseline has *more* incidents than CCR; cells lost to a lower-incident baseline are
  excluded, since they don't cross). Both render cleanly (verified). Figures git-ignored
  (regenerated from the CSVs).

## Status
- **Distributed path GREEN.** `seeds=2 tasks=12` runs in ~15s (HTTP 200), 576 runs,
  n=24/cell, results persisted to `results/rows.json` + `raw.json`. D1–D4 all resolved.
- Full-grid signal (n=24) holds the thesis: at high value / low canary cost
  (`V10_k0.05_mixed_I`) **CCR dominates** (net utility 8.3 vs ~4.8 baselines, incidents
  0.125); CCR's advantage **requires indistinguishable canaries** — in `V3_k0.2_mixed_D`
  (distinguishable) it collapses (net −2.4, incident 1.0); in a clean pool the canary
  cost is pure overhead (optimistic_fallback edges it). Honest boundary conditions.
- **Next:** V×kappa grid (R3) is coded in `_headline_conditions` — redeploy router, run
  the grid (`tasks=24 seeds=3`, ~140s, n=72/cell), download `results/raw.json`, and
  `analyze.py` emits the per-cell RQ2 winner surface. Then write up the crossover and the
  fail-safe-baseline caveat (R1). `always-ready` on the agent host can be removed (pooling
  made it unnecessary — saves cost).

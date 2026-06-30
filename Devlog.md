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
  local via `az login` + DefaultAzureCredential; AOAI account `oai-<token>`). Then
  `analyze.py --ci` and `lambda_r_sweep.py` with **`embedding` added to the baseline set**.
- **Real embeddings changed the embedding baseline a lot:** incident 0.083→**0.000**,
  success 0.52→0.556 — the strongest *apparent* safety baseline, so we added it to the
  rq2/lambda_r/significance baseline sets (excluding it was no longer honest). **Devil's-
  advocate (Task 1) verified the mechanism against the data:** the real embedding routes
  **100% of tasks to a `degraded` provider** (the `northwind` agent of each skill — four
  distinct agents; 17280/17280, never a malicious or capable persona; see R15) — its
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

### R13 — The observable demo: `/demo/compare` + App Insights + Mermaid (Task 3)
- **Why the cloud now earns its keep:** in `local_run.py` an "incident" is a label; over real
  A2A an incident is a real HTTP POST shipping the task payload to a malicious agent —
  *observable*. The demo makes the CCR-vs-no-CCR benefit visible to a newcomer.
- **`POST /api/demo/compare`** (router): for one task over real A2A, runs `optimistic_fallback`
  vs `ccr` (configurable), returns per policy the ordered A2A calls (agent, persona,
  kind=canary|task, payload sent), the incident label, and a **Mermaid `sequenceDiagram`**.
  Auto-picks the first of ≤8 tasks where the policies *differ* on incident (the teaching
  case). Verified in-process: optimistic ships the REAL task to `canary_aware [MALICIOUS]`
  (incident); CCR probes all candidates synthetically, then executes only on the verified
  `correct` agent (no incident).
- **App Insights enrichment:** router logs custom dims per call (policy/kind/targetAgent/
  targetPersona/payload/isIncident); the agent host logs what it received (agentId/persona/
  kind/payloadReceived) → Application Map + end-to-end transaction waterfall + a KQL filter
  (`persona=='canary_aware' && kind=='task'`) show the incident on screen.
- **Tooling choice:** App Insights (native) + Mermaid, **not** LangSmith/Langfuse — those
  trace LLM generations/agent reasoning, of which this system has none (deterministic
  SimAgents + one embedding call). Langfuse would only fit if the agents became real LLMs.
- **Infra trim (Task-3 cleanup):** dropped the `data`/`results` blob containers + their app
  settings + the `DATA_STORAGE_ACCOUNT` output + all blob code in the router (dataset ships
  bundled; results are inline / local). `_VALIDATION` cut to 4 headline cells. Kept: agent
  host, router, Storage (Flex requires it), App Insights, AOAI, RBAC. `az bicep build` passes.

### R14 — Embedding reproducibility: live + cache + verify (keeps the real instance on the table)
- **Concern:** a committed vector cache alone could be hand-crafted ("numbers I invented so
  it fits"); a live AOAI run proves the numbers came from the real model. We want both.
- **Design (`ccrlib/embeddings.py`), three modes by precedence:** (1) **live** — AOAI
  configured → real `text-embedding-3-small` (the canonical, paper run), and each vector is
  *recorded* to `data/embedding_cache.json`; (2) **cache replay** — AOAI unset but cache
  present → exact numbers offline, no Azure; (3) **hash** fallback. The cache is a *recording*
  of a live run, not a substitute.
- **Anti-fabrication:** `python -m ccrlib.embeddings --verify` re-queries the live model and
  confirms every cached vector still matches (cosine ≥ 1−1e-4) → committed vectors are
  re-checkable, not invented. Embeddings are deterministic per model version (no sampling),
  so the live run is reproducible-as-reported with deployment + api-version pinned.
- **Cache write is best-effort** (succeeds locally to populate the committable file; no-op on
  the read-only cloud app filesystem). `data/embedding_cache.json` is trackable (only
  `data/results/` is git-ignored). Verified the 3 modes locally (hash / replay / verify-aborts-
  without-AOAI) without touching `data/`. REPRODUCE.md §2 updated (2a live, 2b offline replay).

### R15 — Second devil's-advocate pass: full reproduction + precision fixes
- **Method:** regenerated the entire factorial offline from the committed real-embedding
  cache (`backend=embedding_cache`, bit-identical to the live AOAI run) and cross-checked
  *every* number in `evidence-section.md`/`README`/`Devlog` against the freshly computed
  data (103,680 runs).
- **Verified EXACT (no hallucinations):** RQ1 table (all 18 values) + its 6 CIs (bootstrap
  is seeded, `random.Random(20260624)`); RQ2 7/20 and high-value 12/12, sig 10/12; RQ3
  0.556/0.986/3.05; RQ4 D 0.854/1.0/0.812, I 0.111/0.139/0.944; Pareto 36/40 and
  112/80/50/40/0/0; λ_r 4 cells all-vs-optimistic, flips [2.45,3.35]; λ_r exactness
  (decision-coupled residual 1.8e-15). **Fixation confirmed:** `embedding` chose
  `{degraded: 17280}` of 17280 runs, 0 incidents.
- **Fixes applied (precision/scope, not fabrication):**
  1. **"single `degraded` agent" → corrected.** `embedding` fixates on **four** distinct
     agents (`degr-{rag,planning,data_transform,api_action}-northwind-0`, one per skill,
     each with its own card), not one. The substantive claim (always the `degraded`
     persona, never malicious/capable → safety by fixation) holds. Reworded in
     `evidence-section.md` (RQ1 + Pareto), `README.md` (threats), `REPRODUCE.md` (§2a).
  2. **"median 3.08" → 2.90.** `lambda_r_sweep.py` reported `sorted(flips)[len//2]` (the
     upper-middle), not the true median of {2.45,2.72,3.08,3.35}. Fixed to
     `statistics.median` and updated `evidence-section.md`. Range [2.45,3.35] was correct.
  3. **RQ3/RQ4 tables now name the representative cell** (V5, κ=0.2): the numbers were the
     single-cell values, exact, but presented as if general.
  4. **README:** removed the stale mention of `data`/`results` Storage containers (dropped
     in R13; only `deploy-*` remain).
- **Verdict: clean.** No invented numbers, no unsupported inferences; the thesis and all
  central figures reproduce exactly. The only changes were tightening over-specific wording
  and one mislabeled statistic.

### R16 — Level-B: measuring canary indistinguishability (the central open problem)
- **Why:** the paper assumes indistinguishability as an exogenous flag (§8.4) and names
  *constructing a provably-indistinguishable canary against an adaptive adversary* as the open
  problem (§10). R16 turns the flag into a **measurement**: `experiments/indistinguishability/`.
- **Design:** in a QA (RAG) domain, build real-vs-canary query sets under 4 **construction
  strategies** — `marked` (explicit probe markers), `templated` (keyword-bag), `llm_generated`
  (chat model paraphrases a real-style question with a known answer), `dist_matched` (canary =
  a held-out REAL labelled question). Two adversaries score them: (1) an **embedding
  classifier** (text-embedding-3-small + cross-validated L2 logistic regression, ROC-AUC, with
  a **label-permutation null** so high-dim spurious splits aren't mistaken for real ones), and
  (2) an **LLM judge** (chat model scores P(probe); AUC). Metric: AUC≈0.5 = indistinguishable,
  →1.0 = distinguishable.
- **Honest framing baked in:** `dist_matched` is ≈0.5 *by construction* (same distribution) —
  it shows the only provably-indistinguishable canary is a real task whose answer you happen to
  know (needs labelled real traffic). The live question is how close `llm_generated`
  (synthesized-but-disguised) gets to that 0.5 floor. A high AUC there is an honest negative
  that *bounds* the property — exactly what §8.4/§10 want.
- **Status:** harness built; `python run.py --selftest` green (classifier returns ~0.50 with a
  matching permutation null on same-distribution data, ~1.00 on a genuine 3-dim shift; canary
  shapes + the rank-AUC identity verified). Needs the NUC to run for real: Azure OpenAI
  embeddings + a chat deployment + a SQuAD slice. `results.json` is committable (AUC numbers);
  the vector cache + dataset + figure are git-ignored (re-run to reproduce). Decisions taken
  with the user: **public QA dataset** + **both adversaries**.
- **Next:** run on the NUC, then write a §8.8 "Constructing the canary: measured
  indistinguishability" into the paper and downgrade §10's open problem to "characterized".

## 2026-06-27 — Real-LLM agent pilot (§8.10) and AISec 2026 packaging

### R17 — Real-LLM agent pilot: the thesis on real models, and where it breaks (Limitation C)
- **Why:** §8 is deterministic SimAgents; the standing question (and the user's real interest)
  was whether the mechanism survives **real LLM agents** — and what it *cannot* catch. New
  harness: `experiments/real_agents/` (gpt-5.4-mini agents over a SQuAD QA/RAG pool).
- **The honesty problem, and the fix:** a safety-aligned model **resists** the malicious prompts
  — in the first run the "adversarial" agent answered *helpfully* instead of exfiltrating and the
  defector often didn't defect (CCR looked no better than optimistic). So we **do not rely on the
  model being evil**: capability stays real LLM (correct/degraded *answer* with the model), but
  the malicious *consequence* is **structural** (wrapper-enforced). An incident = the real payload
  was delivered to an untrusted endpoint (recorded in the transcript); `degraded` answers
  **without the context** (genuinely weaker). The lazy defector's *detection* is structural
  (marker = the D/I condition), **justified by R16/§8.8** (a real LLM can't detect an
  indistinguishable probe) — we do **not** claim the LLM reproduces the detection.
- **Two threat models, on purpose:** (a) **lazy defector** = cooperate-iff-it-can-confirm-a-test
  (the thesis' treacherous-turn adversary, caught by an indistinguishable canary); (b)
  **competent covert** = a **valid-signed exfiltrator** that answers the canary *and* the real
  task correctly yet exfiltrates the real payload. The first devil's-advocate pass caught that an
  approximate "competent" agent conflated capability with malice, so we built a **clean** covert
  exfiltrator (`build_pool_covert`).
- **Findings (n=40, gpt-5.4-mini, one cached realization):**
  - **RQ1/RQ4 hold on real LLMs:** CCR incident **0.175** (lowest; optimistic 0.725), success at
    **parity** (0.925), `attestation_only` collapses (0.275/0.55 — the Provenance Paradox).
    RQ4 D→I: defection **0.7→0.0**, incident 0.825→0.175.
  - **Limitation C — CCR breaks under competent malice:** against the clean covert exfiltrator
    CCR **flips best→worst**, incident **0.175→0.775**. It passes attestation (valid signature)
    *and* the capability probe (correct answer), so CCR delegates and "succeeds" (0.925) **while
    the real data is exfiltrated**. The probe sees **competence, not malice** — the empirical form
    of the new Threats bullet ("the canary tests capability, not malice").
- **What it establishes / doesn't:** real LLM *capability* + the reduction vs the
  incompetent/lazy defector; and an **empirical** demonstration that a competent valid-signed
  adversary defeats a capability probe (needs complementary controls: egress, provenance/receipts,
  behavioral monitoring). The "harm" is **exposure** (delivery to an untrusted endpoint), not a
  demonstrated malicious act by the model. Pilot scale: one model/domain, one realization, no
  strong CIs; `degraded` only ~65% (the model knows SQuAD facts).
- **Status:** ran on the NUC (`results.json` + `incidents.md`); numbers re-verified;
  `chat_cache.json` git-ignored. Written into the paper as **§8.10** + a Threats bullet +
  abstract/§10 scoping.
- **Next:** scale the pilot (more models/domains/tasks, with CIs) — that is the **AAMAS** paper
  (separate, future).

### R18 — AISec 2026 submission: English LaTeX, double-blind, formatting
- **Why:** the target venue is **AISec 2026** (ACM workshop, CCS-colocated; ≤10pp sigconf,
  double-blind, GenAI declaration). Produced `paper/ccr-aisec2026.tex`, translated from the
  Spanish canonical (`Paper.A2A.v0.3.md`, kept as a backup copy — **both** now carry §8.10).
- **Double-blind:** anonymized artifact repo (Anonymous GitHub / 4open.science); redacted personal
  identifiers from `Devlog.md`/README; `\documentclass[sigconf,anonymous,review]`.
- **Scope honesty end-to-end:** Limitation C (capability ≠ malice) now stated in the abstract,
  §8.10, Threats, and Conclusions; §10 future-work points to scaling the pilot.
- **Formatting cleanup:** fixed digit-bolding in the §8.10 pilot table (`\textbf` in text mode —
  `\mathbf` does not bold digits); made long inline literals breakable (`\allowbreak` in
  `text-embedding-3-small`, `/.well-known/agent-card.json`); restructured the Reproducibility
  `verbatim` (the two-column box is ~40 chars wide — switched to short command-only lines + prose,
  and added `real_agents/run.py`); `\emergencystretch=2em` for the remaining minor overfull boxes.
- **Status:** compiles on Overleaf (~8–9pp, under the 10pp limit — confirm the exact count on
  recompile). Deadline **July 24**, notification **Sept 3**.

## Status (current — supersedes the cloud-bring-up snapshots in R1–R8)
- **Evidence FINAL:** the canonical numbers are R11 (post tie-break #3 + real embeddings),
  re-verified in R15; analysis via `analyze.py --ci`, `lambda_r_sweep.py`, `figures.py`.
  Reproducibility is R14 (live + cache + verify). Earlier per-run numbers in R1–R8 are
  **pre-fix** (explicitly retired by R9) — read R11/R15 as authoritative.
- **Distributed path GREEN.** Router + agent host on Flex Consumption; D1–D4 resolved;
  the observable demo (`/demo/compare` + App Insights + Mermaid) is R13. The cloud is a
  *distributed validation* of the deterministic in-process numbers, not a separate result.
- **Indistinguishability MEASURED (R16/§8.8):** harness ran on the NUC; the synthesized canary
  fools the reasoning judge but a classifier detects it (AUC ~0.78), while a distribution-matched
  canary is ~0.5 — §8.8 reports this and §10's open problem is downgraded to "characterized".
- **Real-agent pilot DONE (R17/§8.10):** CCR reproduces RQ1/RQ4 on gpt-5.4-mini (incident 0.175),
  but a competent valid-signed exfiltrator defeats it (0.175→0.775) — **Limitation C** documented.
- **AISec 2026 submission PREPARED (R18):** English `ccr-aisec2026.tex`, anonymized, ~8–9pp,
  Limitation C scoped end-to-end. Deadline July 24. The AAMAS paper (scaled real-agent study)
  is separate/future.
- **Open items:** none blocking the lab. `always-ready` on the agent host can be removed
  (pooling made it unnecessary).

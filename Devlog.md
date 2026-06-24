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

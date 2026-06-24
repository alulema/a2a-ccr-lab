# Empirical Evaluation

We evaluate Card+Canary Routing (CCR) against five baselines on a deterministic
agent-to-agent (A2A) testbed, asking four questions: **(RQ1)** does VoI-gated probing
improve effective routing over card- and attestation-based selection? **(RQ2)** *when*
does probing pay for itself? **(RQ3)** is CCR robust to honest-but-degraded providers
that defeat attestation? **(RQ4)** does it withstand a strategic agent that games the
probe? We then stress the headline metric itself: we price the risk parameter
($\lambda_r$) by sensitivity analysis and give a parameter-free Pareto view, so the
conclusion does not rest on a chosen weight.

## Setup

Each agent is published as an A2A endpoint exposing a signed *agent card* and a
JSON-RPC `message/send` skill. A **router** discovers cards over HTTP and selects an
agent per task; policies act only on observable evidence (cards, signatures, and
verifiable canary outputs). Ground-truth persona (`correct`, `degraded`,
`adversarial`, `canary_aware`) is read **only** by the post-hoc evaluator to label
outcomes, never inside a policy.

We compare six policies: `random` (control), `card_only` (argmax card score),
`embedding` (semantic match, trust-agnostic), `attestation_only` (best card among
signature-valid agents — the "verified identity" line), `optimistic_fallback` (try the
best card, fall through on failure up to budget; the hard no-probe baseline), and `ccr`
(VoI-gated canary probing followed by expected-utility delegation).

**Metrics.** A run yields `success` (task solved), `total_cost` (real executions plus
canaries, in execution-cost units), and two ground-truth labels: an **incident** — *any*
real execution that touched a malicious agent (harm/exposure occurs at invocation time,
whether or not the task later succeeds) — and a **defection** — a malicious agent that
*passed the canary and then failed the real task*, i.e. a successful probe evasion. Net
utility is

$$U \;=\; V\cdot \mathbf{1}[\text{success}] \;-\; \lambda_c\cdot \text{cost} \;-\; \lambda_r\cdot \mathbf{1}[\text{incident}],$$

with the price of cost $\lambda_c{=}1$ and the price of an incident $\lambda_r{=}2$
unless stated otherwise.

**Design.** We run a full factorial of $V\in\{1,2,5,10\}$ (task value),
$\kappa\in\{0.05,0.1,0.2,0.4,0.6\}$ (canary cost), pool $\in$
\{clean, hostile, mixed\}, and canary indistinguishability $\in$ \{I, D\} — 120
conditions $\times$ 6 policies, $n{=}144$ runs per cell (24 tasks $\times$ 6 seeds). The
routing budget is set to $B = 2\,c_{\text{exec}} + k\,\kappa + 1 = 3 + 4\kappa$ so that
the budget never artificially binds CCR's probing (it can always afford to canary all
$k{=}4$ preselected candidates and still execute and retry). Everything is
deterministic, which is what lets the **distributed** validation (below) reproduce the
in-process numbers exactly.

## RQ1 — Effectiveness

In the representative hostile-mixed cell ($V{=}5$, $\kappa{=}0.2$, indistinguishable
canaries), CCR matches the strongest baseline's task success while cutting incidents
roughly fivefold:

| policy | success | net utility | incident rate | mean canaries |
|---|---|---|---|---|
| **ccr** | 0.951 | **2.53** | **0.090** | 4 |
| optimistic_fallback | 0.958 | 1.89 | 0.486 | 0 |
| attestation_only | 0.514 | 1.57 | 0.000 | 0 |
| card_only | 0.514 | 1.57 | 0.000 | 0 |
| embedding | 0.472 | 1.19 | 0.083 | 0 |
| random | 0.472 | 0.56 | 0.403 | 0 |

`optimistic_fallback` reaches comparable success only by *executing* on agents until one
works — touching a malicious agent on $48.6\%$ of tasks. The attestation/card baselines
avoid incidents but solve barely half the tasks: they buy safety by **under-routing**,
refusing any task whose only declared-capable agents are unverifiable. CCR is the single
policy that is simultaneously high-success and low-incident, because the VoI gate spends
a synthetic, side-effect-free probe *before* delegating, and the prior-doubt floor in the
VoI forces it to probe even confident-looking cards.

We quantify uncertainty with a cluster bootstrap by task (the $n{=}144$ runs per cell are
24 tasks $\times$ 6 seeds over a fixed pool, not 144 independent draws; 95% CI, $B{=}2000$).
The comparison that survives is precise: CCR's task success $[0.910, 0.986]$ is
**statistically indistinguishable** from `optimistic_fallback`'s $[0.924, 0.986]$, while
its incident rate $[0.049, 0.139]$ is **disjoint** from `optimistic_fallback`'s
$[0.382, 0.590]$. The robust claim is therefore *not* that CCR completes more tasks, but
that it attains the **same** success at a **significantly lower incident rate** — it
removes exposure without sacrificing throughput.

## RQ2 — When does verification pay?

Probing is not free, so CCR should win only when the value at stake justifies the canary
cost. Sweeping $V\times\kappa$ over the mixed pool with indistinguishable canaries yields
a clean cost–benefit frontier (net-utility winner per cell):

| $V \backslash \kappa$ | 0.05 | 0.1 | 0.2 | 0.4 | 0.6 |
|---|---|---|---|---|---|
| 1 | ATT | ATT | ATT | ATT | ATT |
| 2 | **CCR** | **CCR** | ATT | ATT | ATT |
| 5 | **CCR** | **CCR** | **CCR** | OPT | OPT |
| 10 | **CCR** | **CCR** | **CCR** | OPT | OPT |

CCR is the net-utility winner in **8/20** cells, and the boundary is monotone: the
higher the value, the higher the canary cost CCR can absorb. At $V{=}1$ probing never
pays; at $V{\ge}5$ it pays up to $\kappa{=}0.2$. This is the intended result — CCR is a
tool for *valuable* delegations, and the experiment locates exactly where that is.

We do not over-read this count. A paired cluster bootstrap (CCR − best baseline, paired by
task) finds the net-utility advantage **significant and positive in 7 of the 12**
high-value indistinguishable cells ($V{\ge}5$, $\kappa{\le}0.2$); the remaining margins
fall within task-level noise. The net-utility *winner* is thus a regime- and
significance-qualified statement, and it additionally depends on how an incident is priced
(§\,$\lambda_r$). The quantity that is significant throughout — and the one we lead with —
is the incident reduction of §RQ1, not the net-utility margin.

## RQ3 — Robustness to honest degradation

Attestation verifies *identity*, not *current capability*. In the clean pool, the
honest-but-degraded provider is correctly signed but ships a stale card, so
`attestation_only` and `card_only` select it and fail ($\text{success}{=}0.514$). CCR's
empirical probe detects the capability gap and routes around it
($\text{success}{=}1.0$, $U{=}3.16$), confirming that declarative evidence — even
*verified* declarative evidence — is insufficient when capability drifts.

## RQ4 — Strategic defection: a model, and the condition for defeating it

The hardest adversary is `canary_aware`: validly signed (so attestation admits it), it
passes a probe it can *recognize* and then defects on the real task — the failure mode
prior "lazy-peer" work does not capture. We model this adversary explicitly: it succeeds
on a probe **iff it can tell the probe from a real task**, and otherwise performs at its
true (low) capability. Under that model, making the canary indistinguishable removes its
ability to selectively pass:

| CCR canary | defection rate | incident rate | success |
|---|---|---|---|
| distinguishable (D) | 0.875 | 0.993 | 0.757 |
| indistinguishable (I) | 0.056 | 0.090 | 0.951 |

**We are explicit about what this does and does not establish.** It is *not* empirical
evidence that real canaries are indistinguishable — distinguishability is an exogenous
condition in the simulation, and the contrast above is, in part, a restatement of that
modeling choice (the $D$ column is the adversary obeying "I can tell"). What the result
*does* establish is the **decision-theoretic structure**: (i) a recognizable probe is
strictly worse than no probe — the adversary evades it *and* CCR pays the cost (net
utility goes negative); and (ii) the entire security value of probing is contingent on a
single property — probe indistinguishability — which therefore must be *constructed and
defended*, not assumed. The residual incident rate under $I$ (0.090) is itself
instructive: it is not zero because the modeled adversary still passes an indistinguishable
probe at its true success rate ($\approx 0.12$) by chance, so probing bounds — but does not
eliminate — exposure to a defector that occasionally performs. We therefore frame
**constructing a provably indistinguishable canary against an adaptive adversary as the
central open problem**, of which this experiment characterizes the payoff and the failure
cost, rather than as a solved component.

## Pricing the risk: $\lambda_r$ sensitivity

The single-$\lambda_r$ "winner" in RQ2 is itself a modeling choice: at $\lambda_r{=}2$,
`optimistic_fallback` wins several high-value/high-cost cells *despite* a $5\times$ higher
incident rate, because a realized incident is under-priced. We therefore reprice the same
runs across $\lambda_r$ and locate the crossover $\lambda_r^\star$ at which CCR takes the
lead. The repricing is exact for every baseline (none consult $\lambda_r$ when routing);
for CCR we verified it is exact here too — a decision-coupled re-run in which CCR
re-decides at each $\lambda_r$ yields net utility identical to the frozen reprice in all
tested high-cost cells, because once the canary separates capability from risk the
expected-utility argmax is dominated by the capability estimate and does not depend on
$\lambda_r$ over the relevant range. (This invariance is a property of the present pool,
not a general claim: with a capable-but-risky provider $\lambda_r$ would change CCR's
choice.)

Across the indistinguishable hostile/mixed cells where a baseline out-scores CCR at
$\lambda_r{=}2$ despite more incidents (nine cells), CCR becomes the net-utility winner
once an incident is priced at $\lambda_r^\star\in[2.6,\,5.8]$ (median $4.3$) — only
modestly above the default. The few cells that never flip ($V{=}5$, $\kappa\in\{0.4,0.6\}$)
are an honest economic boundary: there the strongest baseline is `attestation_only`,
which attains *zero* incidents by abstention, and CCR's residual incident rate plus
expensive canaries cannot beat a zero-incident abstainer at moderate value. CCR is
correctly *not* recommended there. The takeaway for the paper's tables: report
`incident_rate` beside net utility and show the crossover, rather than a single-$\lambda_r$
ranking that silently rewards cheap success over safety.

## A parameter-free view: Pareto dominance

To avoid resting any claim on a chosen weight, we also report Pareto dominance over
(success $\uparrow$, incident $\downarrow$, cost $\downarrow$). CCR is **non-dominated in
37/40** indistinguishable hostile/mixed cells — on the frontier exactly where it is meant
to operate. In **28/40** of these, CCR and `optimistic_fallback` are *both* non-dominated:
CCR buys fewer incidents with canary cost, `optimistic_fallback` buys cheapness with
incidents, so they are **incomparable without pricing an incident** — which is precisely
the trade-off the $\lambda_r$ crossover resolves in CCR's favor under any realistic price.
The Pareto view and the $\lambda_r$ sweep are thus two halves of one argument, neither
using an arbitrary weight. Two corollaries justify our baseline set: `embedding` is
Pareto-optimal in **0/120** cells (always dominated) and `random` in only $40/120$, while
CCR is on the frontier in $58/120$ overall — correctly *dominated* in clean pools, where
the canary is pure overhead. That CCR is not everywhere on the frontier is the
self-consistent result: it earns the frontier only where verification pays.

## Distributed validation and threats to validity

The numbers above are reproduced over **real A2A transport**: the router and the agent
host run as separate Azure Functions (Flex Consumption), exchanging signed cards and
JSON-RPC `message/send` calls over HTTP; determinism guarantees the distributed run
matches the in-process one. Bringing this up surfaced an operational threat to validity
worth stating: serverless **autoscale can inject latency spikes** into individual A2A
calls (a request landing on a cold scale-out instance), and a naive synchronous router
converts such a spike into a *false* failure — or, worse, a false incident attributable
to the platform rather than to the agent. We mitigate this by (i) retrying transient
transport faults (timeouts, connection errors, 5xx) with backoff — never an agent's own
JSON-RPC response — and (ii) the same separation-of-concerns that keeps ground-truth
persona out of policies: a transport timeout is never labeled a defection. (Connection
pooling additionally removed a wall-clock ceiling that a per-call TLS handshake imposed on
large runs.) The remaining limitations are the usual ones for a simulation-grounded study:
agents are deterministic stand-ins for real providers, the malice taxonomy is
hand-specified, and the canary's indistinguishability is assumed rather than
cryptographically enforced — the last being the most important target for future work,
since RQ4 shows it is the property on which the whole defense turns.

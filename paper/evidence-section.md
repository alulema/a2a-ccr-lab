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
`embedding` (semantic match by cosine similarity of task text to card description, using
**real Azure OpenAI `text-embedding-3-small` vectors** — trust-agnostic),
`attestation_only` (best card among signature-valid agents — the "verified identity"
line), `optimistic_fallback` (try the best card, fall through on failure up to budget;
the hard no-probe baseline), and `ccr` (VoI-gated canary probing followed by
expected-utility delegation). When two validly-signed agents declare the same capability
(a stale honest provider and a defector both at the ceiling), a router has no basis to
prefer one, so all card-score selections break ties **uniformly at random (seeded)** —
modelling the absence of signal rather than resolving it by an artifact of agent order.

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
canaries), CCR is the only policy that is **simultaneously high-success and low-incident**:

| policy | success | net utility | incident rate | mean canaries |
|---|---|---|---|---|
| **ccr** | 0.944 | **2.35** | 0.139 | 4 |
| optimistic_fallback | 0.972 | 1.42 | 0.722 | 0 |
| embedding | 0.556 | 1.78 | **0.000** | 0 |
| random | 0.514 | 0.75 | 0.410 | 0 |
| attestation_only | 0.347 | −0.17 | 0.451 | 0 |
| card_only | 0.347 | −0.17 | 0.451 | 0 |

Each baseline fails on at least one axis. `optimistic_fallback` reaches the highest
success only by *executing* on agents until one works — touching a malicious agent on
$72\%$ of tasks. `embedding` (real semantic routing) incurs
**zero incidents** — but as an *artifact*: it routes **every** task to the single
nearest-description agent (here the benign-but-mediocre `degraded` one), so it never
touches a malicious agent *and* never reaches a *capable* one, completing only $56\%$ (we
dissect this "safety by fixation" in the Pareto discussion). Strikingly,
`attestation_only` / `card_only` now fail on **both** axes (success 0.35, incidents 0.45):
identity verification gives **no signal** to separate a validly-signed defector from an
honest provider when both declare the ceiling capability — the Provenance Paradox in full.
CCR alone resolves the tension, because the VoI gate spends a synthetic, side-effect-free
probe *before* delegating, and the prior-doubt floor forces it to probe even
confident-looking cards.

We quantify uncertainty with a cluster bootstrap by task (the $n{=}144$ runs per cell are
24 tasks $\times$ 6 seeds over a fixed pool, not 144 independent draws; 95% CI, $B{=}1000$).
CCR's task success $[0.903, 0.979]$ **overlaps** `optimistic_fallback`'s $[0.938, 1.000]$
(parity, not superiority) while its incident rate $[0.083, 0.194]$ is **disjoint and far
below** `optimistic_fallback`'s $[0.653, 0.806]$. Against the safe baseline the trade
reverses: CCR's success is **disjoint and far above** `embedding`'s $[0.424, 0.681]$,
while `embedding`'s incidents $[0.000, 0.000]$ are below CCR's. CCR thus buys a large
amount of task completion for a small, bounded amount of exposure — the only point on the
frontier that is high on both axes.

## RQ2 — When does verification pay?

Probing is not free, so CCR should win only when the value at stake justifies the canary
cost. We sweep $V\times\kappa$ over the mixed pool with indistinguishable canaries and
take the net-utility winner per cell **against the full baseline set, including the strong
zero-incident `embedding` router** (its inclusion is what makes this the hard test).

CCR is the net-utility winner in **7/20** cells, concentrated in a clean high-value /
cheap-probe region: across the high-value cells ($V{\ge}5$, $\kappa{\le}0.2$, hostile and
mixed) **CCR wins 12/12** at the default $\lambda_r{=}2$, and a paired cluster bootstrap
(CCR − best baseline, by task) finds its advantage **significant and positive in 10 of
those 12**. CCR *loses* exactly where it should: at low value ($V{\le}2$) or expensive
probes ($\kappa{\ge}0.4$), where the winner is either `embedding` — which is genuinely
safer (zero incidents) but completes far fewer tasks — or `optimistic_fallback`, whose win
is an artifact of under-priced incidents that the $\lambda_r$ analysis below dissolves.
The frontier is therefore honest in both directions: CCR earns its keep on valuable
delegations and concedes the rest. (The full per-cell winner grid is in the supplementary
`rq2_costbenefit` table.) The quantity that is significant throughout — and the one we
lead with — is the incident reduction of §RQ1, not a raw winner count.

## RQ3 — Robustness to honest degradation

Attestation verifies *identity*, not *current capability*. In the clean pool (honest
agents only), the degraded provider is correctly signed but ships a stale card claiming
ceiling capability, so `attestation_only` and `card_only` select it and fail
($\text{success}{=}0.556$). CCR's empirical probe detects the capability gap and routes to
a genuinely capable agent ($\text{success}{=}0.986$, $U{=}3.05$), confirming that
declarative evidence — even *verified* declarative evidence — is insufficient when
capability drifts.

## RQ4 — Strategic defection: a model, and the condition for defeating it

The hardest adversary is `canary_aware`: validly signed (so attestation admits it), it
passes a probe it can *recognize* and then defects on the real task — the failure mode
prior "lazy-peer" work does not capture. We model this adversary explicitly: it succeeds
on a probe **iff it can tell the probe from a real task**, and otherwise performs at its
true (low) capability. Under that model, making the canary indistinguishable removes its
ability to selectively pass:

| CCR canary | defection rate | incident rate | success |
|---|---|---|---|
| distinguishable (D) | 0.854 | 1.000 | 0.812 |
| indistinguishable (I) | 0.111 | 0.139 | 0.944 |

**We are explicit about what this does and does not establish.** It is *not* empirical
evidence that real canaries are indistinguishable — distinguishability is an exogenous
condition in the simulation, and the contrast above is, in part, a restatement of that
modeling choice (the $D$ column is the adversary obeying "I can tell"). What the result
*does* establish is the **decision-theoretic structure**: (i) a recognizable probe is
strictly worse than no probe — the adversary evades it *and* CCR pays the cost (net
utility goes negative); and (ii) the entire security value of probing is contingent on a
single property — probe indistinguishability — which therefore must be *constructed and
defended*, not assumed. The residual incident rate under $I$ (0.139) is itself
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
$\lambda_r{=}2$ *despite more incidents* — **four cells, all won by `optimistic_fallback`**
(incident $0.72$ vs CCR's $\approx0.15$) — CCR becomes the net-utility winner once an
incident is priced at $\lambda_r^\star\in[2.45,\,3.35]$ (median $3.08$), only modestly
above the default. Crucially, the cells where CCR instead loses to `embedding` are **not**
in this set: `embedding` wins them with *fewer* incidents (zero), so those losses are not
pricing artifacts but the honest boundary where a zero-incident semantic router is
genuinely preferable to paying for a probe — provided the adversary's card is
semantically separable (the contingency discussed with the Pareto view below). The
takeaway for the paper's tables:
report `incident_rate` beside net utility and show the crossover; the only "wins" that
dissolve under realistic incident pricing are `optimistic_fallback`'s cheap-but-unsafe
ones.

## A parameter-free view: Pareto dominance

To avoid resting any claim on a chosen weight, we also report Pareto dominance over
(success $\uparrow$, incident $\downarrow$, cost $\downarrow$). CCR is **non-dominated in
36/40** indistinguishable hostile/mixed cells — on the frontier exactly where it is meant
to operate. The frontier is occupied by three genuinely incomparable strategies, each a
different corner: `optimistic_fallback` (highest success, highest incidents;
Pareto-optimal in $112/120$ cells), `embedding` (zero incidents, lowest success;
$80/120$), and **CCR** (high on both axes; $50/120$). `attestation_only` and `card_only`
are Pareto-optimal in **0/120** — dominated everywhere, because a zero-signal identity
check is strictly worse than semantic routing here. That CCR is non-dominated in only half
the cells overall is the *self-consistent* result: it is correctly dominated in clean
pools (the canary is pure overhead) and at low value, and it earns the frontier where its
success-and-safety balance is irreplaceable. Choosing between the three frontier corners
requires pricing an incident — which is exactly what the $\lambda_r$ sweep does, resolving
it in CCR's favor across the high-value region.

**The contingency of the semantic baseline.** `embedding`'s zero-incident showing is the
strongest *apparent* safety result among the baselines, so it deserves scrutiny — and the
routing logs make the mechanism unambiguous. With real `text-embedding-3-small` vectors the
policy routes **100% of tasks to the single `degraded` agent** (its "High-throughput …
service" card is the task's nearest neighbour) and selects **no other agent** — malicious
or honest-capable — in any of the 17,280 runs. Its zero incidents are therefore an
**artifact of fixation**: it happens to lock onto a *benign* card, so it never touches a
malicious agent; it also never reaches a *capable* one, which is exactly why its success is
only 0.556. Had the adversary worded its card to be the nearest neighbour instead, the same
policy would route **every** task to it (incident → 1.0). So `embedding`'s safety is not a
property of semantic routing — it is contingent on the adversary's card wording, which the
adversary controls. CCR's probe tests *capability* by execution, not description
similarity, and is invariant to card wording. We report `embedding` with real vectors (not
a hash toy) precisely so this comparison is honest, and we read its result as the *limit*
of what a description-only baseline can establish, not as evidence that semantic routing is
safe.

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

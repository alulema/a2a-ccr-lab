"""CCR Router (Azure Functions, Flex Consumption) — distributed A2A.

Runs the routing policies over REAL A2A HTTP calls against the agent host.

  GET  /api/health
  POST /api/experiment/run   body: { "seeds": 2, "tasks": 12 }  (optional)
       small validation subset — shows the distributed path reproduces local numbers.
  POST /api/demo/compare     body: { "pool": "mixed", "policies": ["optimistic_fallback","ccr"] }
       side-by-side trace of CCR vs a no-CCR baseline for ONE task: which agents got a REAL
       call (your data) vs a synthetic canary, the incident, and a Mermaid sequence diagram.
       Emits enriched App Insights traces (targetAgent / persona / kind / isIncident).

Separation of concerns: routing POLICIES (ccrlib.policies) only ever see cards
discovered over HTTP and verifiable canary/task outputs. Ground-truth personas
are read here, in the EVALUATOR, solely to label incidents/defections — never
inside a policy decision.
"""
from __future__ import annotations

import json
import logging
import os
import time

import azure.functions as func
import requests

from ccrlib import experiment, metrics
from ccrlib.policies import POLICIES, Context
from ccrlib.scoring import Params

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://localhost:7071/api")


# --- Dataset loading -----------------------------------------------------------

def _load_dataset(name: str, local_rel: str):
    # The dataset ships bundled inside the app (copied by scripts/sync_lib at package
    # time); the repo copy is the fallback for a local `func start`. No Blob dependency.
    bundled = os.path.join(os.path.dirname(__file__), "data", name)
    if os.path.exists(bundled):
        with open(bundled, encoding="utf-8") as f:
            return json.load(f)
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", local_rel))
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --- A2A HTTP transport --------------------------------------------------------

# One pooled, keep-alive Session reused across every A2A call in a run. urllib opened a
# fresh TCP+TLS connection per call; over the ~1000s of sequential calls in a run that
# handshake overhead alone pushed a single synchronous POST past the ~230s gateway
# ceiling (Devlog D4). Connection reuse collapses it to a handful of handshakes.
_SESSION = requests.Session()

# The agent host is a separately-scaling Function App. A request landing on a cold
# scale-out instance can stall to the client timeout. Retry transient TRANSPORT faults
# (timeouts, connection errors, 5xx/429) so a platform autoscale event doesn't corrupt a
# data point — a real A2A client would. This never retries an agent's own JSON-RPC
# response (returned as-is for the evaluator); a transport timeout must not be mislabeled
# as agent behavior. See Devlog D3.
_RETRY_ATTEMPTS = 4
_RETRY_BACKOFF_S = 2.0
_RETRY_HTTP_CODES = {429, 500, 502, 503, 504}


def _http_json(url: str, payload: dict | None = None, timeout: int = 30) -> dict:
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            if payload is None:
                resp = _SESSION.get(url, timeout=timeout)
            else:
                resp = _SESSION.post(url, json=payload, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
        else:
            if resp.status_code not in _RETRY_HTTP_CODES:
                resp.raise_for_status()  # real 4xx/5xx -> raise; 2xx -> fall through
                return resp.json()
            last_exc = requests.HTTPError(f"transient {resp.status_code} from {url}")
        if attempt < _RETRY_ATTEMPTS - 1:
            sleep_s = _RETRY_BACKOFF_S * (2 ** attempt)
            logging.warning("A2A transport transient fault on %s (attempt %d/%d): %r; "
                            "retrying in %.1fs", url, attempt + 1, _RETRY_ATTEMPTS,
                            last_exc, sleep_s)
            time.sleep(sleep_s)
    assert last_exc is not None
    raise last_exc


def _http_get_card(agent_id: str) -> dict:
    url = f"{AGENT_BASE_URL}/agents/{agent_id}/.well-known/agent-card.json"
    return _http_json(url, timeout=30)


def _http_transport(agent_id: str, request: dict) -> dict:
    url = f"{AGENT_BASE_URL}/agents/{agent_id}/a2a"
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": request}
    return _http_json(url, payload=rpc, timeout=60)["result"]


def _build_env(active_specs: list[dict]):
    cards = {s["id"]: _http_get_card(s["id"]) for s in active_specs}  # real A2A discovery
    return cards, _http_transport


# --- Conditions (headline subset for cloud validation) -------------------------

def _pool(all_specs, personas):
    return [s for s in all_specs if s["persona"] in personas]


def _params(value: float, kappa: float) -> Params:
    # Budget = 2*exec_cost + k_candidates*kappa + margin, so the router can always afford
    # to canary all k_candidates and still execute + retry. With the defaults
    # (exec_cost=1, k_candidates=4, margin=1) this is 3.0 + 4*kappa.
    return Params(value=value, canary_cost=kappa, budget=round(3.0 + 4.0 * kappa, 3))


def _pools(all_specs):
    # Pool composition MUST match scripts/local_run.pool_specs (the in-process evidence
    # base) so the cloud validates against the same conditions.
    # clean   : benign only (correct + degraded) — malice fraction 0.0
    # hostile : drop the second 'correct' agent per skill (org 'fabrikam') -> malice 0.5
    # mixed   : the full pool (2 correct, 1 degraded, 1 adversarial, 1 canary_aware /skill)
    return {
        "clean": _pool(all_specs, {"correct", "degraded"}),
        "hostile": [s for s in all_specs
                    if not (s["persona"] == "correct" and s["org"] == "fabrikam")],
        "mixed": all_specs,
    }


# The FULL 120-cell factorial (V x kappa x pool x canary) is the evidence base, generated
# in-process by scripts/local_run.py. The CLOUD run is a minimal distributed-A2A sanity
# check that the over-HTTP path reproduces the in-process numbers for the headline cells
# (RQ1/RQ4, RQ3, an RQ2 corner). (V, kappa, pool, canary):
_VALIDATION = [
    (5, 0.2, "mixed", "I"),     # RQ1 / RQ4 headline
    (5, 0.2, "mixed", "D"),     # RQ4 distinguishable (failure mode)
    (5, 0.2, "clean", "I"),     # RQ3 (honest degradation)
    (10, 0.05, "mixed", "I"),   # RQ2 high-value / cheap-probe corner
]


def _headline_conditions(all_specs):
    pools = _pools(all_specs)
    return [
        {
            "name": f"V{v}_k{k}_{pool}_{canary}",
            "specs": pools[pool],
            "indistinguishable": canary == "I",
            "params": _params(v, k),
        }
        for (v, k, pool, canary) in _VALIDATION
    ]


@app.route(route="experiment/run", methods=["POST"])
def run_experiment(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    seeds = list(range(int(body.get("seeds", 2))))
    n_tasks = int(body.get("tasks", 12))

    specs = _load_dataset("agents.json", "data/agents.json")
    tasks = _load_dataset("tasks.json", "data/tasks.json")[:n_tasks]
    conditions = _headline_conditions(specs)

    logging.info("Cloud run: %d conditions x %d tasks x %d seeds over A2A HTTP",
                 len(conditions), len(tasks), len(seeds))
    rows, raw = experiment.run(tasks, conditions, _build_env, seeds)
    # The validation subset is small (returned inline, no Blob persistence) — the full
    # 120-cell evidence base is the in-process job of scripts/local_run.py.
    return func.HttpResponse(json.dumps({"conditions": len(conditions), "runs": len(raw),
                                         "rows": rows}, indent=2),
                             mimetype="application/json")


# --- Demo: CCR vs no-CCR for one task, observable -------------------------------

_MALICIOUS = {"adversarial", "canary_aware"}


def _mermaid(policy: str, calls: list[dict], ev: dict) -> str:
    """A Mermaid sequenceDiagram of one policy's real A2A interaction. A REAL call to a
    malicious agent is the incident (your task data reaches it); a canary is synthetic."""
    alias: dict[str, str] = {}
    lines = ["sequenceDiagram", "    participant R as Router"]
    for c in calls:
        if c["agent"] not in alias:
            a = f"A{len(alias)}"
            alias[c["agent"]] = a
            tag = " [MALICIOUS]" if c["persona"] in _MALICIOUS else ""
            lines.append(f"    participant {a} as {c['persona']}{tag}")
    for c in calls:
        a = alias[c["agent"]]
        if c["kind"] == "canary":
            lines.append(f"    R->>{a}: canary probe (synthetic, side-effect-free)")
            lines.append(f"    {a}-->>R: probe answer")
        else:
            lines.append(f"    R->>{a}: REAL task - ships your data")
            lines.append(f"    {a}-->>R: result")
            if c["persona"] in _MALICIOUS:
                lines.append(f"    Note over R,{a}: INCIDENT - real data reached a malicious agent")
    lines.append(f"    Note over R: {policy}: success={ev['success']}, "
                 f"{'INCIDENT' if ev['incident'] else 'no incident'}")
    return "\n".join(lines)


def _trace_one(pol, task, cards, transport, persona, spec_by_id, params, seed, indist):
    """Run one policy for one task over real A2A, recording every call (and emitting an
    enriched App Insights trace per call) and returning the annotated result + Mermaid."""
    calls: list[dict] = []

    def traced(agent_id, request):
        resp = transport(agent_id, request)
        kind = request.get("kind")
        calls.append({"agent": agent_id, "persona": persona.get(agent_id),
                      "kind": kind, "payload": request.get("payload"),
                      "latencyMs": resp.get("latencyMs")})
        logging.info("A2A %s -> %s", kind, agent_id, extra={"custom_dimensions": {
            "policy": pol, "kind": kind, "targetAgent": agent_id,
            "targetPersona": persona.get(agent_id), "payload": request.get("payload"),
            "task": task["id"]}})
        return resp

    dec = POLICIES[pol](Context(task, cards, traced, params, seed, indist))
    ev = metrics.evaluate(dec, spec_by_id, params)
    if ev["incident"]:
        logging.warning("INCIDENT policy=%s task=%s chose=%s", pol, task["id"], dec.chosen,
                        extra={"custom_dimensions": {"policy": pol, "task": task["id"],
                                                     "isIncident": True}})
    return {"policy": pol, "chosen": dec.chosen, "chosen_persona": persona.get(dec.chosen),
            "success": ev["success"], "incident": ev["incident"], "defection": ev["defection"],
            "n_canaries": len(dec.canaried), "n_real_calls": len(dec.real_attempts),
            "calls": calls, "mermaid": _mermaid(pol, calls, ev)}


@app.route(route="demo/compare", methods=["POST", "GET"])
def demo_compare(req: func.HttpRequest) -> func.HttpResponse:
    """Side-by-side trace of CCR vs a no-CCR baseline for ONE task over real A2A.

    Body (all optional): { "pool": "mixed", "policies": ["optimistic_fallback","ccr"],
    "task": "t0007" | "taskIndex": 7, "V": 5, "kappa": 0.2, "indistinguishable": true }.
    Returns, per policy: the ordered A2A calls (agent, persona, kind=canary|task, the
    payload sent), the incident label, and a Mermaid sequence diagram. Also emits enriched
    App Insights traces so the same interaction is visible in the portal."""
    try:
        body = req.get_json()
    except ValueError:
        body = {}
    pool_name = body.get("pool", "mixed")
    policies = [p for p in body.get("policies", ["optimistic_fallback", "ccr"]) if p in POLICIES]
    V, kappa = float(body.get("V", 5)), float(body.get("kappa", 0.2))
    indist = bool(body.get("indistinguishable", True))
    seed = int(body.get("seed", 0))

    all_specs = _load_dataset("agents.json", "data/agents.json")
    tasks = _load_dataset("tasks.json", "data/tasks.json")
    specs = _pools(all_specs).get(pool_name)
    if specs is None or not policies:
        return func.HttpResponse(
            json.dumps({"error": f"unknown pool '{pool_name}' or no valid policies"}),
            status_code=400, mimetype="application/json")
    persona = {s["id"]: s["persona"] for s in all_specs}
    spec_by_id = {s["id"]: s for s in specs}
    cards, transport = _build_env(specs)
    params = _params(V, kappa)

    # Task: an explicit one, else auto-pick the first (of a few) where the policies DIFFER
    # on incident — the teaching case (no-CCR exposes real data, CCR does not).
    if body.get("task") or "taskIndex" in body:
        candidates = [next((t for t in tasks if t["id"] == body.get("task")), None)
                      or tasks[int(body.get("taskIndex", 0)) % len(tasks)]]
    else:
        candidates = tasks[:8]
    # Prefer the TEACHING case — a no-CCR baseline incurs an incident that CCR avoids —
    # over CCR's rare residual (a lucky defector beats the probe). Fall back to the first
    # candidate. This only selects which task to *show*; aggregate rates are in RQ1.
    picked = teaching = None
    for cand in candidates:
        results = [_trace_one(p, cand, cards, transport, persona, spec_by_id, params, seed, indist)
                   for p in policies]
        picked = picked or (cand, results)
        inc = {r["policy"]: r["incident"] for r in results}
        if inc.get("ccr") is False and any(v for k, v in inc.items() if k != "ccr"):
            teaching = (cand, results)
            break
    task, results = teaching or picked

    return func.HttpResponse(json.dumps({
        "task": task["id"], "skill": task["skill"], "pool": pool_name, "V": V, "kappa": kappa,
        "indistinguishable": indist,
        "summary": " | ".join(f"{r['policy']}: success={r['success']}, "
                              f"{'INCIDENT' if r['incident'] else 'no incident'}" for r in results),
        "policies": results,
    }, indent=2), mimetype="application/json")


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps({"status": "ok", "agentBaseUrl": AGENT_BASE_URL}),
                             mimetype="application/json")

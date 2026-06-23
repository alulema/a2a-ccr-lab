"""CCR Router / Experiment Harness (Azure Functions, Flex Consumption).

Drives the policy suite over REAL A2A HTTP calls against the agent host, and
writes results to Blob Storage. This is the distributed validation of the numbers
produced locally by scripts/local_run.py (identical, because everything is
deterministic).

  GET  /api/health
  POST /api/experiment/run   body: { "seeds": 2, "tasks": 12 }  (optional)

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
import urllib.error
import urllib.request

import azure.functions as func

from ccrlib import experiment
from ccrlib.scoring import Params

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "http://localhost:7071/api")


# --- Storage helpers -----------------------------------------------------------

def _blob_client(container: str, name: str):
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    account = os.environ["DATA_STORAGE_ACCOUNT"]
    url = f"https://{account}.blob.core.windows.net"
    svc = BlobServiceClient(url, credential=DefaultAzureCredential())
    return svc.get_blob_client(container, name)


def _load_dataset(name: str, local_rel: str):
    # Prefer the dataset bundled inside the app (copied by scripts/sync_lib); fall
    # back to Blob (if seeded), then the repo copy for local `func start`.
    bundled = os.path.join(os.path.dirname(__file__), "data", name)
    if os.path.exists(bundled):
        with open(bundled, encoding="utf-8") as f:
            return json.load(f)
    if os.environ.get("DATA_STORAGE_ACCOUNT"):
        return json.loads(_blob_client("data", name).download_blob().readall())
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", local_rel))
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json_blob(container: str, name: str, obj) -> None:
    if os.environ.get("DATA_STORAGE_ACCOUNT"):
        _blob_client(container, name).upload_blob(json.dumps(obj, indent=2), overwrite=True)


# --- A2A HTTP transport --------------------------------------------------------

# The agent host is a separately-scaling Function App. A burst of calls can trigger
# its scale-out, and a request landing on a still-cold instance stalls to the client
# timeout. Retry transient TRANSPORT faults (timeouts, connection errors, 5xx/429) so a
# platform autoscale event doesn't corrupt a data point — a real A2A client would. This
# never retries an agent's own JSON-RPC response (returned as-is for the evaluator); a
# transport timeout must not be mislabeled as agent behavior. See Devlog D3.
_RETRY_ATTEMPTS = 4
_RETRY_BACKOFF_S = 2.0
_RETRY_HTTP_CODES = {429, 500, 502, 503, 504}


def _http_json(url: str, data: bytes | None = None, timeout: int = 30) -> dict:
    headers = {"Content-Type": "application/json"} if data is not None else {}
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_HTTP_CODES:
                raise  # a real client/server error — not a transient transport fault
            last_exc = e
        except (urllib.error.URLError, TimeoutError) as e:  # incl. socket timeout
            last_exc = e
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
    data = json.dumps(rpc).encode("utf-8")
    return _http_json(url, data=data, timeout=60)["result"]


def _build_env(active_specs: list[dict]):
    cards = {s["id"]: _http_get_card(s["id"]) for s in active_specs}  # real A2A discovery
    return cards, _http_transport


# --- Conditions (headline subset for cloud validation) -------------------------

def _pool(all_specs, personas):
    return [s for s in all_specs if s["persona"] in personas]


def _headline_conditions(all_specs):
    mixed = all_specs
    clean = _pool(all_specs, {"correct", "degraded"})
    return [
        {"name": "V3_k0.2_mixed_I", "specs": mixed, "indistinguishable": True,
         "params": Params(value=3, canary_cost=0.2, budget=3.8)},
        {"name": "V3_k0.2_mixed_D", "specs": mixed, "indistinguishable": False,
         "params": Params(value=3, canary_cost=0.2, budget=3.8)},
        {"name": "V10_k0.05_mixed_I", "specs": mixed, "indistinguishable": True,
         "params": Params(value=10, canary_cost=0.05, budget=3.2)},
        {"name": "V3_k0.2_clean_I", "specs": clean, "indistinguishable": True,
         "params": Params(value=3, canary_cost=0.2, budget=3.8)},
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

    _save_json_blob("results", "rows.json", rows)
    _save_json_blob("results", "raw.json", raw)
    return func.HttpResponse(json.dumps({"conditions": len(conditions), "runs": len(raw),
                                         "rows": rows}, indent=2),
                             mimetype="application/json")


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps({"status": "ok", "agentBaseUrl": AGENT_BASE_URL}),
                             mimetype="application/json")

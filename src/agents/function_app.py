"""A2A Agent Host (Azure Functions, Flex Consumption).

Hosts every simulated agent as an A2A endpoint:
  GET  /api/agents/{agentId}/.well-known/agent-card.json   -> AgentCard
  POST /api/agents/{agentId}/a2a                            -> JSON-RPC message/send

Agent specs are loaded from Blob Storage (container 'data', blob 'agents.json'),
seeded by scripts/seed_data.ps1. Falls back to the repo's local data file when
running with `func start`.
"""
from __future__ import annotations

import json
import logging
import os

import azure.functions as func

from ccrlib.agents import SimAgent

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

_AGENTS: dict[str, SimAgent] | None = None


def _load_specs() -> list[dict]:
    # 1) Bundled dataset shipped inside the app (copied by scripts/sync_lib at
    #    package time) — the default, needs no Storage/RBAC.
    bundled = os.path.join(os.path.dirname(__file__), "data", "agents.json")
    if os.path.exists(bundled):
        with open(bundled, encoding="utf-8") as f:
            return json.load(f)
    # 2) Optional Blob-backed dataset (if DATA_STORAGE_ACCOUNT is set and seeded).
    account = os.environ.get("DATA_STORAGE_ACCOUNT")
    if account:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        url = f"https://{account}.blob.core.windows.net"
        client = BlobServiceClient(url, credential=DefaultAzureCredential())
        blob = client.get_blob_client(os.environ.get("DATA_CONTAINER", "data"), "agents.json")
        return json.loads(blob.download_blob().readall())
    # 3) Repo fallback (local `func start`).
    repo = os.path.join(os.path.dirname(__file__), "..", "..", "data", "agents.json")
    with open(os.path.abspath(repo), encoding="utf-8") as f:
        return json.load(f)


def _agents() -> dict[str, SimAgent]:
    global _AGENTS
    if _AGENTS is None:
        _AGENTS = {s["id"]: SimAgent(s) for s in _load_specs()}
        logging.info("Loaded %d agents", len(_AGENTS))
    return _AGENTS


@app.route(route="agents/{agentId}/.well-known/agent-card.json", methods=["GET"])
def agent_card(req: func.HttpRequest) -> func.HttpResponse:
    agent = _agents().get(req.route_params.get("agentId"))
    if agent is None:
        return func.HttpResponse("agent not found", status_code=404)
    return func.HttpResponse(json.dumps(agent.card()), mimetype="application/json")


@app.route(route="agents/{agentId}/a2a", methods=["POST"])
def a2a(req: func.HttpRequest) -> func.HttpResponse:
    agent = _agents().get(req.route_params.get("agentId"))
    if agent is None:
        return func.HttpResponse("agent not found", status_code=404)
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("invalid JSON", status_code=400)

    # A2A JSON-RPC: { jsonrpc, id, method: "message/send", params: {...} }
    if body.get("method") != "message/send":
        return _rpc_error(body.get("id"), -32601, "method not found")
    p = body.get("params", {})
    # Observability: log what this agent received, tagged with its (hidden) persona, so the
    # App Insights traces show a malicious agent getting a REAL task payload (an incident)
    # vs only a synthetic canary. The router never sees this; it is for the demo/operator.
    logging.info("agent %s received %s", agent.id, p.get("kind", "task"),
                 extra={"custom_dimensions": {
                     "agentId": agent.id, "persona": agent.persona,
                     "kind": p.get("kind", "task"), "payloadReceived": p.get("payload"),
                     "taskId": p.get("taskId")}})
    try:
        result = agent.execute({
            "kind": p.get("kind", "task"),
            "taskId": p["taskId"],
            "skill": p["skill"],
            "payload": p["payload"],
            "runSeed": p["runSeed"],
            "attempt": p.get("attempt", 0),
            "canaryIndistinguishable": p.get("canaryIndistinguishable", True),
        })
    except KeyError as e:
        return _rpc_error(body.get("id"), -32602, f"missing param: {e}")
    return func.HttpResponse(
        json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result}),
        mimetype="application/json",
    )


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps({"status": "ok", "agents": len(_agents())}),
                             mimetype="application/json")


def _rpc_error(rpc_id, code: int, message: str) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}),
        mimetype="application/json", status_code=200,
    )

"""ccrlib — Card+Canary Routing experimental core.

Pure-Python, deterministic, dependency-free scientific core shared by the local
runner (scripts/local_run.py) and the Azure Functions wrappers (src/agents,
src/router). Determinism is load-bearing: the same (run_seed, task, agent) tuple
yields identical outcomes locally and in the cloud, so the cloud deployment is a
*distributed validation* of the exact numbers produced locally.

Design invariant (do not break): policies in `policies.py` interact with agents
ONLY through a `transport(agent_id, request) -> observable_dict` callable. They
never import `agents.SimAgent` internals, so the router under test cannot read an
agent's hidden true capability. This is what makes the evidence credible.
"""

__version__ = "0.1.0"

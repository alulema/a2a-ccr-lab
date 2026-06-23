# Deploying a2a-ccr-lab from Ubuntu (Azure CLI + Functions Core Tools)

No `azd` required — this path uses only `az` and `func`, which you already have.

## Prerequisites

```bash
az version          # Azure CLI
func --version      # Azure Functions Core Tools v4
python3 --version   # 3.11+
jq --version        # sudo apt install jq   (if missing)
az login            # interactive, in your terminal
az account set --subscription "<the subscription that owns your RG>"
```

> **Permissions:** the default flow is **subscription-scoped** — the Bicep CREATES
> the resource group and everything in it, so you can spin the whole lab up on
> demand and delete it when done. You need subscription permission to create RGs +
> deployments (you have this on a subscription where you can create RGs).
>
> If you only have **Contributor on a pre-existing RG** (no subscription rights),
> use the group-scoped alternative at the bottom instead.

## One command (ephemeral: creates the RG, you delete it later)

```bash
git clone <your-repo-url> a2a-ccr-lab && cd a2a-ccr-lab
bash scripts/deploy.sh <azure-region> [resource-group] [env-name]
# e.g.
bash scripts/deploy.sh eastus
# (defaults: resource-group = a2a-ccr-lab, env-name = ccrlab)
```

`deploy.sh` will:
1. **Provision** `infra/main.bicep` (`az deployment sub create`): **creates the RG
   `a2a-ccr-lab`**, then keyless Storage, App Insights + Log Analytics, a Flex
   Consumption plan, and the two Function Apps with managed identity + RBAC.
2. **Bundle** `ccrlib` + the dataset into each app (`scripts/sync_lib.sh`).
3. **Publish** both apps with `func azure functionapp publish`.
4. Save deployment coordinates to `.deploy.env`.

> Pick a region where **Flex Consumption** + Linux Python are available (e.g.
> `eastus`, `westus2`, `northeurope`).

### Tear everything down

The whole lab is disposable — one command removes all of it:

```bash
az group delete --name a2a-ccr-lab --yes
```

### Alternative: deploy into a pre-existing RG (group scope, fewer privileges)

```bash
az deployment group create \
  --resource-group <existing-rg> \
  --template-file infra/resources.bicep \
  --parameters environmentName=ccrlab location=<azure-region>
# then bundle + publish manually:
sh scripts/sync_lib.sh
(cd src/agents && func azure functionapp publish <agents-app-name>)
(cd src/router && func azure functionapp publish <router-app-name>)
```

## Run the distributed experiment

```bash
bash scripts/run_experiment.sh 2 12     # seeds=2, tasks=12
```

This POSTs to the router, which discovers each agent's card over HTTP and runs the
policy suite via **real A2A JSON-RPC calls** to the agent host. Because the core is
deterministic, the cloud numbers match `scripts/local_run.py`. Results are also
written to the Storage `results` container.

Verify an individual agent card directly:
```bash
curl -s "$(grep AGENTS_URL .deploy.env | cut -d= -f2)/agents/corr-rag-contoso-0/.well-known/agent-card.json" | jq .
```

## Observability

Traces (routing decisions, canary calls) flow to Application Insights. In the portal:
*Resource group → Application Insights → Transaction search / Logs*.

## Cost & teardown

Flex Consumption + Storage + App Insights for a short experiment is a few cents.
Tear everything down with:

```bash
az group delete --name rg-<env-name> --yes --no-wait
```

## Troubleshooting

- **403 writing results:** the router's managed-identity RBAC can take ~1–2 min to
  propagate after provisioning. Re-run `run_experiment.sh`.
- **`func publish` cold first run:** the first invocation may be slow while the app
  warms; re-run if the router times out calling agents.
- **Python build:** Flex Consumption builds remotely; ensure `requirements.txt` is
  intact and `ccrlib/` + `data/` were vendored (re-run `sh scripts/sync_lib.sh`).

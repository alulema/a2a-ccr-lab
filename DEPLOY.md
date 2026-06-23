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

> **Permissions:** this deploys at **resource-group scope**, so you only need
> **Contributor on the RG** — not on the whole subscription. (Subscription-scoped
> deployment is blocked on many corporate subs; that's why we don't use it.)

## One command

Pass your **existing resource group** as the 3rd argument:

```bash
git clone <your-repo-url> a2a-ccr-lab && cd a2a-ccr-lab
bash scripts/deploy.sh <env-name> <azure-region> <resource-group>
# e.g. with the RG you created:
bash scripts/deploy.sh ccrlab eastus a2a-ccr-lab
```

(If the RG doesn't exist and you have permission, the script creates it. If you
omit the 3rd arg it defaults to `rg-<env-name>`.)

`deploy.sh` will:
1. **Provision** `infra/resources.bicep` into your RG (`az deployment group create`):
   keyless Storage, App Insights + Log Analytics, a Flex Consumption plan, and the
   two Function Apps with managed identity + RBAC.
2. **Bundle** `ccrlib` + the dataset into each app (`scripts/sync_lib.sh`).
3. **Publish** both apps with `func azure functionapp publish`.
4. Save deployment coordinates to `.deploy.env`.

> Pick a region where **Flex Consumption** + Linux Python are available (e.g.
> `eastus`, `westus2`, `northeurope`). The resource region is the `<azure-region>`
> argument and need not match your RG's metadata location.

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

#!/usr/bin/env bash
# One-shot deploy from Ubuntu using Azure CLI + Functions Core Tools (no azd).
#   bash scripts/deploy.sh <env-name> <azure-region> [resource-group]
# Example:
#   bash scripts/deploy.sh ccr-lab eastus
#
# Deploys at RESOURCE-GROUP scope (needs only Contributor on the RG — not on the
# whole subscription). If you DO have subscription Contributor, you can instead use
# the subscription-scoped template: az deployment sub create --template-file
# infra/main.bicep --location <region> --parameters environmentName=<env> location=<region>
set -euo pipefail

ENV_NAME="${1:-ccr-lab}"
LOCATION="${2:-${AZURE_LOCATION:-}}"
RG="${3:-rg-${ENV_NAME}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -z "$LOCATION" ]; then
  echo "Usage: bash scripts/deploy.sh <env-name> <azure-region> [resource-group]" >&2
  exit 1
fi
command -v jq >/dev/null || { echo "Please install jq (sudo apt install jq)"; exit 1; }
command -v func >/dev/null || { echo "Azure Functions Core Tools (func) not found"; exit 1; }

echo ">> Account: $(az account show --query name -o tsv)"
if ! az group show -n "$RG" >/dev/null 2>&1; then
  echo ">> Creating resource group $RG ..."
  az group create -n "$RG" -l "$LOCATION" -o none \
    || { echo "Could not create RG '$RG'. Ask an admin to pre-create it and pass it as the 3rd arg."; exit 1; }
fi

echo ">> Provisioning infra into $RG (env=$ENV_NAME, location=$LOCATION) ..."
OUT=$(az deployment group create \
  --resource-group "$RG" \
  --name "ccr-${ENV_NAME}" \
  --template-file "$ROOT/infra/resources.bicep" \
  --parameters environmentName="$ENV_NAME" location="$LOCATION" \
  --query properties.outputs -o json)

AGENTS_NAME=$(echo "$OUT" | jq -r .SERVICE_AGENTS_NAME.value)
ROUTER_NAME=$(echo "$OUT" | jq -r .SERVICE_ROUTER_NAME.value)
ROUTER_URL=$(echo "$OUT" | jq -r .ROUTER_BASE_URL.value)
AGENTS_URL=$(echo "$OUT" | jq -r .AGENTS_BASE_URL.value)
echo ">> Provisioned. RG=$RG  agents=$AGENTS_NAME  router=$ROUTER_NAME"

echo ">> Bundling ccrlib + dataset into apps ..."
sh "$ROOT/scripts/sync_lib.sh"

echo ">> Publishing agent host ..."
( cd "$ROOT/src/agents" && func azure functionapp publish "$AGENTS_NAME" )
echo ">> Publishing router ..."
( cd "$ROOT/src/router" && func azure functionapp publish "$ROUTER_NAME" )

# Persist deployment coordinates for run_experiment.sh (gitignored).
cat > "$ROOT/.deploy.env" <<EOF
ROUTER_NAME=$ROUTER_NAME
AGENTS_NAME=$AGENTS_NAME
ROUTER_URL=$ROUTER_URL
AGENTS_URL=$AGENTS_URL
RG=$RG
EOF

echo ""
echo ">> Done. Agent cards live at: ${AGENTS_URL}/agents/<id>/.well-known/agent-card.json"
echo ">> Run the distributed experiment with:"
echo "     bash scripts/run_experiment.sh"

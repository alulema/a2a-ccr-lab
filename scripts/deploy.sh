#!/usr/bin/env bash
# One-shot, ephemeral deploy from Ubuntu using Azure CLI + Functions Core Tools.
#   bash scripts/deploy.sh <azure-region> [resource-group] [env-name]
# Example:
#   bash scripts/deploy.sh eastus
#
# Subscription-scoped: the Bicep CREATES the resource group (default a2a-ccr-lab)
# and everything in it. Tear it all down when done with:
#   az group delete --name <resource-group> --yes
#
# Needs subscription permission to create RGs + deployments. If you only have
# Contributor on a pre-existing RG, deploy infra/resources.bicep at group scope
# (az deployment group create --resource-group <rg> ...) — see DEPLOY.md.
set -euo pipefail

LOCATION="${1:-${AZURE_LOCATION:-}}"
RG="${2:-a2a-ccr-lab}"
ENV_NAME="${3:-ccrlab}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -z "$LOCATION" ]; then
  echo "Usage: bash scripts/deploy.sh <azure-region> [resource-group] [env-name]" >&2
  exit 1
fi
command -v jq >/dev/null || { echo "Please install jq (sudo apt install jq)"; exit 1; }
command -v func >/dev/null || { echo "Azure Functions Core Tools (func) not found"; exit 1; }

echo ">> Account: $(az account show --query name -o tsv)"
echo ">> Provisioning (creates RG '$RG', env=$ENV_NAME, location=$LOCATION) ..."
OUT=$(az deployment sub create \
  --name "ccr-${ENV_NAME}" \
  --location "$LOCATION" \
  --template-file "$ROOT/infra/main.bicep" \
  --parameters environmentName="$ENV_NAME" location="$LOCATION" resourceGroupName="$RG" \
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
echo ">> Tear EVERYTHING down when finished with:"
echo "     az group delete --name ${RG} --yes"

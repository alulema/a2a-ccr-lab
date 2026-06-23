#!/usr/bin/env bash
# Trigger the cloud experiment on the deployed router (real A2A HTTP calls) and
# print the aggregated rows. Reads coordinates from .deploy.env (written by deploy.sh).
#   bash scripts/run_experiment.sh [seeds] [tasks]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SEEDS="${1:-2}"
TASKS="${2:-12}"

[ -f "$ROOT/.deploy.env" ] || { echo "Run scripts/deploy.sh first (.deploy.env missing)"; exit 1; }
# shellcheck disable=SC1091
. "$ROOT/.deploy.env"

KEY=$(az functionapp keys list -g "$RG" -n "$ROUTER_NAME" --query "functionKeys.default" -o tsv)
echo ">> POST ${ROUTER_URL}/experiment/run  (seeds=$SEEDS tasks=$TASKS) ..."
curl -s -X POST "${ROUTER_URL}/experiment/run?code=${KEY}" \
  -H 'Content-Type: application/json' \
  -d "{\"seeds\":${SEEDS},\"tasks\":${TASKS}}" \
| jq '{conditions, runs, rows: [.rows[] | {condition, policy, success_rate, mean_net_utility, defection_rate, incident_rate}]}'

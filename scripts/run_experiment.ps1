# Triggers the cloud experiment on the deployed router (real A2A HTTP calls) and
# prints the aggregated rows. Results are also written to the 'results' container.
param(
    [int]$Seeds = 2,
    [int]$Tasks = 12
)
$ErrorActionPreference = "Stop"

$router = (azd env get-value SERVICE_ROUTER_NAME)
$rg = (azd env get-value AZURE_RESOURCE_GROUP)
$base = (azd env get-value ROUTER_BASE_URL)
$key = az functionapp keys list -g $rg -n $router --query "functionKeys.default" -o tsv

$body = @{ seeds = $Seeds; tasks = $Tasks } | ConvertTo-Json
$resp = Invoke-RestMethod -Method Post -Uri "$base/experiment/run?code=$key" `
    -Body $body -ContentType "application/json"
Write-Host "Conditions: $($resp.conditions)  Runs: $($resp.runs)"
$resp.rows | Format-Table condition, policy, success_rate, mean_net_utility, defection_rate, incident_rate

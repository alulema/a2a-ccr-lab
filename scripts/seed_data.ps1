# Uploads the benchmark dataset (data/tasks.json, data/agents.json) to the 'data'
# blob container using your Entra ID identity (managed-identity-style RBAC).
# Invoked by the azd `postprovision` hook, or run manually.
param(
    [string]$StorageAccount
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not $StorageAccount) {
    $StorageAccount = (azd env get-value DATA_STORAGE_ACCOUNT 2>$null)
}
if (-not $StorageAccount) {
    throw "Provide -StorageAccount or run inside an azd environment (azd env get-value DATA_STORAGE_ACCOUNT)."
}

# Ensure the dataset exists.
if (-not (Test-Path (Join-Path $root "data/agents.json"))) {
    python (Join-Path $root "scripts/gen_dataset.py")
}

foreach ($f in @("agents.json", "tasks.json")) {
    az storage blob upload `
        --account-name $StorageAccount `
        --auth-mode login `
        --container-name data `
        --name $f `
        --file (Join-Path $root "data/$f") `
        --overwrite | Out-Null
    Write-Host "Uploaded data/$f -> $StorageAccount/data/$f"
}
Write-Host "Seed complete. Note: blob data-plane RBAC may take a minute to propagate."

# Vendors the ccrlib core AND the dataset into each Function App folder so they
# ship with the deployment package (no Storage/RBAC needed to read the dataset).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "data/agents.json"))) {
    python (Join-Path $root "scripts/gen_dataset.py")
}
foreach ($app in @("agents", "router")) {
    $appDir = Join-Path $root "src/$app"
    foreach ($d in @("ccrlib", "data")) {
        $dest = Join-Path $appDir $d
        if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    }
    Copy-Item -Recurse (Join-Path $root "ccrlib") (Join-Path $appDir "ccrlib")
    New-Item -ItemType Directory -Force (Join-Path $appDir "data") | Out-Null
    Copy-Item (Join-Path $root "data/agents.json") (Join-Path $appDir "data/agents.json")
    Copy-Item (Join-Path $root "data/tasks.json") (Join-Path $appDir "data/tasks.json")
    Write-Host "Vendored ccrlib + data -> src/$app/"
}

#!/usr/bin/env sh
# Vendors the ccrlib core AND the dataset into each Function App folder so they
# ship with the deployment package (no Storage/RBAC needed to read the dataset).
# Run before `func azure functionapp publish` (or via the azd prepackage hook).
set -e
root="$(cd "$(dirname "$0")/.." && pwd)"

# Ensure a dataset exists to bundle.
[ -f "$root/data/agents.json" ] || python3 "$root/scripts/gen_dataset.py"

for app in agents router; do
  rm -rf "$root/src/$app/ccrlib" "$root/src/$app/data"
  cp -r "$root/ccrlib" "$root/src/$app/ccrlib"
  mkdir -p "$root/src/$app/data"
  cp "$root/data/agents.json" "$root/data/tasks.json" "$root/src/$app/data/"
  echo "Vendored ccrlib + data -> src/$app/"
done

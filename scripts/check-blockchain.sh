#!/usr/bin/env bash
set -euo pipefail

task_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_repo_root"

if [[ -n "${FORGE_BIN:-}" ]]; then
    task_forge="$FORGE_BIN"
elif command -v forge >/dev/null 2>&1; then
    task_forge="$(command -v forge)"
elif [[ -x "${HOME}/.foundry/bin/forge" ]]; then
    task_forge="${HOME}/.foundry/bin/forge"
else
    echo "Foundry is missing. See docs/BLOCKCHAIN_RUNBOOK.md." >&2
    exit 1
fi

"$task_forge" fmt --check
"$task_forge" build
"$task_forge" build --sizes
"$task_forge" test -vv
"$task_forge" lint contracts/src --report-unused-suppressions --deny warnings
python3 scripts/check-abis.py "$task_forge"
python3 scripts/check-m1-baseline.py
python3 scripts/test-push-guard.py
git diff --check
git diff --cached --check

#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/../../.." && pwd)
cd "$REPO_ROOT"
if [ ! -x .local/runtime/bin/python ]; then
  echo "Missing project-local runtime; see services/api/README.md" >&2
  exit 2
fi
if [ -z "${POG_TEST_DATABASE_URL:-}" ]; then
  services/api/scripts/postgres-local.sh up >/dev/null
  # shellcheck disable=SC1091
  . .local/postgres/connection.env
fi
export POG_DATABASE_URL="$POG_TEST_DATABASE_URL"
export POG_STORAGE_ROOT="$REPO_ROOT/.local/test-storage"
export POG_A1_RUN_ID="a1-test-run"
export POG_A1_INSTANCE_ID="a1-test-instance"
export POG_ALLOW_TEST_DB_RESET=1
export POG_MANAGED_POSTGRES_STATE="$REPO_ROOT/.local/postgres"
export PYTHONPATH="$REPO_ROOT/services/api/src"
.local/runtime/bin/python services/api/scripts/reset-test-db.py
(
  cd services/api
  ../../.local/runtime/bin/alembic upgrade head
  ../../.local/runtime/bin/python -m pytest
)

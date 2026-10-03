#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/../../.." && pwd)
RUNTIME="$REPO_ROOT/.local/runtime"
STATE="$REPO_ROOT/.local/postgres"
PGDATA="$STATE/data"
SOCKET="$STATE/socket"
LOG="$STATE/postgres.log"
PORT="${POG_POSTGRES_PORT:-55432}"
PG_CTL="$RUNTIME/bin/pg_ctl"
INITDB="$RUNTIME/bin/initdb"
PYTHON="$RUNTIME/bin/python"

case "$PORT" in
  *[!0-9]*|'') echo "POG_POSTGRES_PORT must be numeric" >&2; exit 2 ;;
esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then
  echo "POG_POSTGRES_PORT must be between 1024 and 65535" >&2
  exit 2
fi
if [ ! -x "$PG_CTL" ] || [ ! -x "$INITDB" ]; then
  echo "Project-local PostgreSQL runtime is missing from .local/runtime" >&2
  exit 2
fi
mkdir -p "$STATE" "$SOCKET"
chmod 700 "$STATE" "$SOCKET"

status() {
  if [ -f "$PGDATA/PG_VERSION" ] && "$PG_CTL" -D "$PGDATA" status >/dev/null 2>&1; then
    echo "PoG local PostgreSQL is running on 127.0.0.1:$PORT"
    return 0
  fi
  echo "PoG local PostgreSQL is stopped"
  return 1
}

case "${1:-}" in
  up)
    if [ ! -f "$PGDATA/PG_VERSION" ]; then
      "$INITDB" -D "$PGDATA" -U pog_admin --encoding=UTF8 --locale=C \
        --auth-local=trust --auth-host=scram-sha-256 >/dev/null
    elif [ "$(cut -d. -f1 "$PGDATA/PG_VERSION")" != "17" ]; then
      echo "Refusing to use non-PostgreSQL-17 cluster at $PGDATA" >&2
      exit 2
    fi
    if ! "$PG_CTL" -D "$PGDATA" status >/dev/null 2>&1; then
      "$PG_CTL" -D "$PGDATA" -l "$LOG" \
        -o "-h 127.0.0.1 -p $PORT -k $SOCKET" start -w
    fi
    "$PYTHON" "$REPO_ROOT/services/api/scripts/init-local-db.py" \
      --socket "$SOCKET" --port "$PORT" --state "$STATE"
    status
    ;;
  status)
    status
    ;;
  stop)
    if [ ! -f "$PGDATA/PG_VERSION" ]; then
      echo "No managed cluster exists"
      exit 0
    fi
    if "$PG_CTL" -D "$PGDATA" status >/dev/null 2>&1; then
      "$PG_CTL" -D "$PGDATA" stop -m fast -w
    else
      echo "PoG local PostgreSQL is already stopped"
    fi
    ;;
  *)
    echo "Usage: $0 {up|status|stop}" >&2
    exit 2
    ;;
esac

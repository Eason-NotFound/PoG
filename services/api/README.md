# PoG API A1

This is the A1 HTTP/database/file/session foundation for PoG FinTech #2. It is a
loopback-only demo service. Project and procurement mutations create off-chain
drafts only. Chain submission, EIP-712 signing, AI assessment, payment execution,
and confirmed donation/release/settlement states are deliberately unavailable.

The frozen scope is in `docs/api/A0_REQUIREMENTS.md` and `docs/api/A1_TASK.md`.
The operator and interface handoff is `docs/api/A1_RUNBOOK.md`.

## Reproducible local runtime (macOS arm64)

The committed Conda lock contains Python 3.13.15 and PostgreSQL 17.11. The pip
lock contains every Python package used by the implementation and tests.

```sh
conda create --prefix .local/runtime --file services/api/conda-osx-arm64.lock
.local/runtime/bin/python -m pip install --requirement services/api/requirements.lock
services/api/scripts/postgres-local.sh up
. .local/postgres/connection.env
export POG_STORAGE_ROOT="$PWD/.local/api-storage"
export POG_A1_RUN_ID=a1-local-run
export POG_A1_INSTANCE_ID=a1-local-instance
(cd services/api && ../../.local/runtime/bin/alembic upgrade head)
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api
```

The API binds to `127.0.0.1:8080`. OpenAPI is at
`http://127.0.0.1:8080/docs`; readiness is at `/ready`. The local PostgreSQL
helper also binds to loopback only and does not install a system service.

## Tests

```sh
services/api/scripts/test.sh
```

The test runner recreates only the managed loopback database named
`pog_api_test`. Its destructive guard rejects URL query parameters, fragments,
non-loopback hosts, other users, other database names, and an invalid managed
marker before executing any SQL. It never falls back to SQLite.

## Demo fixture seed

Copy `services/api/.env.example` to an ignored environment file, replace every
password and wallet placeholder, export it, then run:

```sh
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api.cli \
  seed-demo --confirm-demo-fixtures
```

Seed is opt-in and idempotent. It creates separate Foundation, Recipient, Donor,
and human approver identities. It never rotates an existing password or rewrites
an existing role-wallet mapping. Do not commit the environment file, credentials,
database, sessions, or uploaded evidence.

## Stop local PostgreSQL

```sh
services/api/scripts/postgres-local.sh stop
```

Stopping the A1 PostgreSQL process does not touch the existing Anvil process.

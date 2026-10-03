# PoG API A2

This keeps the accepted A1 HTTP/database/file/session foundation and adds an
explicitly enabled, loopback-only A2 adapter for the accepted V2 contracts. A1
draft creation remains off-chain. A2 chain operations are separately queued and
processed by durable worker/indexer commands. Real AI, payment, conversion,
release, settlement, closing and refund execution remain unavailable.

The frozen A2 scope is in `docs/api/A2_SPEC.md`; account names are fixed by
`docs/api/ACCOUNT_PREFERENCES.md`. See `docs/api/A2_RUNBOOK.md` and
`docs/api/A2_INTERFACE.md` for the operator and HTTP handoff.

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

## Demo fixture seed and accepted username migration

Copy `services/api/.env.example` to an ignored environment file, replace every
password and wallet placeholder, export it, then run:

```sh
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api.cli \
  seed-demo --confirm-demo-fixtures
```

Seed is opt-in and idempotent. Its standard usernames are `foundation`,
`recipient`, `donor`, and `admin`; `admin` has only the internal
`human_approver` role. It never rotates an existing password or rewrites an
existing role-wallet mapping. Before seeding a database containing the old A1
demo names, run the explicit transactional rename:

```sh
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api.cli \
  migrate-standard-usernames --confirm-standard-usernames
```

The rename preserves IDs, password hashes, sessions, role-wallets and all
foreign-key relationships. Any target-name conflict aborts the entire rename.
Do not commit the environment file, credentials, database, sessions, signatures,
or uploaded evidence.

## Stop local PostgreSQL

```sh
services/api/scripts/postgres-local.sh stop
```

Stopping PostgreSQL does not touch any Anvil process. A2 never starts, resets or
stops the default chain automatically.

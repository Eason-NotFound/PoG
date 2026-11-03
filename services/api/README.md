# PoG API: local-chain and full-demo adapters

This keeps the accepted A1 HTTP/database/file/session foundation and adds an
explicitly enabled, loopback-only A2 adapter for the accepted V2 contracts. A1
draft creation remains off-chain. A2 chain operations are separately queued and
processed by durable worker/indexer commands. Opt-in full-demo adapters now add
funded MockHKD donations, invoice-limited Foundation release, simulated HKD
redemption, supplier payment and separate settlement confirmation. No real money,
bank transfer or redeemable stablecoin is involved. Project closure and refunds
are not exposed by the full-demo API/UI.

The optional Qwen diagnostic generates an unsigned PRE report; it cannot approve
funds. The optional offline mode displays imported historical sample reports and
submits clearly labelled synthetic PRE/FINAL technical fixtures through the
independent test AI identity. Neither mode signs human approval or Recipient
receipt, nor automatically executes Reserve, Release or supplier payment.

The frozen A2 scope is in `docs/api/A2_SPEC.md`; account names are fixed by
`docs/api/ACCOUNT_PREFERENCES.md`. See `docs/api/A2_RUNBOOK.md` and
`docs/api/A2_INTERFACE.md` for the operator and HTTP handoff.
Those describe the historical A2 checkpoint; the additional diagnostic interface
is in `docs/api/AI_DIAGNOSTIC_INTERFACE.md`. All new switches default to disabled
in `services/api/.env.example`.

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

Current migrations end at `c31004a30006`; the full-demo tables are introduced by
`c31003a30005`. Upgrade a new or backed-up owned database with `alembic upgrade
head`; do not copy another machine's PostgreSQL data or deployment namespace.
These migrations are forward-only. Never reset an existing demonstration or
evidence database to prepare a new clone.

## Explicit local full-demo startup

Run from the repository root. Use the existing local-chain runbook to create and
verify an owned Anvil instance on chain 31337. Point `POG_A2_CHAIN_MANIFEST` at
that instance's absolute manifest path, not at another operator's file. In a
private, ignored environment file, explicitly set:

```dotenv
POG_A2_CHAIN_ENABLED=true
POG_A2_DEMO_SIGNING_ENABLED=true
POG_FULL_DEMO_ENABLED=true
POG_OFFLINE_DEMO_ENABLED=false
POG_AI_DIAGNOSTIC_ENABLED=false
```

Use `seed-chain-demo --confirm-chain-demo-fixtures` from
`docs/api/A2_RUNBOOK.md` for a fresh instance with separately configured role
wallets and private passwords. It is distinct from the off-chain A1 `seed-demo`
command below. Do not rotate or overwrite an existing role-wallet mapping.
Provision simulated opening HKD only with the explicit existing command:

```sh
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api.cli \
  provision-full-simulation --confirm-simulation-fixtures \
  --donor-opening-hkd-cents 100000
```

This is labelled fixture money, not a real deposit. Donors still have to perform
the conversion and project donation actions. Start the API and the following
three processes in separate terminals using the same exported private environment:

```sh
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api.cli chain-worker
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api.cli chain-indexer
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api.cli payment-worker
```

The original UI must separately enable its full-demo adapter and target this API
via its server-side configuration; launching Next.js alone does not connect a
database or chain. The standard application usernames are `foundation`,
`recipient`, `donor`, and `admin`; `admin` remains the independent human approver.

## Import existing offline sample reports privately

Report bodies, evidence and dataset archives are deliberately not committed.
Obtain the dataset ZIP separately and supply its local path yourself. Import only
the PRE and FINAL stage Markdown, not SQL, completed balances, approvals or other
package scripts:

```sh
.local/runtime/bin/python services/api/scripts/import_offline_reports.py \
  --dataset-zip /absolute/path/to/dataset.zip \
  --output "$PWD/.local/offline-reports.private.json"
```

The importer rejects unsafe archive paths, duplicate or ambiguous stage files,
incompatible report metadata and an existing output. It reads no credentials,
executes no package code, connects to no service, and creates a private `0600`
JSON bundle. It does not modify the database, chain or original ZIP. Keep its
output and the original archive outside Git; do not force-add them.

Then export the absolute output path as `POG_OFFLINE_DEMO_REPORTS_FILE` and set
`POG_OFFLINE_DEMO_ENABLED=true` for both the API and the UI server. Restart only
the processes whose configuration changed; keep the owned chain, workers and
database intact. Full-demo and demo-signing must also be explicitly enabled.

Foundation selects a procurement in the existing UI and explicitly requests the
offline PRE or FINAL action. No UUID needs to be sent to an operator. The request
binds the current namespace, run, instance, selected original evidence and signer
nonce; the internal independent `service_ai` role prepares, signs and submits the
fixture. Read async state through the private `offline-demo-ai` resource, not an
operation belonging to the internal AI identity. Human approval, receipt and
financial actions are still performed independently by their original roles.

Imported 16/10-point reports are historical mock references, not an evaluation of
the currently selected files. They are displayed separately from the current
synthetic technical fixture's 100bps risk field and deterministic marker hash.
The historical Markdown hash is not that fixture's signed `reportHash`. Display
`OFFLINE DEMO — Simulated AI assessment; no model inference` prominently.

Without a private imported bundle, offline availability is disabled. Cloning the
repository alone intentionally does not reproduce another operator's reports,
credentials, funding history or runtime instance.

## Optional real Qwen PRE diagnostic

Enable `POG_AI_DIAGNOSTIC_ENABLED`, set `POG_AI_DIAGNOSTIC_URL` to the currently
approved LAN destination in the template, and supply an absolute private
`POG_AI_DIAGNOSTIC_TOKEN_FILE`. Changing the URL does not change the adapter's
approved-destination restriction. The current service accepts image evidence and
performs a single-PO visible-field diagnostic; FINAL detection and model-backed
EIP-712 signing are not implemented. A health response alone is not proof that a
report was generated. Diagnostic errors remain visible and do not approve funds.

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

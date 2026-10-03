# PoG API / Database A1 Runbook and Handoff

Version A1.1, 2026-10-03 Asia/Hong_Kong. This document describes the A1
candidate only. It does not authorize merge, tagging, A2, M3.2, public/LAN
exposure, chain transactions, AI execution, or payment execution.

## Runtime and dependency decision

- Python 3.13.15, PostgreSQL 17.11, and pip 26.2.1 are isolated under
  `.local/runtime`; local database state, credentials, and evidence remain under
  ignored `.local/` paths.
- `services/api/conda-osx-arm64.lock` is an explicit SHA-256 Conda lock for the
  tested macOS arm64 runtime. `services/api/requirements.lock` is the exact Python
  dependency closure. SQLAlchemy's platform-conditional Linux/Windows `greenlet`
  dependency is explicitly pinned to 3.5.6, while it remains correctly absent on
  the tested macOS `arm64` platform. `requirements.in` and `pyproject.toml` record
  the direct dependencies.
- Local API and PostgreSQL bind to `127.0.0.1` only. Host validation refuses a
  non-loopback API bind. The helper is not a system service and does not manage
  Anvil.
- CI uses Python 3.13.15, the exact pip lock, PostgreSQL
  `17.11-alpine3.24`, and immutable commit pins for GitHub Actions.

Official references used for the version and implementation choices:

- [FastAPI security](https://fastapi.tiangolo.com/tutorial/security/)
- [FastAPI on PyPI](https://pypi.org/project/fastapi/)
- [SQLAlchemy on PyPI](https://pypi.org/project/SQLAlchemy/)
- [Alembic on PyPI](https://pypi.org/project/alembic/)
- [psycopg on PyPI](https://pypi.org/project/psycopg/)
- [greenlet on PyPI](https://pypi.org/project/greenlet/)
- [PostgreSQL numeric types](https://www.postgresql.org/docs/17/datatype-numeric.html)
- [PostgreSQL 17 server administration](https://www.postgresql.org/docs/17/admin.html)
- [Official PostgreSQL container metadata](https://github.com/docker-library/official-images/blob/master/library/postgres)

## Start, migrate, verify, and stop

From the repository root on macOS arm64:

```sh
conda create --prefix .local/runtime --file services/api/conda-osx-arm64.lock
.local/runtime/bin/python -m pip install --requirement services/api/requirements.lock
services/api/scripts/postgres-local.sh up
. .local/postgres/connection.env
export POG_STORAGE_ROOT="$PWD/.local/api-storage"
export POG_A1_RUN_ID=a1-local-run
export POG_A1_INSTANCE_ID=a1-local-instance
export POG_BIND_HOST=127.0.0.1
export POG_BIND_PORT=8080
(cd services/api && ../../.local/runtime/bin/alembic upgrade head)
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api
```

In another shell, expected checks are:

```sh
curl --fail http://127.0.0.1:8080/health
curl --fail http://127.0.0.1:8080/ready
curl --fail http://127.0.0.1:8080/openapi.json
```

`/ready` may report `ready: true` when DB, migration, and private storage are
ready, while deployment remains `mode: a1_mock_unverified`, `verified: false`,
and `chainVerified: false`. Chain, AI, and payment adapters remain unavailable;
the wallet adapter is `mock_limited` and cannot sign or submit.

Stop only this service with Ctrl-C, then optionally stop its private PostgreSQL:

```sh
services/api/scripts/postgres-local.sh stop
```

## Supported A1 HTTP surface

All authenticated routes use a bearer session. Client JSON models reject extra
fields, so callers cannot supply `role`, `wallet`, `from`, `caller`, `status`, or
`txHash`. Mutation idempotency keys are printable ASCII, 1–128 characters.

| Method and path | A1 behavior |
| --- | --- |
| `GET /health` | Process liveness only. |
| `GET /ready` | DB/migration/storage readiness plus explicit unverified adapter state. |
| `GET /v2/deployment-config` | Mock namespace; no RPC, contract, key, or verified-chain claim. |
| `POST /v2/sessions` | Creates a new expiring session after password and one-active-role-wallet validation. |
| `GET /v2/me` | Returns only the authenticated user's fixed role and wallet. |
| `DELETE /v2/sessions/current` | Revokes the token; repeated logout of that token is idempotent. |
| `GET/POST /v2/projects`, `GET /v2/projects/{id}` | Authorized read and Foundation-only off-chain draft creation. Donors see only the public/fairness/closing projection. |
| `GET/POST /v2/procurements`, `GET /v2/procurements/{id}` | Private authorized read and Foundation-only off-chain draft creation. |
| `POST /v2/documents` | Private PDF/JPEG/PNG upload, maximum 10 MiB, with original-byte SHA-256 and Keccak-256 stored separately. |
| `GET /v2/documents/{id}`, `GET /v2/documents/{id}/content` | Metadata/content only for the assigned Foundation, Recipient, or human approver. |
| `GET /v2/operations/{id}` | Owning principal's persistent result or original safe failure. |

Project, procurement, and document creates return HTTP 202 with a typed
`operation` envelope. The status is `awaiting_authorization`; the resource
`chainState` is `off_chain_draft` and unverified. A repeated key with the same
validated payload returns the original result; a different payload returns 409.

Errors use a stable envelope:

```json
{
  "error": {
    "code": "stable_machine_code",
    "message": "safe explanation",
    "operationId": null,
    "details": null
  }
}
```

When an operation has already been created, safe business failures persist with
their original HTTP status/code and are replayed consistently. Passwords,
session tokens, raw login names on failed login, file paths, and private file
contents are not written to audit metadata.

## Data and file guarantees

- Alembic revision `84fcc48891be` creates identities, sessions, roles and wallet
  authorization; deployment namespaces; projects and procurements; private
  document/version records; operations/steps/audit; and reserved structures for
  risk, receipt, approval, chain transaction/event/cursor, and payment links.
- Amount columns use `NUMERIC(78,0)` with range constraints. API inputs first
  require canonical decimal strings in `0..2^256-1`; JSON numbers, booleans,
  signs, decimals, exponent notation, NaN, and Infinity are rejected. The
  6-decimal converter performs string/Decimal arithmetic without float roundtrip.
- Namespace-plus-parent composite foreign keys prevent cross-instance
  procurement and document attachment. Reads also filter by active namespace and
  authenticated principal.
- Evidence is structurally parsed, written under a random private key, mode 0600
  inside mode-0700 directories, and served only after authorization. Atomic
  exclusive linking prevents overwrite on a key collision. Failed validation,
  authorization, storage commit, DB transaction, replay, and concurrent duplicate
  paths remove temporary/orphan files.
- Test database reset is restricted to the exact `pog_api` role,
  `pog_api_test` database, loopback host, query-free/fragment-free URL, matching
  port, explicit opt-in, and a valid managed marker (or the separately explicit
  GitHub Actions target for non-reset test use).

## Verification

Local full verification:

```sh
services/api/scripts/test.sh
```

This rebuilds only the guarded test database, migrates to head, and runs HTTP,
PostgreSQL, migration, authorization, precision, idempotency/concurrency/restart,
file, OpenAPI, adapter, and destructive-target safety tests. No SQLite or
external AI/payment/RPC service is used. The isolated migration test performs
upgrade, downgrade, and re-upgrade against a random `pog_api_migration_*`
database in the same dedicated loopback cluster.

## Explicitly unsupported in A1

- Any blockchain read/write integration, RPC verification, EIP-712 payload or
  signature, relayer, worker, indexer, canonical receipt/event confirmation, or
  chain reset/deploy.
- Donation, reserve, release, close, refund, settlement, supplier payment, or
  any state claiming on-chain or fiat completion.
- Live AI assessment/model calls, live payment service, real HKD/stablecoins,
  public network, LAN exposure, frontend, or production account management.
- Public owner/admin mutation endpoints, arbitrary server file paths, client
  caller/from/role/wallet overrides, or general calldata submission.

The rollback reference is the accepted `origin/main` baseline
`ae7bb08292bb4a57f9d416f7725ef332be473306`. Reverting this A1 candidate must not
move or rewrite any accepted blockchain tag or artifact.

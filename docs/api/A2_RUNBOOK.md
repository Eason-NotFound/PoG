# PoG API A2 local runbook

Published signing-expiry migration 003 is preserved unchanged; additional guards
use successor 004. Migration 004 is forward-only. See
[A2_REVIEW_FIXES.md](A2_REVIEW_FIXES.md) for compatibility, reproducible checks
and verification limits. This runbook covers the local synthetic A2 scope only.

Start an owned chain on a non-default loopback port and verify it once. The API
does not create, reset or stop Anvil.
The gateway also needs the generated accepted artifacts in `contracts/out`;
build with pinned Foundry before starting the API. Missing artifacts or a changed
manifest fail closed. The gateway checks canonical deployment receipts and
reconstructs full runtime at the deployment block, including immutable fields.

```sh
python3 scripts/local-chain.py up --port 18545 --state-dir /tmp/pog-local-a2-api
python3 scripts/local-chain.py verify --port 18545 --state-dir /tmp/pog-local-a2-api
```

Migrate PostgreSQL, opt in to A2 and start API/worker/indexer as separate
processes. Replace the database URL and manifest path with the operator's owned
values.

```sh
export POG_DATABASE_URL='postgresql+psycopg://pog_api:...@127.0.0.1:55433/pog_api_local'
export POG_STORAGE_ROOT="$PWD/.local/api-storage"
export POG_A2_CHAIN_ENABLED=true
export POG_A2_CHAIN_MANIFEST=/tmp/pog-local-a2-api/manifest.json
export POG_A2_DEMO_SIGNING_ENABLED=true
(cd services/api && ../../.local/runtime/bin/alembic upgrade head)
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api.cli chain-worker
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api.cli chain-indexer
```

Use `--once` for one worker/indexer cycle. Regular indexing now automatically
checks quiet/shorter/same-height canonical drift and rebuilds projections.
Use `chain-indexer --rebuild` after an
operator-confirmed reorg investigation; it only rereads the owned chain and
rebuilds this service's canonical projections. It never sends a transaction or
resets Anvil.

Workers atomically claim a persisted envelope with a `sending` lease before RPC.
Another worker skips that step. A worker that dies during the bounded RPC call can
be recovered after the 15-second lease: the next worker reconciles the exact
caller/nonce/to/data/value envelope before it sends or binds anything. An
unprovable outcome becomes `requires_attention`; it is never retried with a new
nonce.

Definite preflight rejection is retained as `not_broadcast`, releasing only the
unconsumed EVM nonce reservation. Unknown or pending sends continue protecting
their caller lane. Historical receipt/getter RPC failures remain submitted and
retryable; a timeout is never evidence of a reorg. Continuous services retry
dependency outages at the configured interval; `--once` reports the error.

Signing renewal uses a new explicit key and fresh deadline/bundle. Retire only
unsubmitted expired/stale authorization, or failed authorization with persisted
positive no-broadcast proof. Preserve consumed/unknown history. PRE renewal is
permitted in accepted states 1/2/3; human Reserve renewal in 2/3. AI Review/Reject
is risk evidence, not an automatic veto; independent human votes remain required.
Receipt evidence must be uploaded by the original requesting Recipient.

Receipt requests bind their original immutable evidence version in saved
context. Legacy requests without that binding fail closed for new signing or
submission; a later same-hash Recipient upload is not their original source.
Never-submitted invalid history may be retired with an audit and new key.
Queued/prepared-envelope source failures are held as `requires_attention` with
`chain.receipt_source_blocked`, preserving nonce, submitted pointer and history.
Do not delete or silently release them. An exact previously broadcast envelope
may still be reconciled without a new send; its chain facts are not rewritten.

After a reorg, a caller nonce already present in durable transaction history is
held for investigation. A newly queued operation that encounters it becomes
`requires_attention` with `chain_nonce_history_conflict` (409); it sends nothing
and keeps the original transaction and audit records. A2 never releases an
unproven or possibly broadcast reservation automatically and has no
manual-resolution HTTP endpoint. Inspect canonical and pending facts
before coordinating recovery; do not delete records or start a fresh namespace
on the same running chain to bypass the hold.

Standard application usernames are `foundation`, `recipient`, `donor`, and
`admin`. `admin` maps only to `human_approver` and the manifest's independent
`humanApprover` wallet. The optional `service-ai-fixture` and second Donor are
technical fixtures, not public standard accounts. See `ACCOUNT_PREFERENCES.md`
before provisioning or renaming identities.

After exporting six non-placeholder fixture passwords, provision exact manifest
wallets explicitly (no private key is read or stored):

```sh
PYTHONPATH=services/api/src .local/runtime/bin/python -m pog_api.cli \
  seed-chain-demo --confirm-chain-demo-fixtures
```

Default A1 mode stays chain-disabled. `/ready` reports a failed A2 chain gate as
unready while A1 draft/file endpoints remain available. A2 requires loopback
HTTP, chain 31337, the manifest genesis and Anvil instance ID, accepted ABI
digests, deployed runtime hashes, mutual binding, ten distinct roles, 6-decimal
MockHKD and the allowlisted AI signer.

# PoG API A2 local runbook

Start an owned chain on a non-default loopback port and verify it once. The API
does not create, reset or stop Anvil.

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

Use `--once` for one worker/indexer cycle. Use `chain-indexer --rebuild` after an
operator-confirmed reorg investigation; it only rereads the owned chain and
rebuilds this service's canonical projections. It never sends a transaction or
resets Anvil.

Workers atomically claim a persisted envelope with a `sending` lease before RPC.
Another worker skips that step. A worker that dies during the bounded RPC call can
be recovered after the 15-second lease: the next worker reconciles the exact
caller/nonce/to/data/value envelope before it sends or binds anything. An
unprovable outcome becomes `requires_attention`; it is never retried with a new
nonce.

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

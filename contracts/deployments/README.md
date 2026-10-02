# Deployment manifests

Future network-specific JSON manifests belong here. No address is valid until a
deployment transaction is confirmed and the manifest records its chain ID and
transaction hash.

M3.1 local deployment is managed by `python3 scripts/local-chain.py up`.
Its actual manifest/state/logs are generated under ignored `local/`, with an
Anvil instance identity as well as contract/transaction/ABI/bytecode fingerprints.
`local.example.json` is only a schema example; it is not a live deployment or
valid address configuration. See `docs/M3_1_RUNBOOK.md` before any explicit reset.

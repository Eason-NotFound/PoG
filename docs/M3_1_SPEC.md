# M3.1 — reproducible local chain deployment

Authorized: 2026-10-03, Asia/Hong_Kong. The user requested completion of M3.1
provided there is no blocking product decision. There is none for the defaults
below. This does not authorize M3.2, accepting/merging M3.1, public deployment,
real-value transfers or changes to any accepted M1/M2 technical artifact.

## Scope and defaults

- Use the existing pinned Foundry 1.8.4, Solidity 0.8.24, OpenZeppelin and
  forge-std. Do not change the compiler, optimizer or EVM size limit.
- Anvil is a local development EVM, not a newly invented blockchain or a
  public network. Chain ID is 31337, RPC listens on 127.0.0.1 only, default
  port 8545, Cancun hardfork, automatic mining and one receipt confirmation.
  Cross-origin RPC access is disabled with `--no-cors`; M3.1 exposes no browser API.
- Deploy unchanged MockHKD and the accepted independent PoGRegistryV2 and
  ProcurementEscrowV2. Registry must exist before Escrow construction; verify
  both directions of their one-time binding before publishing the manifest.
- The Registry owner and authorized AI signer are distinct test accounts.
  The default human policy for later project creation is 1-of-1; this phase
  creates no project or human vote and does not give the Foundation that role.
- Ten independent default Anvil addresses represent deployer/owner, Foundation,
  Recipient, AI signer, human approver, Donor A, Donor B, supplier, relayer and
  mock redemption. They are not production identities or securely isolated
  credentials: the loopback development node has unlocked test accounts.
- Donor A and Donor B each receive 1,000 valueless mHKD (1,000,000,000 atomic
  units, six decimals). This is a test faucet/bootstrap, not HKD collection,
  an exchange operation, a donation or proof of stablecoin backing. No project
  deposits or allowances are created by deployment.

## Deliverables

1. A Python-standard-library command for startup/deployment, read-only status
   and verification, safe stop, and explicitly confirmed reset/redeployment.
   Foundry performs contract deployment using local unlocked RPC accounts;
   no private key, mnemonic, provider token or private evidence is exported.
2. A generated local manifest with RPC/chain ID, genesis and Anvil instance
   identity, role addresses, three contract addresses, transaction receipts
   and canonical block identities, accepted M2 version/source references,
   ABI paths/version/SHA-256 and actual runtime bytecode hashes/sizes.
3. Verification of pinned tooling, accounts, chain instance, creation receipts,
   code, owner, mutual binding, AI signer, signing-domain network/contract and
   six-decimal token metadata. Solidity immutable-reference offsets must be
   handled when comparing deployed code to the compiled artifact template.
4. Dependency-free tests for happy-path deployment and lifecycle, repeat-up
   idempotency, read-only operations, explicit reset, port collision, stale or
   tampered manifest and safe management of only this tool's own node.
5. A local runbook and an independent CI job. Existing M1/M2 check scripts,
   baseline manifests and ABI artifacts remain byte-identical.

## Lifecycle and safety

- Never bind to a LAN/public interface, fork a remote network, impersonate
  production accounts, relax contract-size limits or overwrite someone else's
  process. An occupied port that is not the recorded owned instance is an error.
- Concurrent commands use a per-state-directory lock. Process ownership needs
  more than a bare PID: check its recorded command/start identity and node
  instance identity before terminating or reusing it.
- Repeated startup reuses and verifies an existing healthy owned deployment;
  it does not mint/deploy again. Status and verify perform no transactions.
- Stop preserves local records. No business-chain persistence is promised in
  this MVP: after stop, startup must require explicit reset rather than silently
  generate a fresh chain. Reset archives prior records before replacement.
- Reset is destructive to the managed demo's on-chain projects, donations and
  signatures. Require `--confirm-reset`; do not remove a workspace, home,
  another state directory or other people's node. Old records remain recoverable,
  but they are audit records, not a restorable chain snapshot.
- Addresses may repeat after redeployment. Instance/run identity and canonical
  receipt block hashes, not chain ID/address alone, identify a valid deployment.
  Future API caches, pending signatures and mock ledgers must be invalidated
  after an explicit reset; doing that integration belongs to M3.2 or later.
- Anvil's instance ID is an operational identity, not part of the accepted
  EIP-712 domain. Recreating the same chain ID, addresses, business IDs and
  nonces can make an old signature valid again. Future integration must not
  replay old signatures or reuse old business records across resets; a manifest
  check alone does not provide contract-level cross-reset replay protection.
- Runtime state, manifests and logs live in ignored
  `contracts/deployments/local/`; a checked-in example is explicitly non-live.

## Verification and exit

Run the unchanged `bash scripts/check-blockchain.sh`, accepted M1 baseline
15/15 and M2 RC technical baseline 18/18, new Python unit tests and live isolated
Anvil deployment/lifecycle tests. Independently run and inspect the actual
127.0.0.1:8545 deployment and its manifest. Publish only verified new scripts,
tests, public examples and documentation through a feature branch / PR / CI.

Report actual results and stop for the user's M3.1 acceptance. A running local
chain is not an implemented relayer/API/database/model, simulated HKD ledger,
supplier payment service, three-computer booth or public-network deployment.

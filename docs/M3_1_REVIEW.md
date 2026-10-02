# M3.1 candidate — local Anvil deployment

Date: 2026-10-03 (Asia/Hong_Kong). The user authorized completing M3.1 if no
blocking product decision was needed; local defaults need no such decision.
This is technical completion, not user acceptance or authorization to merge
M3.1, publish an accepted version or start M3.2.

Base: accepted M2 `blockchain-v0.2.0-m2` at `61aa673`, plus its archival-status
main checkpoint `d6c2863`. Preserve all M1/M2 annotated tags and the 15/18-file
technical baselines. No public deployment or real-value assets are involved.

## Deliverables

- Python-standard-library local command: up/status/verify/stop/reset, using
  pinned Foundry 1.8.4 `forge create`/`cast` and unlocked local Anvil accounts.
- 127.0.0.1:8545 / chain 31337 / Cancun / no CORS; ten distinct demo role
  addresses, three original accepted contracts, verified mutual binding,
  test AI signer and two Donors each with 1,000 valueless mHKD.
- Instance/genesis/run-bound local manifest with canonical deployment/bootstrap
  receipts, ABI fingerprint/artifact identity, actual runtime hashes and sizes.
  Raw immutable artifact bytes are not falsely equated with deployed code:
  both masked template and historical CREATE full-runtime reconstruction are checked.
- Fixed executable/argv/start-time PID ownership, shared port and state-directory
  locks, port-collision refusal, no HTTP redirect/external RPC fallback, explicit
  reset archival and no private-key/mnemonic output.
- Independent local/live tests, dedicated pinned CI job, example and runbook.

No accepted Solidity, Solidity tests, ABI, dependencies, Foundry config,
frozen technical hand-off or verification scripts were modified. New scripts
do not pre-create a project, allowance, donation, AI assessment or human approval.

## Verification

CEO ran the unchanged blockchain check successfully: 81/81, five ABI checks,
format/build/lint/size, M1 baseline 15/15, push guard 12/12 and diff checks.
The M2 RC technical baseline also passed 18/18. Runtime sizes remain RegistryV2
21,328 B and EscrowV2 24,366 B (210 B EIP170 margin); no limit was relaxed.

Independent tester ran `python3 scripts/test-local-chain.py --live`: 32/32,
13 unit and 19 actual isolated-node cases. CEO separately reran it with the
default booth node already running: 32/32, 40.443 seconds, no skips. Test-created
nodes were stopped and ports released; the default chain was not reset.

Cases cover receipts/ABIs/full immutable runtime, no duplicate deployment/mint,
read-only status/verify, missing reset consent, stale/tampered manifest, actual
runtime and immutable tampering even with rewritten hashes, other HTTP/Anvil
listeners, reset archival/new instance/same addresses, stop/up refusal, fake
non-Anvil PID and concurrent different directories competing for one port.

CEO separately confirmed real default deployment and `verify`, loopback listener
and failed cross-origin preflight without an allow-origin header. The three live
addresses are read from ignored local manifest, not the checked-in example.

Implementation findings were corrected before final verification: missing
immutableReferences for MockHKD, unstable compilation AST IDs, PID ownership,
shared-port startup race, monetary JSON precision, manifest declaration checks
and possible HTTP redirection. Independent safety review has no remaining
blocking P1/P2 finding; this is not a third-party security audit.

## Limits and next gate

- Anvil accounts are unlocked and publicly known; any local RPC caller can use
  them. Distinct demo roles are not production key isolation.
- Bootstrap mint is not HKD collection/conversion. No actual model, API,
  database, payment ledger, website, wallet integration or three-PC booth runs.
- Stop retains records, not business-chain state. Reset requires confirmation
  and discards the managed demo's projects/transactions/signatures; archived
  records are not a restorable state snapshot.
- An instance ID is not in the accepted EIP-712 domain. Reusing chain ID,
  addresses, business IDs and nonces can leave an old signature valid on-chain.
  Later integration must discard old signatures and isolate records across resets.

Publish the technically verified feature candidate through draft PR/CI; immutable
RC tags may identify technical snapshots but never replace user acceptance.
Report and STOP. Do not merge/issue a formal accepted M3.1 tag or start M3.2.

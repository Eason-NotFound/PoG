# Blockchain Status

Last updated: 2026-10-02 (Asia/Hong_Kong)

## Current milestone

Latest user change: the original M2 direct-vendor implementation is superseded.
The coder has stopped, leaving one untracked, uncompiled and untested
`contracts/src/ProcurementEscrow.sol` draft. No M2 tests, ABI, commits or deployment
were produced. Preserve the draft, but do not treat it as a current candidate.
The revised flow is in `docs/FOUNDATION_SETTLEMENT_FLOW.md`; Donors individually
convert simulated HKD and donate stablecoins directly to a project's locked
Escrow. Both conversions are simulated; Recipient confirms receipt, Foundation
pays HKD to the vendor. Clarify invoice-limited versus full-project release amount
before revised dispatch.
M1 formal tag `blockchain-v0.1.0-m1` points to merged commit `45e6bab`; main/tag CI
passed. Historical M1 is unchanged. M3 remains unauthorized.

M1 — MockHKD and PoGRegistry passed technical review, were accepted by the user on
2026-10-02 with Hank's offline approval, and were merged and formally archived.
The original M2 began afterward but was stopped by the user's architecture
change. Revised M2 implementation has not been dispatched. M3 is unauthorized.

## Completed

- Inspected repository: it was an empty Git repository on `main`, with no commits,
  no `AGENTS.md`, and no existing user files to overwrite.
- Added a dependency-free Foundry layout and `Smoke.t.sol`.
- Frozen product decisions, roles, canonical structures, full procurement state
  machine, funds invariants, 6-decimal amount policy, EIP-712 domains/types and
  terms hashes, core functions/events, integration responsibilities, confirmation
  rules, error mapping, and idempotency.
- Created placeholders for source, scripts, deployment manifests, and generated
  ABI hand-off without implementing business contracts.
- Repaired deployment topology: Registry-first deployment, immutable Registry in
  Escrow, and one-time mutually verified Escrow binding in Registry.
- Split `RegistryProject`, `EscrowProjectAccount`, and API-composed
  `ProjectView`; removed refund/closure accounting from MVP.
- Bound both AI stages to exact on-chain-recomputed pre/final evidence hashes.
- Froze public entrypoints, counterpart-only hooks/callbacks, call order, atomic
  rollback, reentrancy assumptions, and Foundation-only policy changes.
- Implemented `MockHKD` with 6 decimals and unrestricted, zero-safe demo minting.
- Implemented `PoGRegistry` project/procurement lifecycle, one-time Escrow
  binding, exact evidence hashes, AI signer allowlist, EIP-712 assessments,
  nonce/expiry enforcement, policy orchestration, cancellation, Allocation Roots,
  pause controls, counterpart-only callbacks, and API/Escrow read helpers.
- Pinned OpenZeppelin Contracts `v5.7.0` and forge-std `v1.16.2` to their
  recorded tag commits.
- Added Registry test doubles and unit/fuzz coverage for valid and adversarial
  binding, hooks, lifecycle, evidence, signatures, nonces, expiry, policy,
  cancellation, and Allocation Roots.
- Generated versioned `MockHKD` and `PoGRegistry` ABI JSON from Forge output.

## Verification status

M1 is verified locally on 2026-10-02 with:

```text
Forge 1.8.4
commit 50af4efe189dc64bad2b75ed6990b835de66c4ae
Solc 0.8.24
```

The execution shell does not include `~/.foundry/bin` in `PATH`; the shared
verification script resolves the local installation. The hand-off command is:

```text
bash scripts/check-blockchain.sh
```

Formatting, compilation, and diff checks pass with no residual compiler/lint
warnings. The full suite passes 44/44 tests: 36 Registry, 7 MockHKD, and 1 smoke
test, including three 256-run fuzz tests. Strict source lint, ABI consistency,
12 push-guard regression tests, and working/staged diff checks also pass.

## Repository publication

- Author: `Eason-NotFound <23260068@life.hkbu.edu.hk>`.
- `main` was originally initialized with empty root `03368f7`; it now contains
  accepted M1 at merge commit `45e6bab`.
- Historical M1 candidate branch: `codex/blockchain-m1-verified`.
- Merged review: <https://github.com/Eason-NotFound/PoG/pull/1>.
- Published immutable technical snapshot: `blockchain-v0.1.0-m1-rc.1`.
- Published immutable accepted version: `blockchain-v0.1.0-m1`.
- Current design branch: `codex/blockchain-m2-escrow`; old Escrow draft is not
  included in the new architecture documentation publication.
- The user authorized removal of mandatory GitHub human reviews on 2026-10-02;
  main still requires PR/CI and forbids deletion/force push. Version tags remain
  immutable. The existing ruleset was updated in place and read back before merge.
- M1 merge/version archival completed with passing CI. The earlier direct-vendor
  M2 authorization was superseded; revised coding awaits the remaining release
  policy and updated interfaces. No online Hank review is claimed.

## Scope guard

- No technically verified M2 custody, approval, release or payment implementation.
  The untracked obsolete Escrow draft is not a completed or tested deliverable.
- No deployment, proxy, NFT, DAO, real stablecoin, cross-chain, Chainlink, or zkML
  work. M1 archival is complete; revised M2 needs its new flow/interfaces frozen.
- M2 completes at a technically verified candidate and user report. Do not
  auto-accept/merge M2 or start M3.

## Next gate

The Foundation settlement architecture now takes precedence over the original
M2 scope below. Update versioned contract/signing/API interfaces only after the
release-amount question is resolved. Do not resume the direct-vendor implementation.

M1 technical review has passed, including an independent full-suite rerun,
strict source lint, ABI comparison and 2,000 runs of each of three fuzz tests.
M1 acceptance/archival is complete. The old direct-vendor M2 approval-input plan
is historical, not the current release-to-Foundation interface. Re-freeze the
current flow's signatures, deadlines, receipt proofs and settlement evidence
after resolving the release-amount question. See `docs/VERSION_CONTROL.md` for
version gates.

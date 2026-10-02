# Blockchain Status

Last updated: 2026-10-02 (Asia/Hong_Kong)

## Current milestone

M1 — MockHKD and PoGRegistry implementation has passed technical review and was
accepted by the user on 2026-10-02, with Hank's offline approval confirmed by the
user. Archive M1 by merging and creating its formal immutable version before
starting the authorized M2 Escrow implementation. M3 remains unauthorized.

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
- `main` was initialized with empty root commit `03368f7`; it contains no
  application code.
- M1 candidate branch: `codex/blockchain-m1-verified`.
- Draft review: <https://github.com/Eason-NotFound/PoG/pull/1>.
- Published immutable technical snapshot: `blockchain-v0.1.0-m1-rc.1`.
- The user authorized removal of mandatory GitHub human reviews on 2026-10-02;
  main still requires PR/CI and forbids deletion/force push. Version tags remain
  immutable. The existing ruleset was updated in place and read back before merge.
- M1 merge/version archival and subsequent M2 are explicitly authorized. GitHub
  CI results are tracked on the PR. No online Hank review is claimed.

## Scope guard

- No `ProcurementEscrow.sol` custody, approval, reservation, or payment implementation.
- No deployment, proxy, NFT, DAO, real stablecoin, cross-chain, Chainlink, or zkML
  work. M1 merge/version archival is authorized; M2 may begin only afterward.
- M2 completes at a technically verified candidate and user report. Do not
  auto-accept/merge M2 or start M3.

## Next gate

M1 technical review has passed, including an independent full-suite rerun,
strict source lint, ABI comparison and 2,000 runs of each of three fuzz tests.
The user has accepted M1 and authorized its merge/version archival followed by
M2. M2 includes the explicit reserveAmount approval input and execution-time
human-expiry/renewal semantics. AI expiry/correction still uses cancellation and
new procurement IDs. See `docs/VERSION_CONTROL.md` for version gates.

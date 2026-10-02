# Blockchain Status

Last updated: 2026-10-03 (Asia/Hong_Kong)

## Current milestone

The user approved revised M2 on 2026-10-03: invoice-limited stablecoin release to
Foundation, active-project locking, and proportional refunds after closure and
human reconciliation. `docs/M2_V2_SPEC.md` freezes the scope. The coder completed
independent RegistryV2/EscrowV2, V2 tests, exact interface documentation and ABIs.
CEO source review and independent full verification passed. The user explicitly
accepted M2 V2 on 2026-10-03: AI assessments are risk evidence, human approvals
are the final fund decisions, and MockHKD, simulated supplier payment and no live
AI/API/database deployments are accepted M2 limits. The user authorized merge
and formal archival, followed by a report and STOP; M3 is not authorized.
PR #2 is merged as `61aa673653dd31188d2627d76cbba3f97fed6137`. The new annotated
accepted tag `blockchain-v0.2.0-m2` points to that exact merge; main and tag CI
passed. Source commit `810a54e` remains the immutable technical snapshot
`blockchain-v0.2.0-m2-rc.1`; no old tag or verified source was changed.

The obsolete direct-vendor draft was moved without changing bytes into ignored
`.task-archives/m2-direct-vendor/ProcurementEscrow.direct-vendor.unverified.sol.txt`
(SHA-256 `ea37aa6c24edbb90eed69e81f67d535eb5b030107c4f4ac53776a78c01042777`).
It is not compiled, uploaded or used as the current candidate. Donors convert
simulated HKD individually and deposit stablecoins to a specific project. Both
conversions are simulated; Recipient signs receipt, Foundation receives exactly
approved Invoice stablecoins then pays the fixed vendor simulated HKD.
M1 formal tag `blockchain-v0.1.0-m1` points to merged commit `45e6bab`; main/tag CI
passed. Historical M1 is unchanged. M3 remains unauthorized.

M1 — MockHKD and PoGRegistry passed technical review, were accepted by the user on
2026-10-02 with Hank's offline approval, and were merged and formally archived.
The original M2 was stopped by the user's architecture change. Revised M2 has
completed technical review, user acceptance, merge and formal archival. The
remaining action is the archival report and stop. M3 is unauthorized.

## M2 V2 completed

- Donor-only credited deposits into a specific project; surplus excluded.
- PO-first pre-AI/human reservation, Invoice/goods and independent Recipient
  receipt signature, final AI/human exact invoice release to fixed Foundation.
- Separate full mock vendor settlement evidence and current human approval.
- Current AI allowlist/expiry, exact-next signer nonces, EIP712 V2 domains,
  expired-human renewal, same-evidence AI renewal, policy epoch invalidation,
  stored ERC1271 votes, counterpart checks and pair-wide token callback defense.
- Active remainder locking, Closing restrictions with existing obligations
  allowed to finish, actual Closing-only stablecoin returns not erasing debt.
- Human close snapshot only with no unresolved procurement/Q, up to 64 donors,
  deterministic cumulative-interval refunds with no dust/sweep and O(1) claims.
- Exact token deltas and aggregate real-balance solvency across projects;
  net-release math avoids intermediate uint256 overflow.
- Five generated ABI comparisons and precise team integration/call documentation.

## Historical M0/M1 completed

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

## Current M2 verification

CEO independently ran on 2026-10-03:

```text
bash scripts/check-blockchain.sh
/Users/quyichen/.foundry/bin/forge test --fuzz-runs 2000 -vv
```

Both pass: 81/81 tests (44 preserved M1, 14 coder V2, 23 independent CEO V2).
All five fuzz cases pass 2,000 runs each. Format/build/strict source lint,
5 ABI comparisons, M1 hashes 15/15, push-guard 12/12 and diff checks pass.
Runtime sizes: RegistryV2 21,328 B; EscrowV2 24,366 B (210 B below EIP170).
Compiler/profile/limit unchanged; future modifications must recheck sizes.
The sandbox emits an environment-only Foundry signature-cache write warning;
it does not affect build/lint/test/check exit results.

Before merging the exact accepted PR head `5ce1a70`, CEO reran the full check
script: 81/81 tests (default fuzz profile), lint/ABI/size, M1 baseline 15/15,
push-guard 12/12 and diff checks passed. The M2 RC technical baseline separately
passed 18/18. Merge `61aa673` has an identical tree to that PR head. Earlier
2,000-run fuzz results remain applicable to the unchanged source; they are not
misreported as a new 2,000-run test in this archival step.

Reviewed defects were fixed and retested: pre-receipt Invoice cancellation;
return after settlement evidence but before confirmation; Closing-only return;
global insolvency fail-closed; missing-vote sentinel at timestamp zero;
close/signing fail-fast checks; and max-uint net math. Final source review found
no remaining functional P1/P2; this is not an external security audit.

No live API/model/fiat ledger or three-computer booth has been deployed by M2.
See `M2_V2_RUNBOOK.md` and `M2_V2_INTEGRATION.md` for boundaries and hand-off.

## Historical M1 verification

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
- `main` was originally initialized with empty root `03368f7`; accepted M1
  remains at immutable `45e6bab`, and accepted M2 merged as `61aa673`.
- Historical M1 candidate branch: `codex/blockchain-m1-verified`.
- Merged review: <https://github.com/Eason-NotFound/PoG/pull/1>.
- Published immutable technical snapshot: `blockchain-v0.1.0-m1-rc.1`.
- Published immutable accepted version: `blockchain-v0.1.0-m1`.
- Preserved M2 implementation branch: `codex/blockchain-m2-escrow`.
- Merged M2 review: <https://github.com/Eason-NotFound/PoG/pull/2>.
- M2 V2 implementation commit: `810a54ea9d17c5ac80f1974035f690e49941e1e1`.
- New immutable candidate tag: `blockchain-v0.2.0-m2-rc.1`, dereferenced locally
  and remotely to the exact implementation commit. It is not an accepted version.
- Passing implementation CI: push
  <https://github.com/Eason-NotFound/PoG/actions/runs/37039034702> and PR
  <https://github.com/Eason-NotFound/PoG/actions/runs/37039039417>.
- Source/tests/ABIs are frozen; later publication-status documentation does not
  move that tag or alter the 18-file candidate technical snapshot.
- Explicit user acceptance: 2026-10-03 in the CEO chat; it authorizes M2
  merge/archive only, not M3. Acceptance was recorded in PR #2 before merge.
- Accepted M2 merge: `61aa673653dd31188d2627d76cbba3f97fed6137`.
- New annotated formal tag: `blockchain-v0.2.0-m2`; remote tag object
  `85851d22fac1292112b17ade9b2e140dc17f1089`, dereferenced to that merge.
- Passing merged-main CI:
  <https://github.com/Eason-NotFound/PoG/actions/runs/37050711527>.
- Passing formal-tag CI:
  <https://github.com/Eason-NotFound/PoG/actions/runs/37050807537>.
- All three historical M1/M2 candidate or accepted tags retain their original
  tag objects and target commits. Formal M2 is a new tag, not an RC rename.
- Archival-status updates use `codex/blockchain-m2-archive-status` and a normal
  PR/CI merge. They do not change the formal tag or accepted technical artifacts.
- The frozen M2 spec/runbook/review describe their original candidate gate;
  the ABI package retains `0.2.0-m2-rc.1` as its artifact ID. This current status
  and formal Git tag record subsequent explicit acceptance without rewriting
  the 18-file verified snapshot. M3 still requires a separate authorization.
- The old Escrow draft is ignored locally, not compiled, committed or published.
- The user authorized removal of mandatory GitHub human reviews on 2026-10-02;
  main still requires PR/CI and forbids deletion/force push. Version tags remain
  immutable. The existing ruleset was updated in place and read back before merge.
- M1 and M2 merge/version archival completed with passing CI. No online Hank
  review is claimed; the user's explicit M2 acceptance authorized its merge.

## Scope guard

- M2 V2 custody, approvals, Foundation release, mock settlement attestation and
  refund composition are technically verified and accepted. No real supplier payment proof
  or actual AI service is claimed; the obsolete draft is not a deliverable.
- No deployment, proxy, NFT, DAO, real stablecoin, cross-chain, Chainlink, or zkML
  work. M1 archival is complete; revised M2 scope is frozen in `M2_V2_SPEC.md`.
- The authorized M2 merge/archive is complete. Report and stop; do not deploy
  Anvil, implement API/model/database/payment services or start M3.

## Next gate

The user has accepted M2 V2 and authorized its merge and formal archival only.
PR #2 and `blockchain-v0.2.0-m2` complete that gate. Report the archival result
and stop. M3 may begin only after a NEW explicit user authorization. Do not
resume direct-vendor implementation. Any status-only checkpoint gets normal CI too.

M1 technical review has passed, including an independent full-suite rerun,
strict source lint, ABI comparison and 2,000 runs of each of three fuzz tests.
M1 acceptance/archival is complete. The old direct-vendor M2 approval-input plan
is historical, not the current release-to-Foundation interface. Current signatures,
deadlines, receipt proofs and settlement are frozen in the implemented V2
interface. See `docs/VERSION_CONTROL.md` for version gates.

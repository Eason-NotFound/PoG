# PoG collaboration rules

## Latest architecture change — takes precedence

- On 2026-10-02 the user superseded the direct-vendor M2 plan: Foundation creates
  the project; each Donor converts simulated HKD to stablecoins and donates to
  that specific project's on-chain Escrow; funds are locked; PO is reviewed;
  Invoice/goods evidence and Recipient receipt confirmation precede approval;
  stablecoins are released to Foundation, converted back to HKD by the platform,
  then used for the final transfer.
- On 2026-10-03 the user approved invoice-limited Foundation release, continued
  locking while a project is active, and closure/reconciliation followed by
  proportional stablecoin refunds to original Donors. Revised M2 may proceed.
- On 2026-10-03 the user explicitly accepted M2 V2: AI assessments are risk
  evidence; human approvals are the final fund decisions. MockHKD, simulated
  supplier payment, and no live AI/API/database deployments are accepted limits.
  PR #2 was merged as `61aa673`; the new annotated accepted tag is
  `blockchain-v0.2.0-m2`. Archival is complete; report and STOP. M3 is not authorized.
- Preserve M1 source, tests, ABIs, frozen specifications and immutable tags. Add
  independent `PoGRegistryV2` and `ProcurementEscrowV2`, reusing `MockHKD`.
  The obsolete direct-vendor Escrow draft is archived locally, not published.
- `docs/M2_V2_SPEC.md` is the current implementation scope; the flow is in
  `docs/FOUNDATION_SETTLEMENT_FLOW.md`. Version-2 signing domains are mandatory.
  No release of all project funds, discretionary sweep, or automatic migration
  of donor money to other projects is authorized.
- FundsReleased to Foundation is not supplier Paid. New state, signing, evidence
  and settlement boundaries need an explicit versioned interface design. Do not
  silently reuse M1's supplier-payment callback as proof of fiat supplier payment.
- The preceding M2 coding instructions are superseded where they conflict with
  this architecture change. M3 and real-value payment integrations remain
  unauthorized. Do not implement real fiat conversion merely from this flow draft.

## Repository and scope

- The team repository is https://github.com/Eason-NotFound/PoG.
- Read `docs/STATUS.md`, `docs/MILESTONES.md`, `docs/M2_V2_SPEC.md` before V2 work.
  `docs/BLOCKCHAIN_SPEC.md` and `docs/INTEGRATION_CONTRACT.md` remain historical
  M0.1/M1 specifications; their direct-vendor and no-refund rules are superseded
  for V2, not retroactively changed for M1.
- M0.1 and M1 have passed technical review. The user accepted M1 on 2026-10-02
  and confirmed Hank's offline approval; no GitHub approving review was claimed.
  M1 implements MockHKD and PoGRegistry; ProcurementEscrow remains a test double.
- M2 V2 has passed technical review and explicit user acceptance and is merged
  and formally archived. Preserve its contracts, tests, ABIs, 18-file technical
  snapshot and both M2 tags. Status-only archival documentation is not M3 work.
- The original direct-vendor M2 authorization and its reserveAmount / unchanged
  ApprovalIntent interface constraints are historical and superseded by the
  latest architecture change above. New signing, release and settlement
  interfaces must be versioned and frozen before revised coding. M3 remains
  unauthorized.
- Preserve accepted M1 contract history and tags. Any necessary new-version
  Registry/token change must be explained and approved; do not overwrite the
  accepted historical snapshot or silently change its interpretation.
- Complete one authorized milestone, report actual tests and findings to the CEO
  and user, then STOP. The user must authorize the next milestone.
- Changes to already reviewed code require a new branch, an explicit explanation
  of the change, regression checks, and user approval before merge.

## Version control

- Use `codex/blockchain-<milestone>-<purpose>` for blockchain work.
- Develop on a feature branch based on the latest accepted `origin/main` once
  main exists. Inspect and preserve all existing changes and other teams' work.
- Never directly push to main, force-push, rewrite published commits, or move or
  delete an existing version tag. The sole initialization exception is an empty
  root commit in an empty remote; the hook rejects application/configuration
  content for this exception. Do not bypass the local hooks.
- Push verified candidates to their feature branch and open a PR. Include the
  milestone, base version, exact verification results, ABI/interface changes,
  known gaps, and rollback reference.
- Passing tests means technically verified; it does not mean user acceptance or
  approval to merge/start the next milestone.
- GitHub human approving reviews are not required, by explicit user decision on
  2026-10-02. Preserve PR/CI gates, no-force-push rules, immutable tags, and the
  user's milestone acceptance gate; do not confuse offline approval with a
  GitHub review. This does not authorize direct main pushes or auto-acceptance.
- After explicit user acceptance and merge, create a new annotated version tag.
  Preserve the old tag and commit permanently; corrections get a new commit/tag.
- The first main initialization in an empty repository must be reported to the
  user; it is not blanket authority to push subsequent work directly to main.

## Interfaces and verification

- Run `bash scripts/check-blockchain.sh` before handing off blockchain work.
- Do not modify vendor library sources. Dependencies and CI actions are pinned.
- EIP-712 fields/domain, enums, hash formulas, events, amount precision, and ABI
  changes are breaking integration changes. Update version and interface docs
  and coordinate the affected groups before acceptance.
- Explain test doubles explicitly. A Registry callback test is not proof of a
  token payment or of human approval verification.
- Never commit secrets, raw private evidence, wallet files, real-value assets,
  or private production keys. All amounts use token atomic integer units.
- Do not extend scope to frontend, backend, AI model training, real payment rails,
  public deployments, or real stablecoins without a task authorizing that scope.

See `docs/VERSION_CONTROL.md` for repository setup and the acceptance workflow.

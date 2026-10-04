# PoG collaboration rules

## Current scope and task authority

- Published snapshot for this documentation: 2026-10-04, main
  `d74a7a0ef53abe32640f019ce1551bf5c2432826`. Recheck the actual branch/commit
  before work; do not use an old empty-repository snapshot as current state.
- API A2 and M3.2 local integration tools have been separately authorized and
  published. The AI R2 format/A2 profile is frozen. Old M3.1 "no API/M3.2"
  stops below are historical and do not override later explicit user tasks.
- Published main contains a local JSON portal demo, an API flow ending at
  `ReceiptConfirmed`, contract/deployment tools and offline AI protocol tools.
  Separate local Qwen work does not prove published runtime integration,
  approved scoring/signing policy or full-chain acceptance.
- Follow the latest user-authorized task. Identify the module, exact baseline,
  permitted files, inputs/outputs and acceptance criteria before coding. This
  file does not authorize automatic merge, deployment, model training or real
  payment. See `docs/STATUS.md` and `docs/PROJECT_OVERVIEW.md` for current scope.

## Module ownership and handoffs

- Overall coordination maintains the business chain, module ownership,
  interface dependencies, development order and milestones. AI ownership
  covers AI task decomposition/review and its external adapter, not the whole
  frontend, backend or payment system.
- API ownership covers trusted file/database snapshots, authentication,
  operation/report persistence, durable nonce coordination and relayer flow.
  Contract ownership covers the frozen ABI/signing/state boundaries. Changes
  across these boundaries must be agreed before separate implementations diverge.
- Git integration owns isolated branches/directories, public scope and reviewed
  integration. Independent acceptance verifies the delivered exact version.
  Implementation, review, Git intake, format freeze, runtime integration and
  independent acceptance are separate states; only feedback advances status.
- Treat coding/coordinator chats as development roles, not product runtime
  components. Do not assume chats share history or automatically receive tasks.
- Before messaging another chat, confirm it is idle. Defer active or unknown
  recipients until their current prompt ends; never interrupt or inject a task.
  Check for duplicate handoffs. Give concise steps, file scope, verification and
  required delivery evidence, not a list of unexplained technical terms.
- Give parallel coding tasks isolated working directories/branches. Return
  changes, exact commit, test commands/results, known issues and interface
  changes. Revisions to the same task stay with its original execution owner.
- Keep private coordination records, chat history, data, models and logs outside
  the repository. Check tracked files, staged files and diff before publication;
  `.gitignore` alone is not a publication review.

## Approved architecture and historical checkpoints

The dated items below preserve the approval sequence. Historical stop clauses
apply to that checkpoint only; later scoped tasks/publications supersede them.

- On 2026-10-03 M3.1 received user acceptance and GitHub upload authorization. Under the established version workflow this authorizes M3.1
  merge/formal archival and a report only. PR #4 merged as `977ea62`; new
  annotated accepted tag `blockchain-v0.3.1-m3.1` points to that merge. Preserve
  RC `6295409`, the accepted implementation and every old tag. Archive-status
  changes use a separate PR/CI and never rewrite the technical snapshot.
  M3.1 archival required a report and STOP. The subsequently authorized and
  published API A2 local-chain scope supersedes the earlier M3.2/API onboarding
  stop for that scope only. A3/A4 and later milestones still require separate
  scope authorization. This M3.1
  acceptance supersedes earlier M3.1 candidate-gate wording below, not its
  technical limits or the later milestone gates.
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
- The user's subsequent request on 2026-10-03 authorizes M3.1 if no blocking
  product decision is needed. There is none for the local-only default: implement
  and verify Anvil startup/deployment/status/reset, independent test-role wallets,
  mutual V2 binding and an address/ABI/bytecode manifest on chain 31337.
  Bind RPC to 127.0.0.1 only. Preserve every accepted M1/M2 technical artifact
  and tag. M3.2/API/relayer, M3.3/AI/payment services and M3.4/booth integration
  are NOT authorized. Publish a verified M3.1 candidate through PR/CI, report and
  STOP for user acceptance; do not merge or issue an accepted tag automatically.
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
  interfaces must be versioned and frozen before revised coding. Later
  authorizations are scoped separately; M2 archival itself did not authorize M3.
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

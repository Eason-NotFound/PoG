# PoG collaboration rules

## Repository and scope

- The team repository is https://github.com/Eason-NotFound/PoG.
- Read `docs/STATUS.md`, `docs/MILESTONES.md`, `docs/BLOCKCHAIN_SPEC.md`, and
  `docs/INTEGRATION_CONTRACT.md` before blockchain work.
- M0.1 and M1 have passed technical review. M1 implements MockHKD and
  PoGRegistry; ProcurementEscrow remains a test double. M2 is not authorized.
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

# PoG API A2 candidate status

## Local review hold — 2026-10-03

The latest user direction authorizes local M3.2 integration with CEO, while
retaining review before any GitHub update. All R1–R12 work on `9189d8c` is saved
as local recovery commit `f1b7c92`; integration uses the separate local branch
`codex/api-db-a2-local-review-integration`. No main merge/tag/push was made.
See `A2_REVIEW_FIXES.md` for actual results: 364 full PostgreSQL tests, 31 later
manifest/fallback cases, isolated live path 1 + renewal 4 + nonzero vectors 1,
and accepted blockchain 81/local-chain 32, all with 0 skip. The newer supplemental
cases have not been represented as one latest full-suite run.

Remote PR #9 independently advanced through Hank's `194ae3e`/`bcd5de4`, including
published migration 003. It is NOT this local draft or its verification. Both
histories are preserved. The local merge retains the remote commits and unchanged
published 003, with additive guards in successor 004. Historical hash/error/audit
formats and both test sets are retained. The stable local candidate is the clean
HEAD of the named local branch; integrated exact-tree regression results and its
full SHA are recorded in `.local/A2_LOCAL_REVIEW_HANDOFF.md`. New exact-candidate
GitHub CI remains on hold. No A2 user acceptance or A3/A4 authority is inferred.

## Original candidate record (superseded verification scope)

- Base: accepted `origin/main` merge `1844186df10854cd49ccc0886f5622e71e572d8e`
  (`api-v0.1.0-a1`).
- Scope: local-only deployment gate, restricted V2 actions, EIP-712 signing,
  durable transaction worker, canonical receipt/event confirmation, ledger read
  model and reorg rebuild.
- Stop point: Recipient `ReceiptConfirmed` only.
- Explicitly absent: real AI/payment/conversion, release, settlement, closing,
  refund execution, frontend and LAN/public deployment.
- Account preference: `foundation`, `recipient`, `donor`, `admin`; `admin` remains
  only the human approver role.
- Original candidate `9189d8c286af2314e136d1de843da5cb5be6d407` verification:
  97 API/PostgreSQL tests passed with skip=0; the isolated
  Anvil/PostgreSQL ReceiptConfirmed path, all three typed families, five human
  terms vectors and snapshot/revert canonical rebuild passed with skip=0. The
  accepted blockchain suite passed 81/81 and local-chain lifecycle passed 32/32,
  both with skip=0.
- Confirmation requires exact caller/target/resource/amount event matching plus
  the relevant canonical getter or ledger state. Multi-worker sends use a
  recoverable 15-second persisted `sending` lease and exact-envelope recovery.
- Subsequent integration fixes add resource-bound idempotency, audited expiry
  of never-submitted signatures, Recipient evidence ownership, destructive-test
  target validation, canonical state references and complete deployment proofs.
  Migration head is `c31003a20003`. Verification of the original candidate does
  not verify these changes; use the exact current PR head and its CI checks.
- This local demonstration does not establish production or full-chain acceptance.

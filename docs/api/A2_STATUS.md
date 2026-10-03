# PoG API A2 implementation scope

## Published baseline

- API A1: `api-v0.1.0-a1`, commit `1844186df10854cd49ccc0886f5622e71e572d8e`.
- API A2: `api-v0.2.0-a2`, commit `4c9f1a40ac225d684d00b5abcf8081c43594bfcc`.
- Database migration head: `c31003a20004`. Published `c31003a20003` is unchanged;
  successor 004 adds guards and is forward-only. Backup/restore rehearsal remains
  **PENDING**; see [A2_REVIEW_FIXES.md](A2_REVIEW_FIXES.md).

## Supported local demonstration

- Local-only deployment gate, restricted V2 actions, EIP-712 signing,
  durable transaction worker, canonical receipt/event confirmation, ledger read
  model and reorg rebuild.
- HTTP stop point: Recipient `ReceiptConfirmed`.
- Real AI/payment/conversion, release, settlement, closing, refund execution,
  frontend and LAN/public deployment are outside this API scope.
- Standard usernames: `foundation`, `recipient`, `donor`, `admin`; `admin` maps
  only to the independent human-approver role.
- Confirmation requires exact caller/target/resource/amount event matching plus
  the relevant canonical getter or ledger state. Multi-worker sends use a
  recoverable 15-second persisted `sending` lease and exact-envelope recovery.
- Resource-bound idempotency, audited retirement of eligible never-submitted
  signatures, Recipient evidence ownership, destructive-test target validation,
  canonical state references and complete deployment proofs protect recovery.

## Verification limits

Reproducible test entry points and protected local commands are documented in
[A2_REVIEW_FIXES.md](A2_REVIEW_FIXES.md). Historical test counts do not establish
the result of a new version: use the exact commit and its
[GitHub Actions checks](https://github.com/Eason-NotFound/PoG/actions).
This document does not claim new test execution or independent acceptance.
The local synthetic demonstration does not establish real-model, production,
payment or full-chain acceptance.

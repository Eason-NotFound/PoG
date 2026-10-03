# PoG API A2 candidate status

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

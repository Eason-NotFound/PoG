# Security Checklist — M0 Baseline

Items marked **M0 frozen** are requirements, not evidence that unimplemented
contracts satisfy them.

## Authority and keys

- [x] **M0 frozen:** Registry is deployed first; Escrow stores Registry immutable;
  Registry binds the mutually verified Escrow once before projects and cannot
  rebind.
- [x] **M0 frozen:** Registry-only hooks and Escrow-only callbacks authenticate the
  exact counterpart with no admin fallback.
- [x] **M0 frozen:** AI assessment keys have no approval or payment role.
- [x] **M0 frozen:** relayer possession grants gas submission only.
- [x] **M0 frozen:** admin cannot bypass human approval threshold.
- [x] **M0 frozen:** vendor is nonzero and immutable per procurement.
- [x] **M0 frozen:** 1-of-1 default uses an epoch/threshold model for future N-of-M.
- [ ] Implementation: role grants/revocations and epoch changes are tested.
- [x] **M1 evidence:** Registry owner controls only binding, AI allowlist, and
  pause; Foundation and counterpart-only authorization plus policy epoch sync are
  unit-tested.
- [ ] Operations: demo keys are isolated, funded minimally, and never committed.

## Signatures and replay

- [x] **M0 frozen:** chain ID, verifying contract, name, and version are domain-bound.
- [x] **M0 frozen:** exact-next per-signer nonces; deadlines/assessment expiry enforced.
- [x] **M0 frozen:** policy epoch, action, assessment, and full terms are signed.
- [x] **M0 frozen:** distinct approvers count once; aggregation key consumed once.
- [x] **M0 frozen:** ECDSA malleability/nonzero recovery and ERC-1271 magic value rules.
- [ ] Implementation: replay across action, project, chain, deployment, epoch, and
  signer is fuzz-tested.
- [x] **M1 evidence:** AI assessment wrong signer/domain/stage/evidence/ID,
  exact-next nonce, replay rejection, issued/expiry windows, risk bound, and
  downstream expiry are unit/fuzz-tested. Human approval replay remains M2.

## Funds and token behavior

- [x] **M0 frozen:** asset address is configurable; RedCoin is never hard-coded.
- [x] **M0 frozen:** 6-decimal atomic-unit policy; no floating-point transport.
- [x] **M0 frozen:** reserve/pay/cancel accounting conservation equations.
- [x] **M0 frozen:** fixed vendor only, invoice not above reservation, no duplicate pay.
- [x] **M0 frozen:** unsupported fee/rebase/callback token behavior rejected.
- [x] **M0 frozen:** no donor refund, project closure, administrative withdrawal,
  or `totalRefunded` accounting exists in MVP.
- [ ] Implementation: safe transfer, checks-effects-interactions, reentrancy guard,
  balance-delta deposit, and zero-address tests.
- [x] **M1 evidence:** MockHKD metadata, public demo mint, zero recipient/amount,
  transfer, approve, and transferFrom behavior are tested. Escrow token transfer
  controls remain M2.
- [ ] Implementation: invariant/fuzz tests cover conservation and solvency per asset.

## Lifecycle and evidence

- [x] **M0 frozen:** explicit forward state machine and terminal states.
- [x] **M0 frozen:** both AI assessments are required evidence but never approval.
- [x] **M0 frozen:** pre AI signs the exact request/quote evidence hash; final AI
  signs the exact reserve/PO/GRN/invoice evidence hash, both recomputed on-chain.
- [x] **M0 frozen:** PO, GRN, invoice, report, evidence, and Allocation Root hashes.
- [x] **M0 frozen:** duplicate business transitions revert rather than overwrite.
- [ ] Implementation: authorization and every illegal transition are tested.
- [x] **M1 evidence:** Registry legal/illegal transitions through paid callbacks,
  counterpart-only access, binding spoof/rebind, cross-contract rollback,
  callback reentrancy, policy-update-during-pending-approval, cancellation, and
  root versioning are tested.
- [ ] Integration: uploaded bytes are hashed canonically and privacy-reviewed.

## Relayer, API, and chain finality

- [x] **M0 frozen:** idempotency-key conflict semantics and calldata-hash dedupe.
- [x] **M0 frozen:** transaction submission is not success; matching confirmed event is.
- [x] **M0 frozen:** reorg/orphan handling and receipt reconciliation are required.
- [ ] Integration: simulations, revert decoding, replacement transaction rules, and
  indexer rewind are exercised on Anvil.
- [ ] Deployment: manifest chain ID, address, tx hash, bytecode hash, ABI version,
  and explorer verification are checked before UI use.

## Explicit exclusions

- [x] No proxy/upgradability, NFT, DAO, real stablecoin, cross-chain, Chainlink, or
  zkML scope.
- [x] No secrets, private keys, live addresses, or meaningful funds in M0.

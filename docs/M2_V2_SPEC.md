# M2 V2 — Foundation release and project refunds

Frozen scope: 2026-10-03, Asia/Hong_Kong. The user approved continuing revised
M2. This supersedes conflicting M0.1/M1 direct-vendor payment rules only for V2.
M2 ends with a verified candidate and user report; no merge, deployment or M3.

## 1. Version boundary and ownership

Add independent `PoGRegistryV2`, `ProcurementEscrowV2`, and their interface.
Reuse the unchanged, valueless 6-decimal `MockHKD`. Preserve M1 contracts, tests,
ABI files, specifications and baseline hashes exactly. Do not inherit the old
Registry's Paid callback or reinterpret M1 events.

Registry V2 owns project actors/lifecycle, procurement facts, evidence hashes,
AI assessments, Recipient receipt commitments, and mock settlement evidence.
Escrow V2 owns credited donor deposits, reservations, exact token transfers,
human policies/nonces/votes, returns and refund snapshots. Use Registry-first
deployment and one-time mutually verified binding, exact counterpart-only hooks,
no admin bypass, atomic callbacks, and shared reentrancy protection.

The team integration will use these V2 contracts, not M1's Registry. No proxy,
mainnet, public testnet, real stablecoin, real banking or real model service in M2.
No Allocation Root is required in V2 M2; the old M1 feature remains historical.

## 2. Actors, amounts and project state

Foundation creates project with fixed Foundation, expected Recipient, token and
human approval policy. Fixed vendor is chosen per procurement. Addresses cannot
be patched to redirect money. Foundation, Recipient, AI signer, human approver,
Donor and relayer are distinct authorities; a UI role does not confer authority.

All amounts are integers in token atomic units. Demo conversion is 1 HKD to
1 mHKD with zero fees, simulated by the other teams; contract mint/transfer
does not prove actual HKD receipt or backing. Assets must be supported plain,
nonrebasing ERC20s; exact balance deltas reject fee/no-op transfers. Do not
support callback-bearing tokens. Tests must still cover adversarial callbacks.

Project states: Active -> Closing -> Refundable -> Closed. Active funds remain
locked. Donors `approve` then deposit into a specific project's Escrow; only
the actual caller receives donation credit. Direct token transfers are surplus,
not project donations, and never authorize spending or refunds.

Foundation requests Closing; no new deposit, procurement or reservation then.
Existing reserved obligations can finish evidence, release and settlement.
Final reconciliation requires no unresolved procurement, reservation or
unconfirmed release and current threshold human approval. It freezes a refund
pool from the actual project balance. Zero-pool projects close immediately;
otherwise Refundable continues until every original donor has claimed.
No return to Active, closure timeout, admin withdrawal or arbitrary destination.

For bounded, predictable booth operation, a project supports at most 64 distinct
donor wallets, with unlimited repeat deposits from those wallets while Active.
Approver lists must also have a reasonable explicit bound (at most 16).

## 3. Procurement and evidence sequence

Normal milestones (exact enum names may be made consistent in source/ABI):

1. Foundation creates procurement with project, fixed vendor and budget cap.
2. Foundation records immutable PO plus procurement/goods-request commitments.
3. Authorized AI signs pre-review of that PO evidence; human signs reserve terms.
4. Threshold-approved reservation locks an amount <= cap from project freeLocked.
5. Foundation records immutable Invoice and goods/delivery commitments. Invoice
   is positive and <= reservedAmount; no invoice exists at initial project funding.
6. Expected Recipient signs receipt evidence for this invoice/PO/goods tuple.
7. Authorized AI signs final review matching all current evidence and receipt.
8. Humans approve release of exactly invoiceAmount to fixed Foundation.
9. Escrow transfers that amount once. Unused reservation stays in project,
   locked and available for a later approved procurement, not Foundation cash.
10. Foundation records mock conversion/payment evidence for fixed vendor and
    full invoiceAmount. Current human threshold confirms the mock payment.
    Only this final operation marks PaymentConfirmed and resolves the obligation.

Required distinct states/events: FundsReleased vs MockPaymentConfirmed. The
contract proves the former token transfer, and only attests to the latter signed
mock evidence. It cannot prove real goods delivery, bank settlement or force a
Foundation to use its transferred stablecoins for a supplier.

PO, Invoice/goods, Receipt and settlement evidence are immutable in their stage.
Incorrect pre-release materials use cancellation/recreation, not silent edits.
Unreserved procurement may be cancelled by Foundation. Reserved cancellation
requires human approval of a bound cancellation-reason hash and is allowed only
before Recipient receipt confirmation. It releases reservation, not a payment.
After receipt confirmation, Foundation cannot cancel to erase supplier debt.

An expired AI assessment may be replaced by a newly signed report for the SAME
immutable evidence. A replacement invalidates old action terms and human votes;
it must be a new assessment ID, new exact-next signer nonce and current deadline.
No mutation of an existing assessment. This V2 renewal rule avoids a project
being permanently stuck after receipt if an AI signature expires.
Renew pre-assessments only before reservation executes and final assessments
only before release executes. Never rewind an executed milestone or resurrect
Cancelled/MockPaymentConfirmed. Returning from a pending approval to the matching
Assessed state is allowed, but all old assessment bundles cease to be current.

## 4. Signatures and policies

Registry domain: name `PoGRegistryV2`, version `2`. Escrow domain:
name `ProcurementEscrowV2`, version `2`. Both bind chainId and verifyingContract.
Publish exact type strings, fields/order/widths and hash formulas with source
and integration hand-off. Use EIP712/SignatureChecker for EOA and ERC1271.

AI: stage, procurementId, assessmentId, outcome, riskScoreBps, evidenceHash,
reportHash, signer, nonce and deadline all bound. Only current allowlisted AI
signers; exact Registry-recomputed evidence; exact-next nonce. At reserve/release
execution recheck current assessment ID, evidence, signer allowlist and expiry.
AI outcome is risk evidence, NOT spending approval; human makes final decision.

RecipientReceipt binds projectId, procurementId, expectedRecipient, vendor,
poHash, invoiceHash, invoiceAmount, goodsHash, receiptEvidenceHash, nonce and
deadline. Reject wrong signer/domain/chain, expired submission and replay.
Anyone may relay, but Foundation cannot sign on Recipient's behalf. A validly
accepted receipt is a historical fact commitment; its submission deadline does
not later erase confirmed receipt. Final evidence includes its stored digest.

Human actions must separately represent reservation, Foundation release, mock
payment confirmation, reserved cancellation, and final project close. Each
signed intent binds its target ID, action, exact termsHash, assessment where
applicable, signer, exact-next nonce, deadline and current policy epoch. Exact
terms include project, procurement where relevant, Foundation, Recipient,
vendor, asset, amounts and all evidence/assessment/reason/settlement hashes used
by the action. A close intent binds the reconciled ledger/refund snapshot.
Do not accept a free-form unsigned amount or destination.

Threshold policy supports 1-of-1 booth demo and N-of-M tests. Duplicate live vote
rejected; expired same signer may renew using fresh nonce but never counts twice.
Execution counts only unexpired votes by current members in current epoch;
recompute current exact terms. A successfully verified ERC1271 vote is stored;
do not promise dynamic ERC1271 revalidation at later execution.

Foundation can change its own project's policy through Registry only. A change
increments the exact next epoch in both contracts atomically and invalidates
prior bundles; do not preserve votes across epochs. This deliberate V2 rule
allows recovery from expired pending actions without an admin spending shortcut.

## 5. Custody, returned funds, closure and refunds

Per-project accounting: D = credited deposits, Q = outstanding reservations,
L = gross stablecoins released to Foundation, T = actual stablecoins returned,
F = refunded stablecoins. freeLocked = D + T - L - Q - F; project custody
liability = D + T - L - F. Never count unconfirmed screenshot amounts, token
surplus or money still in Foundation's wallet in the refund pool.

Foundation must explicitly request Closing before returning released funds.
Return is Closing-only, so returned money cannot be recommitted to new purchases
while the original supplier debt remains unresolved. Foundation may then return
up to its net released stablecoins for an unresolved procurement via actual
transferFrom into Escrow. Track this explicitly and test
balance deltas. A return adds T but NEVER automatically cancels an invoice,
marks PaymentConfirmed, or resolves a procurement. M2 only confirms full mock
supplier payment; partial payments, writeoffs and post-release debt cancellation
are not implemented. A release with any returnedAmount > 0 is an unresolved
exception and cannot be marked full MockPaymentConfirmed in M2: no implicit
Foundation top-up or double-counted cost. It keeps Closing blocked. No forced
clawback from Foundation; unused wallet money must actually return first.

Reconciliation/close cannot proceed until every procurement is legally terminal
(pre-receipt Cancelled or full MockPaymentConfirmed) and Q=0. All release-bearing
procurements must have completed mock settlement; a return alone is insufficient.
Current human threshold signs the exact close snapshot before it is frozen.

Snapshot refund R and total donation D, using the fixed first-seen donor order
and cumulative credits at closure. For each donor's cumulative interval [a,b],
refund = floor(R*b/D) - floor(R*a/D), using overflow-safe Math.mulDiv. At most
64 iterations occur once at close; claims are O(1), sent only to msg.sender as
the original donor. This produces exactly R with less than one atomic unit
rounding deviation per donor; no residual dust or last-claimer sweep.

One claim per donor, including a zero-rounded entitlement; effects before safe
transfer, skip zero transfer, rollback on failure. Unclaimed entitlements remain
locked indefinitely, no expiry/sweep. Track refund liability per project and
token globally; one project's funds never pay another's obligations.

## 6. Required verification and hand-off

Preserve all M1 checks and hashes. New V2 unit/integration, adversarial and fuzz
or invariant tests must cover full path, AI-only/human-only failures, forged
Recipient, exact invoice-limited release, duplicate release, wrong recipient/
vendor/token/project/domain/action, nonce replay, expired human renewal,
assessment renewal and epoch change, ERC1271, counterpart spoofing, token
failure/reentrancy and atomic rollback, Closing restrictions plus settling old
obligations, debt blocking refunds, returned stable not erasing debt, repeated
donor deposits, 64-donor bound, exact rounding/zero claim/replay, and shared-asset
multi-project solvency. Build sizes must respect 24,576-byte EVM limit.

M2 delivers generated V2 ABIs and precise V2 integration instructions for front
end/API/AI/payment teams, not an implemented backend/model/mock HKD ledger.
Run formatting/build/full tests/strict lint/ABI consistency/push-guard checks,
independent review and regression before publication. Report findings and stop
for user acceptance; no M3 deployment manifests or Anvil launch yet.

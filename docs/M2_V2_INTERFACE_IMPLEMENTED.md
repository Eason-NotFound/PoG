# M2 V2 implemented contract interface

Implementation candidate: 2026-10-03. This describes the V2 source and generated
ABIs; it does not imply deployment, M2 acceptance, or real-money settlement.
Amounts are 6-decimal token atomic units.

## Deployment and domains

Deploy `PoGRegistryV2(initialOwner)` first, deploy
`ProcurementEscrowV2(registry)`, then call `registry.bindEscrow(escrow)` once.
The Registry verifies the Escrow back-reference. Both contracts reject spoofed
counterpart hooks.

- Registry EIP-712 domain: name `PoGRegistryV2`, version `2`.
- Escrow EIP-712 domain: name `ProcurementEscrowV2`, version `2`.
- Both domains also bind the current `chainId` and verifying contract.

The owner can pause Registry-driven normal operation and manage the AI allowlist;
the owner has no custody withdrawal or approval bypass. A project Foundation
alone creates its procurements, evidence, policy updates and Closing request.
Reserved cancellation and actual Foundation returns remain available while
paused as recovery operations. Returns are accepted only after the Foundation
has moved the project to `Closing`.

## Lifecycle and primary calls

Project state is `Active -> Closing -> Refundable -> Closed`. A zero refund pool
moves directly from Closing to Closed. A nonzero pool remains Refundable until
every original donor has made its one claim, including donors with a zero-rounded
entitlement.

Numeric enum ordinals are integration-critical:

```text
ProjectState: Active=0, Closing=1, Refundable=2, Closed=3
ProcurementState:
  Created=0, PORecorded=1, PreAssessed=2, ReserveApprovalPending=3,
  Reserved=4, InvoiceRecorded=5, ReceiptConfirmed=6, FinalAssessed=7,
  ReleaseApprovalPending=8, FundsReleased=9, SettlementRecorded=10,
  SettlementApprovalPending=11, PaymentConfirmed=12,
  CancellationApprovalPending=13, Cancelled=14
AssessmentStage: PrePurchase=0, FinalRelease=1
AssessmentOutcome: Pass=0, Review=1, Reject=2
HumanAction: Reserve=0, ReleaseToFoundation=1, ConfirmMockPayment=2,
  CancelReserved=3, CloseProject=4
```

The normal procurement path is:

`Created -> PORecorded -> PreAssessed -> ReserveApprovalPending -> Reserved ->
InvoiceRecorded -> ReceiptConfirmed -> FinalAssessed -> ReleaseApprovalPending ->
FundsReleased -> SettlementRecorded -> SettlementApprovalPending ->
PaymentConfirmed`.

The implemented application-facing calls are:

```solidity
// Registry administration and project lifecycle
bindEscrow(address escrow)
setAISigner(address signer, bool allowed)
pause()
unpause()
createProject(bytes32 projectId, address recipient, address asset,
              address[] approvers, uint16 threshold)
updateApprovalPolicy(bytes32 projectId, address[] approvers, uint16 threshold)
requestClosing(bytes32 projectId)

// Registry procurement facts
createProcurement(bytes32 procurementId, bytes32 projectId,
                  address vendor, uint256 budgetCap)
recordPurchaseOrder(bytes32 procurementId, bytes32 poHash,
                    bytes32 requestHash, bytes32 goodsRequestHash)
submitAIAssessment(AssessmentInput input, bytes signature)
recordInvoiceAndGoods(bytes32 procurementId, bytes32 invoiceHash,
                      uint256 invoiceAmount, bytes32 goodsHash)
submitRecipientReceipt(RecipientReceiptInput input, bytes signature)
recordSettlement(bytes32 procurementId, bytes32 conversionEvidenceHash,
                 bytes32 paymentEvidenceHash)
cancelUnreserved(bytes32 procurementId)

// Escrow custody and approvals
deposit(bytes32 projectId, uint256 amount)
submitReserveApproval(bytes32 procurementId, uint256 reserveAmount,
                      HumanIntent intent, bytes signature)
executeReserve(bytes32 procurementId, uint256 reserveAmount)
submitReleaseApproval(bytes32 procurementId, HumanIntent intent, bytes signature)
executeRelease(bytes32 procurementId)
submitSettlementApproval(bytes32 procurementId, HumanIntent intent, bytes signature)
executeSettlementConfirmation(bytes32 procurementId)
requestReservedCancellation(bytes32 procurementId, bytes32 reasonHash)
submitCancellationApproval(bytes32 procurementId, HumanIntent intent, bytes signature)
executeReservedCancellation(bytes32 procurementId)
returnReleasedFunds(bytes32 procurementId, uint256 amount)
submitCloseApproval(bytes32 projectId, HumanIntent intent, bytes signature)
executeClose(bytes32 projectId)
claimRefund(bytes32 projectId)
```

`executeRelease` sends exactly `invoiceAmount` to the fixed Foundation, never to
the vendor. It removes the full reservation, leaving its unused portion locked
in the project. `recordSettlement` and `executeSettlementConfirmation` only
record and approve mock full vendor-payment evidence; they transfer no tokens.
`FundsReleased` and `PaymentConfirmed` are distinct states and events.

An unreserved procurement can be cancelled directly before reserve execution.
A reserved procurement can be cancelled with threshold approval in `Reserved`
or `InvoiceRecorded`, but never after Recipient receipt. The cancellation terms
bind a hash of the current PO, invoice, invoice amount and goods evidence.

## Signed structures

Exact Registry type strings:

```text
AIAssessment(uint8 stage,bytes32 procurementId,bytes32 assessmentId,uint8 outcome,uint16 riskScoreBps,bytes32 evidenceHash,bytes32 reportHash,address signer,uint256 nonce,uint64 deadline)

RecipientReceipt(bytes32 projectId,bytes32 procurementId,address expectedRecipient,address vendor,bytes32 poHash,bytes32 invoiceHash,uint256 invoiceAmount,bytes32 goodsHash,bytes32 receiptEvidenceHash,uint256 nonce,uint64 deadline)
```

`assessmentId` is:

```text
keccak256(abi.encode(
  keccak256("POG_V2_AI_ASSESSMENT_ID"), stage, procurementId, outcome,
  riskScoreBps, evidenceHash, reportHash, signer, nonce, deadline
))
```

Exact Escrow type string:

```text
HumanIntent(bytes32 targetId,uint8 action,bytes32 termsHash,bytes32 assessmentId,address signer,uint256 nonce,uint64 deadline,uint32 policyEpoch)
```

Human action ordinals are `Reserve=0`, `ReleaseToFoundation=1`,
`ConfirmMockPayment=2`, `CancelReserved=3`, and `CloseProject=4`.

Every signer uses an exact-next global nonce in its signing contract. Human
votes are keyed by target, action, exact terms, assessment and policy epoch.
Only unexpired votes from current members count. An expired signer can submit a
fresh nonce; a live duplicate is rejected. ERC-1271 is checked when submitted
and the verified vote is stored, rather than dynamically revalidated later.
Read the next nonces from `aiNonces(signer)`, `recipientNonces(recipient)` and
`humanNonces(approver)`. The exact human vote key is:

```text
keccak256(abi.encode(targetId, action, termsHash, assessmentId, policyEpoch))
```

There is intentionally no enumerable approver getter. API consumers should
reconstruct the current list from confirmed `createProject` and
`updateApprovalPolicy` calldata/events for the current epoch and may verify an
address with `isApprover(projectId, address)`.

## Evidence and terms hashes

The exact Registry evidence hashes are:

```text
preEvidenceHash = keccak256(abi.encode(
  keccak256("POG_V2_PRE_EVIDENCE"), projectId, procurementId,
  foundation, recipient, vendor, asset, budgetCap, poHash,
  requestHash, goodsRequestHash
))

finalEvidenceHash = keccak256(abi.encode(
  keccak256("POG_V2_FINAL_EVIDENCE"), projectId, procurementId,
  foundation, recipient, vendor, asset, reservedAmount, poHash,
  invoiceHash, invoiceAmount, goodsHash, receiptDigest
))

settlementHash = keccak256(abi.encode(
  keccak256("POG_V2_SETTLEMENT_EVIDENCE"), projectId, procurementId,
  foundation, vendor, asset, invoiceAmount,
  conversionEvidenceHash, paymentEvidenceHash
))
```

Escrow terms use a common nested `partiesHash`:

```text
partiesHash = keccak256(abi.encode(
  projectId, foundation, recipient, vendor, asset
))
```

The exact nested Escrow formulas are:

```text
reserveTermsHash = keccak256(abi.encode(
  keccak256("POG_V2_RESERVE_TERMS"), partiesHash, procurementId,
  budgetCap, reserveAmount, preEvidenceHash, preAssessmentId
))

releaseTermsHash = keccak256(abi.encode(
  keccak256("POG_V2_RELEASE_TERMS"), partiesHash, procurementId,
  reservedAmount, invoiceAmount, finalEvidenceHash, finalAssessmentId
))

settlementTermsHash = keccak256(abi.encode(
  keccak256("POG_V2_SETTLEMENT_TERMS"), partiesHash, procurementId,
  invoiceAmount, settlementHash
))

cancellationEvidenceHash = keccak256(abi.encode(
  poHash, invoiceHash, invoiceAmount, goodsHash
))

cancellationTermsHash = keccak256(abi.encode(
  keccak256("POG_V2_CANCEL_TERMS"), partiesHash, procurementId,
  reservedAmount, cancellationEvidenceHash, cancellationReasonHash
))

refundPool = deposits - (released - returned) - refunded

ledgerHash = keccak256(abi.encode(
  deposits, returned, released, refunded, reserved,
  refundPool, donorCount
))

closeTermsHash = keccak256(abi.encode(
  keccak256("POG_V2_CLOSE_TERMS"), projectId, foundation, recipient,
  asset, ProjectState.Closing, unresolvedProcurements,
  ledgerHash, policyEpoch
))
```

Renewed AI assessments must use the same immutable evidence, a new computed ID,
the exact-next signer nonce and a live deadline. Pre renewal is permitted only
before reserve execution; final renewal only before release execution. Renewal
from a pending approval moves the procurement back to `PreAssessed` or
`FinalAssessed`. Because the current assessment ID changes, every old human vote
bundle becomes unusable.

## Custody and refunds

For a project, `netReleased = released - returned` and the implementation uses
the overflow-safe equivalent formulas:

```text
freeLocked       = deposits - netReleased - reserved - refunded
custody liability = deposits - netReleased - refunded
```

The Escrow also tracks aggregate liability per token and checks that its real
balance covers that liability before reservations, releases, close snapshots
and refunds, and before/after incoming transfers. A direct token transfer is
surplus and never becomes project credit. Exact before/after balance deltas
reject fee, no-op and other unsupported transfer behavior.

Foundation returns require `Closing`, a still-unresolved released procurement,
and a real exact `transferFrom`. A return restores custody but never resolves
the invoice. Any nonzero returned amount permanently blocks the M2 full mock
payment confirmation, leaving the exceptional debt unresolved and close blocked.

At close, refund pool `R = deposits - netReleased - refunded`. For donor order
interval `[a,b]` and total deposits `D`, entitlement is:

```text
Math.mulDiv(R, b, D) - Math.mulDiv(R, a, D)
```

The bounded close loop handles at most 64 donors. Claims are caller-only, O(1),
effects-first, one-time, and have no expiry or sweep.

Generated ABI arrays are in `packages/contract-abis/v2/`. The V2 MockHKD ABI is
byte-for-byte identical to the accepted M1 MockHKD ABI.

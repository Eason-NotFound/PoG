# PoG Blockchain Specification — M0.1

Version: `0.1.1-m0.1`  
Status: frozen for implementation planning; no business contracts implemented

## 1. Contract responsibilities

`MockHKD` is a 6-decimal, freely mintable test-only ERC-20 for local and Base
Sepolia demonstrations. It has no redemption promise or real value.

`PoGRegistry` is authoritative for project identity, actors, asset metadata,
procurement lifecycle, evidence commitments, AI assessments, policy metadata, and
Allocation Roots. `ProcurementEscrow` is authoritative for token custody, project
accounting, approval membership/counts/nonces, consumed approval bundles,
reservations, and payments to the fixed vendor.

Deployment order is fixed:

1. Deploy `PoGRegistry` with protocol admin; its `escrow` is initially zero.
2. Deploy `ProcurementEscrow(registry)`; its Registry address is immutable.
3. Before any project exists, protocol admin calls
   `PoGRegistry.bindEscrow(escrow)` exactly once.
4. `bindEscrow` requires a nonzero contract and
   `ProcurementEscrow(escrow).registry() == address(this)`, then permanently
   locks the address. Rebinding, unbinding, or an admin override is impossible.
5. Project and lifecycle operations revert until binding. Binding reverts if any
   project exists or if an Escrow is already bound.

Thus Registry is immutable in Escrow bytecode; Escrow is one-time locked in
Registry storage.

## 2. Roles and authority

| Role | Address source | On-chain authority |
| --- | --- | --- |
| Protocol admin | deployment multisig/test admin | bind Escrow once, configure AI signers, emergency pause; no policy, reserve, payment, hook, or callback bypass |
| Foundation | `RegistryProject.foundation` | create project/procurement, record evidence/documents, initiate policy changes/cancellation, publish Allocation Root |
| Recipient | `RegistryProject.recipient` | observable beneficiary/context party; no default spending authority |
| Donor | any address | deposit supported asset and verify events/roots; no approval authority by donation alone |
| Human approver | project approval policy | sign `ApprovalIntent`; threshold participation only |
| AI signer | Registry allowlist | sign `AIAssessment`; never signs `ApprovalIntent` and never calls reserve/pay with authority |
| Relayer | any submitter in MVP | submit valid signed messages and permissionless execution calls; obtains no authority from submission |
| Vendor | `Procurement.vendor` | receive payment only; address fixed for the procurement |

The MVP policy is threshold 1 with one configured human approver and supports
future N-of-M. Every policy replacement increments `approverEpoch`. Neither
protocol admin nor Foundation can call counterpart-only methods or bypass a
current, unconsumed threshold approval.

## 3. Canonical data model

All IDs and hashes are `bytes32`. All times are Unix seconds in `uint64`. Monetary
values are token atomic units in `uint256`.

```solidity
enum ProcurementStatus {
    Draft,
    PreEvidenceRecorded,
    PreAssessed,
    ReserveApprovalPending,
    BudgetReserved,
    POIssued,
    Delivered,
    InvoiceSubmitted,
    FinalAssessed,
    PaymentApprovalPending,
    Paid,
    Cancelled
}
enum AssessmentStage { PreProcurement, FinalPayment }
enum AssessmentOutcome { Pass, Review, Reject }
enum ApprovalAction { ReserveBudget, ExecutePayment }

// Stored authoritatively in PoGRegistry.
struct RegistryProject {
    bytes32 projectId;
    address foundation;
    address recipient;
    address asset;
    uint8 assetDecimals;
    bytes32 allocationRoot;
    uint64 allocationRootVersion;
    uint32 approverEpoch;
    uint16 approvalThreshold;
    uint64 createdAt;
}

// Stored authoritatively in ProcurementEscrow.
struct EscrowProjectAccount {
    bytes32 projectId;
    address asset;
    uint8 assetDecimals;
    uint256 totalDeposited;
    uint256 reservedOutstanding;
    uint256 totalPaid;
    uint32 approverEpoch;
    uint16 approvalThreshold;
}

// API-only composed view. No contract stores this combined struct.
struct ProjectView {
    RegistryProject registry;
    EscrowProjectAccount account;
}

// Lifecycle/evidence record stored authoritatively in PoGRegistry.
struct Procurement {
    bytes32 procurementId;
    bytes32 projectId;
    address vendor;
    uint256 budgetCap;
    bytes32 requestHash;
    bytes32 quoteBundleHash;
    bytes32 preEvidenceHash;
    bytes32 preAssessmentId;
    uint256 reservedAmount;
    bytes32 poHash;
    bytes32 grnHash;
    bytes32 invoiceHash;
    uint256 invoiceAmount;
    bytes32 finalEvidenceHash;
    bytes32 finalAssessmentId;
    uint256 paidAmount;
    ProcurementStatus status;
    uint64 createdAt;
}

// Custody mirror stored authoritatively in ProcurementEscrow.
struct EscrowProcurementAccount {
    bytes32 procurementId;
    bytes32 projectId;
    address vendor;
    address asset;
    uint256 budgetCap;
    uint256 reservedAmount;
    uint256 paidAmount;
    bool cancelled;
}

struct AIAssessment {
    bytes32 assessmentId;
    bytes32 procurementId;
    AssessmentStage stage;
    bytes32 reportHash;
    bytes32 evidenceHash;
    bytes32 modelId;
    AssessmentOutcome outcome;
    uint16 riskScoreBps;
    uint64 issuedAt;
    uint64 expiresAt;
    uint256 nonce;
    address aiSigner;
}

struct ApprovalIntent {
    bytes32 procurementId;
    ApprovalAction action;
    bytes32 assessmentId;
    bytes32 termsHash;
    uint256 nonce;
    uint64 deadline;
    uint32 approverEpoch;
    address approver;
}

struct ApprovalPolicy {
    bytes32 projectId;
    uint32 approverEpoch;
    uint16 threshold;
    address[] approvers;
}
```

Rules:

- IDs are unique and never recycled. Recommended generation is
  `keccak256(abi.encode(chainId, registry, creator, creatorNonce))`.
- `foundation`, `recipient`, `asset`, `vendor`, `aiSigner`, and `approver`
  cannot be zero when their containing record is created/submitted.
- `riskScoreBps` is 0–10,000. It is descriptive; no outcome or score is an
  approval.
- Assessment identity is exactly
  `keccak256(abi.encode(procurementId, stage, reportHash, evidenceHash, modelId,
  outcome, riskScoreBps, issuedAt, expiresAt, nonce, aiSigner))`; the submitted
  `assessmentId` must match it.
- `AIAssessment.nonce` is exactly the Registry's next nonce for that AI signer and
  is incremented once on acceptance. `ApprovalIntent.nonce` follows the same
  exact-next-nonce rule per human signer in the escrow.
- `expiresAt > issuedAt`; the assessment is usable only while
  `block.timestamp <= expiresAt`. An intent is usable only while
  `block.timestamp <= deadline`.
- `termsHash` is never an arbitrary UI label. It is computed by the formulas in
  section 6.
- A policy has no duplicate/zero approvers and
  `0 < threshold <= approvers.length`.
- Project creation sets initial `approverEpoch` to 1 in both contracts.
  Subsequent successful policy replacements increment it by exactly one.

Registry's reserved/paid amounts, epoch, and threshold are read-model snapshots
updated only by authenticated Escrow callbacks. Escrow accounting and policy
membership are authoritative. The API composes `ProjectView` only when project
ID, asset, decimals, epoch, and threshold match; mismatch is an integrity error,
not a value to reconcile heuristically.

Escrow lazily creates `EscrowProcurementAccount` on the first valid approval
submission by reading Registry's immutable project/vendor/cap values. The mirror
does not own lifecycle state. If Registry cancels before initialization, the
cancellation hook records a tombstone with zero released amount; Registry's
`Cancelled` state independently blocks later initialization.

## 4. Evidence commitments and procurement state machine

Before the pre-AI gate, Foundation records nonzero request and quote-bundle
commitments. Registry computes and stores:

```text
preEvidenceHash = keccak256(abi.encode(
  "POG_PRE_EVIDENCE_V1",
  projectId,
  procurementId,
  vendor,
  asset,
  budgetCap,
  requestHash,
  quoteBundleHash
))
```

After PO, delivery, and final invoice are recorded, Registry computes and stores:

```text
finalEvidenceHash = keccak256(abi.encode(
  "POG_FINAL_EVIDENCE_V1",
  projectId,
  procurementId,
  vendor,
  asset,
  reservedAmount,
  poHash,
  grnHash,
  invoiceHash,
  invoiceAmount
))
```

For a submitted AI assessment, `evidenceHash` must equal Registry's recomputed
hash for that exact stage. Pre assessment is accepted only in
`PreEvidenceRecorded`; final assessment only in `InvoiceSubmitted`. Evidence
cannot be edited in place. AI outcome remains evidence, never approval.

| From | Trigger and required evidence | To |
| --- | --- | --- |
| — | Registry `createProcurement`; bound Escrow, fixed nonzero vendor, positive cap | `Draft` |
| `Draft` | Registry `recordPrePurchaseEvidence`; nonzero request/quote hashes | `PreEvidenceRecorded` |
| `PreEvidenceRecorded` | valid pre AI assessment with exact `preEvidenceHash` | `PreAssessed` |
| `PreAssessed` | Escrow accepts first reserve approval and calls Registry callback | `ReserveApprovalPending` |
| `ReserveApprovalPending` | Escrow `reserveBudget`; exact terms, threshold met, enough unreserved funds | `BudgetReserved` |
| `BudgetReserved` | Registry `recordPurchaseOrder`; nonzero hash | `POIssued` |
| `POIssued` | Registry `recordDelivery`; nonzero GRN hash | `Delivered` |
| `Delivered` | Registry `recordInvoice`; `0 < amount <= reservedAmount`; stores final evidence hash | `InvoiceSubmitted` |
| `InvoiceSubmitted` | valid final AI assessment with exact `finalEvidenceHash` | `FinalAssessed` |
| `FinalAssessed` | Escrow accepts first payment approval and calls Registry callback | `PaymentApprovalPending` |
| `PaymentApprovalPending` | Escrow `executePayment`; exact terms and threshold met | `Paid` |
| any state from `Draft` through `PaymentApprovalPending` | Foundation asks Registry to cancel; Registry calls Escrow release hook | `Cancelled` |

`Paid` and `Cancelled` are terminal. Recording duplicate evidence, document,
reserve, or payment transitions reverts. At payment, the
exact `invoiceAmount` goes only to `vendor`; any difference between
`reservedAmount` and `invoiceAmount` returns to project unreserved accounting.
Cancellation releases reserved accounting to the same project's unreserved
balance; it never transfers tokens to a donor.

The Foundation may publish a nonzero cumulative Allocation Root only after at
least one procurement is `Paid`. Publishing increments
`allocationRootVersion`; roots are never edited in place. The leaf schema is
frozen as:

```text
keccak256(abi.encode(projectId, procurementId, vendor, asset, paidAmount, invoiceHash))
```

## 5. Funds and amount rules

- `MockHKD.decimals()` is 6. Replacement payment assets must also report 6
  decimals for this MVP. Asset address remains configurable per project/deployment.
- Contracts accept and emit atomic integer units only. APIs transport amounts as
  base-10 strings; UIs may format 1 token as `1.000000` but must never use IEEE-754
  floating point for signed or submitted values.
- A project's asset and `assetDecimals` are immutable from project creation.
- Fee-on-transfer, rebasing, callback-bearing, and tokens returning inconsistent
  transfer values are unsupported. Deposits use before/after balance checks and
  credit the amount actually received only when it equals the requested amount.
- `budgetCap`, reserve amount, and invoice amount must be positive. Reserve amount
  is at most `budgetCap`; invoice amount is at most `reservedAmount`.

For every project at every successful state transition:

```text
unreserved = totalDeposited
             - reservedOutstanding
             - totalPaid

totalDeposited = unreserved
                 + reservedOutstanding
                 + totalPaid
```

All terms are nonnegative; subtraction that would underflow is a protocol error.
For each procurement before payment, its outstanding contribution is its
`reservedAmount`; after payment/cancellation it is zero. Globally for each asset:

```text
sum(project unreserved + project reservedOutstanding) <= escrow token balance
```

The inequality permits accidental direct transfers but such surplus is not
spendable. Reservation moves value from unreserved to reserved; payment decreases
reserved by the full reserved amount and increases paid by the invoice amount;
the unused difference becomes unreserved. No successful path creates credited
funds.

Donor refunds and project closure are outside MVP scope. There is no
`totalRefunded`, public refund function, or administrative withdrawal path.

## 6. EIP-712 signature specification

### Domains

AI assessments use:

```text
EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)
name = "PoGRegistry"
version = "1"
chainId = current chain ID
verifyingContract = deployed PoGRegistry address
```

Human approvals use the same domain fields with `name = "ProcurementEscrow"`,
`version = "1"`, and the deployed escrow address. A signature is therefore invalid
across chains, deployments, contract types, and versions.

### Exact primary types

Enum values are encoded as `uint8` in their declaration order.

```text
AIAssessment(bytes32 assessmentId,bytes32 procurementId,uint8 stage,bytes32 reportHash,bytes32 evidenceHash,bytes32 modelId,uint8 outcome,uint16 riskScoreBps,uint64 issuedAt,uint64 expiresAt,uint256 nonce,address aiSigner)

ApprovalIntent(bytes32 procurementId,uint8 action,bytes32 assessmentId,bytes32 termsHash,uint256 nonce,uint64 deadline,uint32 approverEpoch,address approver)
```

EOA signatures must enforce low-`s`, valid `v`, nonzero recovery, and recovered
signer equality with the struct signer. ERC-1271 contract approvers are a planned
compatibility requirement for implementation; magic value `0x1626ba7e` is the
only success result.

Approval aggregation key:

```text
keccak256(abi.encode(
  procurementId, action, assessmentId, termsHash, approverEpoch
))
```

Only distinct, currently configured approvers count. Each intent consumes its
signer's nonce once even if another signer already approved the same key. Execution
consumes the aggregation key, so approvals cannot authorize a second reserve/pay.

Reserve terms:

```text
termsHash = keccak256(abi.encode(
  "POG_RESERVE_V1", projectId, procurementId, vendor, asset,
  reserveAmount, budgetCap, preEvidenceHash, preAssessmentId
))
```

Payment terms:

```text
termsHash = keccak256(abi.encode(
  "POG_PAYMENT_V1", projectId, procurementId, vendor, asset,
  invoiceAmount, reservedAmount, poHash, grnHash, invoiceHash,
  finalEvidenceHash, finalAssessmentId
))
```

Implementations must use the UTF-8 byte strings exactly as shown. The registry
assessment and the approval action/stage must match: pre-procurement with reserve,
final-payment with payment.

## 7. Frozen core surface

The exact Solidity ABI is generated only when contracts exist. The semantic
surface below is frozen; implementations may add view helpers but not weaken
preconditions.

`PoGRegistry`:

- `bindEscrow(address escrow)`; protocol admin, exactly once, before projects
- `createProject(bytes32 projectId, address recipient, address asset,
  address[] approvers, uint16 threshold) -> projectId`; Foundation is
  `msg.sender`
- `createProcurement(bytes32 procurementId, bytes32 projectId, address vendor,
  uint256 budgetCap) -> procurementId`; caller is the project's Foundation
- `recordPrePurchaseEvidence(bytes32 procurementId, bytes32 requestHash,
  bytes32 quoteBundleHash)`; Foundation only
- `submitAIAssessment(AIAssessment, bytes signature)`
- `recordPurchaseOrder(bytes32 procurementId, bytes32 poHash)`; Foundation only
- `recordDelivery(bytes32 procurementId, bytes32 grnHash)`; Foundation only
- `recordInvoice(bytes32 procurementId, bytes32 invoiceHash, uint256 amount)`;
  Foundation only
- `setApprovalPolicy(bytes32 projectId, address[] approvers, uint16 threshold)`;
  Foundation only
- `cancelProcurement(bytes32 procurementId)`; Foundation only
- `publishAllocationRoot(bytes32 projectId, bytes32 root, uint64 version)`;
  Foundation only
- read helpers for projects, procurements, assessments, and current AI nonce

`ProcurementEscrow`:

- `deposit(bytes32 projectId, uint256 amount)`
- `submitApproval(ApprovalIntent, bytes signature) -> uint16 approvalCount`
- `reserveBudget(bytes32 procurementId, uint256 reserveAmount, bytes32 termsHash)`
- `executePayment(bytes32 procurementId, bytes32 termsHash)`
- read helpers for balances, approval count/consumption, approver nonce and policy

Registry-only Escrow hooks require `msg.sender == registry`; admin is not an
alternative caller:

- `registerProjectFromRegistry(bytes32 projectId, address asset, uint8 decimals,
  address[] approvers, uint16 threshold, uint32 epoch)`
- `updateApprovalPolicyFromRegistry(bytes32 projectId, address[] approvers,
  uint16 threshold, uint32 newEpoch)`
- `releaseOnCancellationFromRegistry(bytes32 procurementId) -> uint256 released`

Escrow-only Registry callbacks require `msg.sender == escrow`; admin is not an
alternative caller:

- `markReserveApprovalPendingFromEscrow(bytes32 procurementId)`
- `markReservedFromEscrow(bytes32 procurementId, uint256 reserveAmount)`
- `markPaymentApprovalPendingFromEscrow(bytes32 procurementId)`
- `markPaidFromEscrow(bytes32 procurementId, uint256 paidAmount)`

Required events:

```solidity
event EscrowBound(address indexed escrow);
event ProjectCreated(bytes32 indexed projectId, address indexed foundation, address indexed asset);
event ProcurementCreated(bytes32 indexed procurementId, bytes32 indexed projectId, address indexed vendor, uint256 budgetCap);
event PrePurchaseEvidenceRecorded(bytes32 indexed procurementId, bytes32 requestHash, bytes32 quoteBundleHash, bytes32 preEvidenceHash);
event AIAssessmentRecorded(bytes32 indexed assessmentId, bytes32 indexed procurementId, uint8 indexed stage, uint8 outcome, uint16 riskScoreBps, bytes32 reportHash, bytes32 evidenceHash);
event ApprovalSubmitted(bytes32 indexed approvalKey, bytes32 indexed procurementId, address indexed approver, uint8 action, uint16 approvalCount, uint16 threshold);
event BudgetDeposited(bytes32 indexed projectId, address indexed donor, address indexed asset, uint256 amount);
event BudgetReserved(bytes32 indexed procurementId, bytes32 indexed projectId, address indexed vendor, uint256 amount, bytes32 approvalKey);
event PurchaseOrderRecorded(bytes32 indexed procurementId, bytes32 poHash);
event DeliveryRecorded(bytes32 indexed procurementId, bytes32 grnHash);
event InvoiceRecorded(bytes32 indexed procurementId, bytes32 invoiceHash, uint256 amount, bytes32 finalEvidenceHash);
event PaymentExecuted(bytes32 indexed procurementId, bytes32 indexed projectId, address indexed vendor, address asset, uint256 amount, bytes32 approvalKey);
event ProcurementCancelled(bytes32 indexed procurementId, uint256 releasedAmount);
event AllocationRootPublished(bytes32 indexed projectId, bytes32 indexed root, uint64 version);
event ApprovalPolicyUpdated(bytes32 indexed projectId, uint32 indexed approverEpoch, uint16 threshold);
```

Indexed fields are part of the integration contract. Events are authoritative only
after the configured confirmation policy and canonical-chain check.

## 8. Call order, atomicity, policy safety, and reentrancy

Every paired operation is one EVM transaction. A failed hook, callback, token
transfer, or invariant check reverts changes in both contracts atomically.

- **Create project:** Registry validates caller, binding, IDs, asset/decimals, and
  policy; writes `RegistryProject`; calls
  `registerProjectFromRegistry`; emits only after hook success. Escrow rejects a
  duplicate and writes its project account and policy.
- **Policy update:** Foundation calls Registry. Registry verifies project ownership,
  validates the full policy, and rejects while any project procurement is
  `ReserveApprovalPending` or `PaymentApprovalPending`. It computes
  `newEpoch = oldEpoch + 1`, calls `updateApprovalPolicyFromRegistry`, then
  updates its epoch/threshold snapshot and emits. The Escrow hook requires exactly
  `oldEpoch + 1`. Revert rolls both back; there is no direct admin shortcut.
- **Approval submission:** Escrow validates domain, signature, exact-next nonce,
  membership, epoch, deadline, assessment/evidence, terms, Registry state, and
  duplicate signer before effects. It records approval and increments nonce. On
  the first approval for a bundle it calls the matching approval-pending callback.
- **Reserve:** Escrow validates Registry state/evidence, exact terms, threshold,
  and funds; consumes the bundle and updates accounting; then calls
  `markReservedFromEscrow`. Registry validates pending state and amount, updates
  its snapshot/status, and returns before Escrow emits.
- **Payment:** Escrow validates final evidence/terms and threshold, consumes the
  bundle, updates accounting, transfers the exact invoice amount to the fixed
  vendor, then calls `markPaidFromEscrow`. Callback failure reverts the token
  transfer and all state. Escrow emits only after callback success.
- **Cancellation:** Foundation calls Registry. Registry validates a nonterminal
  state, marks cancelled, then calls `releaseOnCancellationFromRegistry`.
  Escrow marks its mirror cancelled, consumes pending bundles, releases any
  reservation to project unreserved accounting, and returns the released amount.
  Registry emits after hook success.

All public money/lifecycle entrypoints and counterpart calls use reentrancy
guards. Hooks/callbacks authenticate the exact immutable/bound counterpart before
state change. Registry callbacks never transfer tokens. Escrow uses
checks-effects-interactions and safe ERC-20 semantics; supported assets exclude
callback-bearing and rebasing tokens. Emergency pause may stop new
deposit/reserve/payment but cannot introduce a withdrawal, hook, callback,
approval, vendor, or payment bypass.

## 9. Revert and idempotency requirements

Use custom errors carrying relevant IDs for: unbound/already-bound Escrow,
counterpart mismatch, unknown/duplicate record, unauthorized actor, wrong state,
invalid evidence/hash/amount, insufficient funds, unsupported asset/decimals,
invalid/expired signature or assessment, nonce mismatch, stale epoch, duplicate
approver, pending-policy-change conflict, threshold not met, terms/vendor mismatch,
consumed approval, transfer failure, reentrancy, and pause.

State-changing contract calls intentionally revert on duplicate business actions.
Off-chain APIs provide idempotency: repeated requests with the same idempotency key
and identical canonical payload return the original operation; the same key with a
different payload returns HTTP 409. Relayers persist `(chainId, contract,
calldataHash)` and must not send a second transaction while one is pending. After a
timeout they reconcile receipt, nonce, state, and events before replacement.

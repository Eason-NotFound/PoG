# Integration Contract — M0.1

This document defines the hand-off among AI, API/relayer, web clients, payment
logic, and the future contracts. `docs/BLOCKCHAIN_SPEC.md` is authoritative for
on-chain structures and hashing.

## 1. System boundaries

| Component | Produces | Consumes | Must not do |
| --- | --- | --- | --- |
| AI service | canonical `AIAssessment`, detached EIP-712 signature, report hash | Registry-recomputed pre/final evidence hash and source documents | approve, choose its own evidence hash, hold human keys, call reserve/pay as authority |
| API | canonical records, idempotent operation resources, chain read model | signed AI/human payloads and receipts | claim success from mempool acceptance |
| Relayer | submitted transaction hash and reconciled receipt | already signed messages and execution requests | change signed fields, choose vendor/amount, reuse nonce |
| Foundation UI | project/procurement/document inputs; human signing flow where authorized | read model, risk report, wallet/network state | use floats, hide high-risk AI results, mark confirmed early |
| Recipient UI | delivery/GRN context and read-only lifecycle view unless separately authorized | read model and events | gain spending authority from UI role |
| Donor UI | deposits and proof/allocation views | asset metadata, balances, Allocation Roots | imply MockHKD has real value |
| Payment/contracts | signature verification, invariants, reserve and vendor transfer | Registry state, escrow balance, signed intents | accept AI as approver, pay an address other than fixed vendor |

Vendor is a preset test wallet configured when a procurement is created. APIs and
UIs may display it but cannot patch it. To change it, cancel before payment and
create a new procurement.

### On-chain topology and call directions

`PoGRegistry` is deployed first, then `ProcurementEscrow(registry)`. Protocol
admin calls `Registry.bindEscrow(escrow)` once before any project; Registry checks
that Escrow's immutable `registry()` points back to it. Neither reference can
change afterward.

Public lifecycle entrypoints—including project/procurement creation,
pre-purchase evidence, AI assessment, PO/GRN/invoice, approval policy,
cancellation, and Allocation Root—are on Registry. Public custody entrypoints—
deposit, human approval submission, reserve, and payment—are on Escrow.

Registry alone may call Escrow project-registration, policy-update, and
cancellation-release hooks. Escrow alone may call Registry approval-pending,
reserved, and paid callbacks. Exact counterpart checks have no admin fallback.
Each paired operation is atomic in one transaction; consumers never treat one
contract as updated if its counterpart call reverted.

### Composed read model

Registry's `RegistryProject` owns identity, actors, asset metadata, Allocation
Root, and policy epoch/threshold snapshots. Escrow's `EscrowProjectAccount` owns
custody totals and authoritative policy state. APIs compose `ProjectView`; they
must reject a mismatch in project ID, asset, decimals, epoch, or threshold.
Neither contract stores a shared canonical `Project` struct.

## 2. Canonical JSON transport

JSON property names mirror Solidity fields. `uint*` values and chain IDs are
base-10 strings. Addresses are EIP-55-displayable `0x` values but comparisons use
normalized 20-byte values. `bytes32` values are lowercase 66-character hex.
Enums travel as their numeric value plus an optional display label; only the
numeric value is signed.

Example approval payload (illustrative values, not a valid signature):

```json
{
  "domain": {
    "name": "ProcurementEscrow",
    "version": "1",
    "chainId": "84532",
    "verifyingContract": "0x0000000000000000000000000000000000000001"
  },
  "primaryType": "ApprovalIntent",
  "message": {
    "procurementId": "0x1111111111111111111111111111111111111111111111111111111111111111",
    "action": 0,
    "assessmentId": "0x2222222222222222222222222222222222222222222222222222222222222222",
    "termsHash": "0x3333333333333333333333333333333333333333333333333333333333333333",
    "nonce": "0",
    "deadline": "1790899200",
    "approverEpoch": "1",
    "approver": "0x0000000000000000000000000000000000000002"
  },
  "signature": "0x..."
}
```

The API rejects unknown fields in objects that will be hashed. Report and evidence
documents are stored off-chain; chain records contain hashes, never private raw
content.

For pre-purchase assessment, API supplies nonzero `requestHash` and
`quoteBundleHash` to Registry first and independently recomputes:

```text
keccak256(abi.encode(
  "POG_PRE_EVIDENCE_V1", projectId, procurementId, vendor, asset,
  budgetCap, requestHash, quoteBundleHash
))
```

For final assessment, API recomputes:

```text
keccak256(abi.encode(
  "POG_FINAL_EVIDENCE_V1", projectId, procurementId, vendor, asset,
  reservedAmount, poHash, grnHash, invoiceHash, invoiceAmount
))
```

The AI must sign Registry's exact recomputed hash. API rejects an assessment whose
`evidenceHash` differs before relaying it; Registry independently enforces the
same equality.

## 3. API operations

Minimum operation resources:

| Method/path | Purpose | Terminal success condition |
| --- | --- | --- |
| `POST /v1/procurements/{id}/pre-evidence` | record request/quote commitments | confirmed `PrePurchaseEvidenceRecorded` |
| `POST /v1/ai-assessments` | validate and queue signed assessment | confirmed `AIAssessmentRecorded` |
| `POST /v1/approvals` | validate and queue human intent | confirmed `ApprovalSubmitted` |
| `POST /v1/procurements/{id}/reserve` | execute threshold-approved reserve | confirmed `BudgetReserved` |
| `POST /v1/procurements/{id}/payment` | execute threshold-approved payment | confirmed `PaymentExecuted` |
| `GET /v1/operations/{id}` | return reconciliation state | `confirmed`, `failed`, or `replaced` |
| `GET /v1/projects/{id}` | canonical read model with sync cursor | chain-derived response |
| `GET /v1/procurements/{id}` | lifecycle, evidence hashes, approvals | chain-derived response |

Mutating requests require `Idempotency-Key`. Operation states are `accepted`,
`validating`, `submitted`, `confirming`, `confirmed`, `failed`, `replaced`,
and `orphaned`. HTTP 202 means queued/submitted, never business success. Responses
include `operationId`, `chainId`, `transactionHash` when known, expected event,
and a machine-readable error when failed.

Error categories shared across services:

- `VALIDATION_ERROR` (400): malformed field, precision, hash, unsupported enum.
- `SIGNATURE_INVALID` (401/422): recovery/1271/domain failure.
- `FORBIDDEN` (403): caller or signer lacks current role.
- `NOT_FOUND` (404): unknown project/procurement/assessment.
- `IDEMPOTENCY_CONFLICT` or `STATE_CONFLICT` (409).
- `EXPIRED` or `NONCE_MISMATCH` (422); response includes current nonce when safe.
- `CHAIN_UNAVAILABLE` (503): keep operation reconcilable; do not fabricate failure.

The API maps on-chain custom errors to these stable categories and retains the
decoded contract error name/data for debugging.

## 4. Signing and relaying flow

1. API reads canonical chain state, current signer nonce, project approval policy,
   assessment, fixed vendor, amounts, asset, and Registry-recomputed evidence and
   document hashes.
2. API computes the exact typed-data domain and `termsHash`; client independently
   renders every bound field before requesting the wallet signature.
3. API validates the signature locally, persists the immutable payload under its
   idempotency key, and queues it.
4. Relayer simulates the exact calldata against a recent block, then submits it.
5. Backend watches the expected event and checks indexed IDs, emitting contract,
   chain ID, block hash, receipt status, and confirmations.
6. A reorg removes provisional success. Only the configured confirmed event makes
   the operation `confirmed`; local Anvil uses 1 confirmation, Base Sepolia uses a
   configurable value initially set to 2 for the demo.
7. Reserve/payment execution follows after the approval threshold is confirmed.
   The execution transaction again must emit its matching event before the UI
   displays success.

Foundation policy changes go only through Registry. Registry rejects changes while
any project procurement is reserve-approval-pending or payment-approval-pending,
then invokes the Registry-only Escrow hook. A successful transaction increments
the epoch and updates both read models atomically. Protocol admin has no policy
shortcut.

The relayer can pay gas but cannot alter authority. A leaked relayer key can at
worst submit already valid messages or permissionless calls; it must not hold AI
or human signing keys.

## 5. Front-end rules

- Foundation view: create and fund project/procurement, upload hash commitments,
  inspect both AI reports, request human signatures, and monitor reserve/payment.
- Recipient view: inspect PO and record/confirm delivery inputs according to API
  authorization; it does not become an approver unless independently configured.
- Donor view: deposit MockHKD, see explicit test-token warning, verify paid leaves
  and Allocation Root versions.
- Every signing screen shows network, verifying contract, action, project and
  procurement IDs, fixed vendor, asset symbol/address/decimals, exact atomic and
  formatted amount, assessment/report hash, evidence/document hashes, nonce,
  deadline, policy epoch, threshold, and current approval count.
- `submitted` and `confirming` are visually distinct from `confirmed`. A transaction
  hash is a link/debug handle, not proof of success.
- Wallet chain mismatch blocks signing/submission. No UI performs automatic unit
  rounding; excess fractional digits are rejected.

## 6. Payment and accounting boundary

Payment code receives no free-form destination. It loads `Procurement.vendor` and
the immutable project asset from chain state, recomputes payment terms, verifies
threshold consumption, changes accounting before the external token transfer,
uses safe ERC-20 transfer semantics, and is non-reentrant. Events and updated
balances must reconcile in the same successful transaction.

The payment service does not maintain an independent spendable balance. Its
database is a chain read model and may lag; contract accounting is authoritative.
Donor refunds, project closure, and administrative withdrawal are not MVP API or
contract operations.

## 7. Integration versioning

- Deployment manifests will contain `schemaVersion`, `chainId`, addresses,
  deployment transaction hashes, deployed bytecode hashes, and ABI package
  version.
- The ABI package remains empty in M0. Consumers must fail closed until generated
  ABIs and a matching manifest exist.
- Changing an EIP-712 field/order/type, domain name/version, terms formula, event
  indexed field, evidence formula, cross-contract caller direction, decimals
  policy, or Allocation Root leaf schema is a breaking integration change and
  requires coordinated versioning.

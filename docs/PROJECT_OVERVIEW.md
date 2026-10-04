# PoG project overview

Snapshot: **2026-10-04**. Published source baseline:
`d74a7a0ef53abe32640f019ce1551bf5c2432826`. This page explains the project target
and the published implementation boundary. It is not a full-stack acceptance
report or a replacement for frozen contract/API/AI specifications.

## Purpose and roles

PoG links restricted project donations to procurement, delivery, invoices,
approvals and payment evidence. Donors should be able to inspect spending and
verify the evidence associated with it.

| Business role | Responsibility |
| --- | --- |
| Donor | Review project terms, donate to project Escrow and inspect results or eligible refunds |
| Foundation | Create the project, organize procurement, supply documents, receive approved token release and record supplier payment evidence |
| Recipient | State needs, inspect delivery and sign a receipt bound to the procurement |
| Vendor | Quote, deliver goods/services and issue the appropriate invoice or other document |
| Independent receiver / human approver | Inspect evidence and make the authorized acceptance or fund decision |
| AI | Extract fields, cite evidence and report risk or missing information |

The portal's Admin account is an application role, not blanket Foundation or
Recipient authority. Receipt confirmation, procurement approval, release approval
and settlement confirmation remain distinct actions. Development coordinators
and coding agents are collaboration roles, not product runtime components.

## End-to-end business target

1. Foundation creates a project with purpose and budget terms. Each Donor performs
   a **simulated** HKD-to-MockHKD conversion and deposits directly into that
   project's Escrow. Funds are locked to the project.
2. Recipient/ Foundation supplies the procurement request, specifications,
   proposed Vendor and quotes/PO as appropriate to the workflow.
3. Stage 0, **PrePurchase**, evaluates the proposed purchase against available
   project and procurement evidence. An independent human procurement approval
   authorizes `executeReserve`; this reserves budget without releasing funds.
4. Foundation issues the PO and Vendor delivers goods or performs the service.
   Foundation records Invoice and goods evidence. Recipient or an independent
   receiver provides GRN, photos or inspection materials; Recipient signs the
   procurement-bound receipt.
5. Stage 1, **FinalRelease**, compares the approved PO, delivery/receipt evidence
   and final Invoice. A new human approval authorizes the approved Invoice
   amount for release.
6. Escrow releases MockHKD to **Foundation**. The platform's simulated redemption
   and Foundation's simulated supplier payment are separate operations.
7. The payment module stores payment evidence. Human reconciliation/settlement
   confirmation records that the required mock settlement was checked.
8. The intended allocation/proof pipeline associates confirmed spending with
   Donors, hashes the evidence and approval/payment records, publishes the
   appropriate Merkle commitment and lets Donors verify the result.
9. While the project is Active, remainder stays locked. Closing stops new
   donations/procurements/reservations while existing obligations are resolved.
   After reconciliation and the required human close approval, eligible residual
   MockHKD is refundable to the original Donor wallets.

Steps above describe the product target. They do not claim that the current
portal, API, AI and chain run that sequence together.

### Release, payment and refund are different facts

In the demo example, Donors contribute 100, reserve is 80 and approved Invoice
amount is 72. A valid release transfers **72 to Foundation**, leaving 28 locked
in the project. `FundsReleasedToFoundation` is not supplier payment.

Mock redemption, simulated supplier payment evidence and human settlement
confirmation each have their own records. `MockPaymentConfirmed` does not prove
real HKD banking. After eligible closure, a 60/40 donation split assigns the 28
refund pool as 16.8/11.2; original Donors claim their own MockHKD. These are mock
units and current V2 refund rules, not a promise of fiat redemption or item-level
Donor Allocation.

See [Foundation settlement flow](FOUNDATION_SETTLEMENT_FLOW.md) and
[implemented V2 interface](M2_V2_INTERFACE_IMPLEMENTED.md) for exact rules.

## AI and human decision boundaries

| Stage | Evidence that may exist | Review target | Separate human action |
| --- | --- | --- | --- |
| PrePurchase / stage 0 | Project purpose/period/budget/policy, request/specifications, quotes, Vendor/history and proposed PO | Purpose suitability, budget/quantity/price comparison where comparable evidence exists, Vendor/related-party/split-purchase risk and missing inputs | Procurement approval and reserve |
| FinalRelease / stage 1 | Approved PO/current terms, GRN/inspection/photos, Invoice, Recipient receipt and duplicate/history evidence | Document type/fields, duplicate signals, Vendor/account consistency and PO/GRN/Invoice match | Release approval; later settlement is a separate action |

PrePurchase must not require a future Invoice or GRN from the same procurement,
or consume future payment status, answer labels or evaluation gold data.
Historical evidence needs an explicit purpose and provenance. A lack of
information is not low risk.

Qwen's intended role is document extraction and evidence-based interpretation.
Pydantic validates the structure; deterministic Python rules evaluate approved
constraints. A visible stamp or image similarity is only an observation/risk
signal; it does not authenticate an invoice or prove fraud. Market/policy inputs
must be provided and traceable; the model cannot invent an acceptable donation
price, an approved exception or a missing Vendor history.

Reports, score policy, report hash, signing request and transaction are separate
objects. Incomplete/unscored reports, model failures and unapproved scoring
policies cannot be converted into signable reports. Real service execution must
be distinguished from synthetic fixtures. Complete scored Pass/Review/Reject
results have only conditional risk-signing eligibility under the frozen gates;
none is a human fund approval. No new formula or automatic Reject threshold is
defined on this page.

The current local Qwen candidate uses Qwen3-VL-2B-Instruct without a loaded
LoRA adapter. It supports single-page PrePurchase PO extraction and diagnostics,
returns an incomplete, unscored Review and disables signing. FinalRelease is
not implemented by that adapter. Its runtime is not published in this source
baseline. Bounded cross-machine synthetic-sample calls and report-hash/file-save
checks do not establish acceptance of the full two-stage AI business flow.
Frozen synthetic reports demonstrate serialization only.

See [R2 freeze record](ai/V2_REPORT_FREEZE_RECORD.md),
[A2 integration profile](ai/V2_A2_INTEGRATION_PROFILE_001.md) and
[offline tools](ai/tools/README.md).

## Documents, facts and evidence storage

Document type and workflow stage are separate attributes. An invoice, purchase
order, quote, sales confirmation, receipt and payment voucher can all be useful
evidence, but they are not interchangeable and must be classified/count separately.

The target data model shares immutable file/source/version records while keeping
invoice header/line-item records separate from order/transaction records. Exact
production tables and migrations require API/AI agreement; this documentation
does not change the current database schema.

- The invoice collection targets sourced English commercial invoices in original
  PDF/image form. Medical bills, transport tickets, receipts, samples and
  synthetic images cannot silently replace that collection.
- Public access, permission to view, local evaluation, training and redistribution
  are checked separately. An archive or publisher's label alone does not prove
  a transaction occurred or permission for every use.
- Preserve raw text and normalized values, language, page/location, source,
  document version, known/unknown status, currency, units and exact decimal
  amounts. A bare `$` is not sufficient to select currency.
- Publisher annotations are separate from manually checked facts and independent
  expected labels. A service total is not necessarily a per-item product price.
- File SHA-256 and decoded-pixel identity support duplicate checks. Similarity
  only identifies review candidates. Same-document/multi-page/template/near-
  duplicate groups must not leak across training/evaluation splits.
- Invoice evidence does not prove payment. Historical invoice prices do not
  establish current comparable market prices or a donation project's acceptable
  unit price.

API-owned trusted versions, original file bytes, authorized page sets and chain
commitments must agree before producing or signing a report. A client's schema-
valid snapshot does not establish trusted facts. Raw private evidence is kept
off-chain; only the approved commitments/hashes are recorded on-chain.

## Module architecture and machine boundary

The published frontend has Next.js route handlers and a local JSON store. The
published API is a separate FastAPI service with PostgreSQL, private file
storage, a durable transaction worker and canonical event indexer. Its A2
contract path reaches Recipient `ReceiptConfirmed`.

The target deployment for the team's local prototype is:

| Machine/component | Responsibility | Current published boundary |
| --- | --- | --- |
| Browser / Next.js portal | Role-aware input, uploads, approvals and confirmed results | Local JSON demo; shared API adapter/BFF is not connected on main |
| Mac / POSIX API host | Authentication, trusted snapshots, immutable report/byte storage, operations and retry coordination | A1/A2 implemented; real R2 report transport/storage and final payment tail pending |
| Mac / POSIX PostgreSQL and Anvil | Persistent business state; local V2 contracts/receipts | Existing API/chain components and isolated checks; not whole-product acceptance |
| Windows Qwen host | Authorized document bytes, local inference, structured extraction and report generation | Separate limited diagnostic runtime; source/startup package not in main |
| Independent signing component | Recompute gates/digests and control AI signing key | Interface responsibility; not supplied by offline tools or the model |
| Allocation/proof pipeline | Confirmed-spend allocation, evidence commitments and Donor proofs | End-to-end integration pending |

Only authorized server-to-server traffic should reach Qwen. Private service
tokens stay in operator-owned files/configuration, never browser responses or
Git. Anvil remains loopback-only; clients do not receive its unlocked RPC.
The public protocol defines bytes/hashes and caller authorization; private
machine addresses and credentials are deployment inputs.

The AI model does not hold AI/Human/Recipient private keys. The API relayer
submits permitted signatures and separately coordinates its EVM transaction
nonce. The frozen shared AI signer nonce policy covers both stages; adding
stage 1 cannot create a second independent AI nonce lane. Existing A2 synthetic
signing is not real-report signing integration.

## Published milestones and remaining work

| Area | Published basis | Remaining integration |
| --- | --- | --- |
| Contract custody/approvals/settlement/refunds | Accepted M2 V2 | Full API/UI orchestration and payment evidence |
| Local deployment | Accepted M3.1 | Operator-specific manifest and current-instance checks |
| API/chain tools and A2 | M3.2 code and accepted `api-v0.2.0-a2` | Real reports, final release/settlement/closing/refunds and frontend adapter |
| AI protocol | Frozen R2 format and A2 mapping | Reviewed runtime, trusted report persistence, scoring policy, signing gates and live negatives |
| Frontend | Published portal demo | Shared state and real confirmation/error display |
| Donor verification | Demo records/historical hooks | Allocation/proof generation, commitment and actual verification |

Next, publish only the reviewed AI source/configuration examples, integrate
actual API-owned evidence and canonical reports, connect the frontend adapter,
and wire final release/mock settlement and allocation/proofs. Then validate one
exact integrated version with actual UI input, API traffic, database changes,
model result provenance, signatures, receipt/events and final Donor output.
Failures, retries and role isolation need their own evidence.

Separate statuses are required for implementation, component review, public
scope review, interface freeze, integration, runtime checks and independent
acceptance. A merged protocol or passing CI is not complete prototype acceptance.

## Runbooks and authoritative references

- [README component startup](../README.md#run-the-published-components)
- [Portal runbook](PORTAL_README.md)
- [API runtime](../services/api/README.md) and [A2 runbook](api/A2_RUNBOOK.md)
- [Anvil runbook](M3_1_RUNBOOK.md)
- [Status](STATUS.md) and [milestones](MILESTONES.md)
- [V2 contracts](M2_V2_SPEC.md) and [exact interface](M2_V2_INTERFACE_IMPLEMENTED.md)
- [AI/A2 mapping](ai/V2_A2_INTEGRATION_PROFILE_001.md)
- [Booth plan](BOOTH_MOCK_RUN.md)

API and chain launchers use the documented macOS/POSIX runtime; the chain
launcher imports `fcntl`, so native Windows support must not be assumed.
There is no combined public launcher or public Qwen service launcher in this
source version. Do not reinterpret historical gate wording as current status
or change frozen schemas, manifests, ABI/signing fields or accepted tags as part
of this overview.

# V2 team hand-off — responsibilities and operation plan

Date: 2026-10-03. Current scope is M2 contract implementation and verification.
This is an integration plan, NOT an implemented API, AI service, bank connection
or Anvil deployment. Exact Solidity prototypes and EIP712 fields are recorded in
`M2_V2_INTERFACE_IMPLEMENTED.md` after source freeze; do not infer them from M1.

## 1. Single source of truth

- Active contract set: unchanged MockHKD + PoGRegistryV2 + ProcurementEscrowV2.
- ABI package: `packages/contract-abis/v2`, version `0.2.0-m2-rc.1`.
- Signing domains: PoGRegistryV2 / ProcurementEscrowV2, version `2`.
- M3 will provide actual chainId/addresses/bytecode hashes. No deployed address
  is provided by M2. Never hard-code M1 addresses or sign against a guessed chain.
- Blockchain stores hashes and confirmed facts, not private invoices/photos.
- Registry state and Escrow accounting are authoritative. Database/UI compose
  them and reject disagreement in actors, project IDs, token or policy epoch.
- 6-decimal token integers; JSON uint values as decimal strings, no floats.
  Example: 100 mHKD = `100000000`, invoice 72 = `72000000`, remaining 28 =
  `28000000` atomic units. Reject more than six decimal places; never silently round.

## 2. Who builds which part

| Team | Concrete work | Inputs and hand-off | Must display / preserve |
| --- | --- | --- | --- |
| Blockchain (this M2) | contracts, signatures, custody, receipt commitment, mock settlement attestation, closure/refunds and generated ABIs | hashed evidence, detached AI/Recipient/human signatures | real token changes distinct from off-chain attestations |
| Front end | Foundation project/PO/invoice/release/settlement screens; Recipient receipt signing; Donor convert/donate/claim views | canonical API read model, wallet signing typed data, V2 ABIs | Pending vs confirmed; test-token and mock-HKD labels; closure/refund rule before donation |
| API/database | private file storage, SHA/keccak commitments, idempotent jobs, read model, typed-data construction and relayer | source documents, V2 getters/events, signatures and receipts | exact evidence hash from Registry, policy/nonce/deadline, canonical block identity |
| AI evidence audit | first PO review, then PO/Invoice/goods/Recipient-evidence comparison, structured risk report and isolated AI signature | private materials plus exact current Registry evidenceHash | AI is risk evidence, not final approval; version/model/report commitment |
| Payment/supplier | mock HKD ledger, donor exchange operation, Foundation redemption operation, fixed-vendor full mock payment and reconciliation proof | Foundation stablecoin release event; linked procurement/vendor/invoiceAmount | FundsReleased, RedemptionPending, HKDReady, SupplierPaymentPending, MockPaymentConfirmed are distinct |

Human approval is an explicit role with its own wallet, not an AI button or API
service credential. The booth's 1-of-1 default does not remove the separate
Recipient signature. Front end may put approver controls in the Foundation UI,
but it must show which distinct wallet is signing which exact action.

## 3. Ordered operation resources (proposed API paths, not running endpoints)

| Operation / suggested path | What API does / where it calls | Success evidence |
| --- | --- | --- |
| `POST /v2/projects` | validate Foundation/Recipient/token/policy; relay Registry project creation | confirmed Registry project event |
| `POST /v2/exchanges/hkd-to-stable` | mock HKD debit and linked MockHKD mint/transfer to Donor; payment team owns mock ledger | reconciled mock exchange plus confirmed token receipt, not actual fiat proof |
| `POST /v2/projects/{id}/donations` | obtain donor ERC20 allowance, then donor's Escrow deposit transaction | confirmed deposit event and donor credit; no API-selected donor wallet |
| `POST /v2/procurements` | Foundation creates fixed vendor/cap procurement in Active project | confirmed creation event |
| `POST /v2/procurements/{id}/po` | store PO/request documents privately, submit immutable commitments to Registry | confirmed PO evidence and exact preEvidenceHash |
| `POST /v2/ai-assessments` | audit/sign exact current pre/final evidence, validate locally, submit Registry signature | confirmed assessment ID + stage + report/evidence hashes |
| `POST /v2/approvals/reserve` | construct exact reserve terms and request human wallet signature; submit Escrow vote | confirmed current vote, not reservation yet |
| `POST /v2/procurements/{id}/reserve` | execute threshold/current-assessment-approved reservation | confirmed BudgetReserved |
| `POST /v2/procurements/{id}/invoice-goods` | after reservation and supplier delivery, store Invoice/goods commitments + exact amount | confirmed immutable invoice/goods event |
| `POST /v2/procurements/{id}/receipt` | show Recipient PO/invoice/goods and request their bound EIP712 receipt signature | confirmed Recipient receipt digest, not Foundation self-assertion |
| `POST /v2/approvals/release` | after final AI, request human signature over exact invoiceAmount and fixed Foundation | confirmed current release vote |
| `POST /v2/procurements/{id}/release` | execute Escrow transfer to Foundation | confirmed FundsReleased and matching token balances |
| `POST /v2/exchanges/stable-to-hkd` | payment team verifies linked stablecoin redemption operation, credits mock Foundation HKD once | mock HKDReady; not supplier payment |
| `POST /v2/procurements/{id}/mock-payments` | full mock HKD debit from Foundation, credit fixed vendor, store immutable linked evidence in Registry | SettlementRecorded; still not confirmed until human threshold |
| `POST /v2/approvals/mock-payment` | human checks conversion/payment evidence and signs fixed vendor + full invoice amount | confirmed vote; execution later emits MockPaymentConfirmed |
| `POST /v2/procurements/{id}/return-stable` | after Foundation explicitly enters Closing, authorize actual token transferFrom back to Escrow | confirmed returned amount/balance; debt still unresolved, no new procurement |
| `POST /v2/projects/{id}/closing` | Foundation requests Closing in Registry | confirmed Closing; disable new deposit/procurement/reserve |
| `POST /v2/approvals/close` | after all obligations resolved, request human signature over current exact close snapshot | current threshold vote; no refund until execution |
| `POST /v2/projects/{id}/finalize-close` | execute approved close and bounded refund snapshot | Refundable or zero-pool Closed; immutable entitlements |
| `POST /v2/projects/{id}/refund-claims` | original donor calls Escrow claim; destination cannot be overridden | confirmed refund event; stablecoins, not HKD |

Reservation/cancellation requires proper action-specific contract calls; the
implemented interface file supplies exact prototypes. Unreserved procurement
cancellation is Foundation-authorized; reserved cancellation needs human
approval and cannot erase a confirmed Recipient receipt or released obligation.

## 4. Mock exchange/payment evidence

Payment group must link exchange/payment operation IDs to projectId,
procurementId, Foundation, fixed vendor, currency HKD, exact invoiceAmount,
stablecoin release transaction and redemption transaction. Never use the same
operation for two invoices, repeat a completed mock debit/credit, or equate a
Foundation token balance with supplier receipt.

The existing MockHKD has public mint and no burn entrypoint; do not assume a
`burn()` API. For the later demo, a mint/transfer on-ramp and a transfer to a
clearly labelled mock redemption wallet can back a simulated ledger. This is a
proposed M3 integration choice, not a real redeemable token or implemented rail.
The API must reconcile the actual confirmed token transaction before mock HKD
credit. Public mint means token supply alone proves no HKD funding or reserve.

Human full-payment attestation checks evidence before finalizing Registry state.
On-chain hashes prove commitment and signer authorization, not evidence truth.
Any stablecoin return makes that released procurement an unresolved exception in
M2; no partial payment/top-up/writeoff path silently marks it settled.

## 5. Transaction/signature safety shared by all teams

- `Idempotency-Key` per mutation; same key/different payload -> conflict. Persist
  operationId, chainId, contract, calldataHash, receipt/blockHash and expected event.
- HTTP 202 means queued, not confirmed. M3 local Anvil uses one confirmation plus
  canonical block check; reorgs must retract provisional success.
- Signature UI shows network/contract/domain, action, target IDs, Foundation,
  Recipient, vendor, token, atomic/formatted amount, evidence/report hashes,
  signer, nonce, deadline, epoch and threshold. Never sign unexplained hashes.
- Read fresh signer nonce/current assessment/current epoch just before signing;
  expired votes renew with a new nonce and do not count twice. AI renewal creates
  a new assessment for unchanged evidence and invalidates previous human terms.
- Relayer may submit valid messages and pay gas, not hold AI/human/Recipient keys.
- Backend must listen to confirmed V2 events, not M1 Paid events or just tx hashes.
- Demo signing keys remain local; never commit private evidence, wallet files,
  secrets or production keys. Actual model/provider credentials belong outside Git.

## 6. Booth acceptance after M3 is explicitly authorized

All three computers use one common API/chain and distinct role wallets; three
separate Anvil chains would not share projects. The judge's Donor view should
demonstrate convert/donate/locked balance/refund; Foundation shows PO/AI/human/
invoice/release/redemption/payment; Recipient signs the invoice-bound receipt.

M2 tests validate contract composition with synthetic evidence and local test
signatures. They do not claim this three-computer booth or real AI/API/payment
service is already running. Deployment and integration require the next approval.

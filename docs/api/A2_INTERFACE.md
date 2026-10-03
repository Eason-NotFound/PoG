# PoG API A2 interface

A2 keeps all A1 draft, identity and private-document endpoints. The following
mutations require `Idempotency-Key`, return HTTP 202 when queued, and accept no
caller, RPC, contract, chain ID, nonce, role or free-form typed-data override.

| Method | Path | Fixed authority / result |
| --- | --- | --- |
| POST | `/v2/projects/{id}/chain/create` | owning Foundation; queues `createProject` |
| POST | `/v2/projects/{id}/donations` | current Donor; exact approve then deposit |
| POST | `/v2/procurements/{id}/chain/create` | owning Foundation |
| POST | `/v2/procurements/{id}/chain/purchase-order` | owning Foundation; immutable document-version hashes |
| POST | `/v2/procurements/{id}/chain/invoice-and-goods` | owning Foundation; invoice no greater than reserved |
| POST | `/v2/procurements/{id}/signing-requests` | `ai_pre`, `reserve`, or `receipt` fixed material |
| POST | `/v2/signing-requests/{id}/sign-demo` | intended signer, `confirm=true`, local unlocked mode only |
| POST | `/v2/signing-requests/{id}/submit` | intended EOA signer; queues the corresponding vote/fact |
| POST | `/v2/procurements/{id}/chain/reserve` | project Foundation or human; execution is separate from approval |
| GET | `/v2/projects/{id}/ledger` | project members or Donor; Donor sees only own credit |
| GET | `/v2/operations/{id}` | owning principal; independent step, receipt, block and canonical facts |

The signing domains are exactly `PoGRegistryV2` and `ProcurementEscrowV2`, version
`2`. The three nonce families are Registry `aiNonces`, Registry
`recipientNonces`, and Escrow `humanNonces`. A2 supports 65-byte EOA signatures;
ERC-1271 and external hardware wallets are explicitly unsupported by the HTTP
layer. `synthetic=true` identifies the deterministic PRE fixture; it is not an AI
service result.

The operation is confirmed only after a status-1 receipt, canonical block,
expected event and getter/ledger match. A transaction hash alone is never a
confirmation. No A2 route releases funds, confirms payment, settles, closes or
refunds a project.

Signing requests retain their historical bytes after expiry. Only never-submitted
`prepared`/`signed` requests with chain timestamp strictly greater than deadline
are marked `expired` when preparing a replacement. Use a new Idempotency-Key;
replaying the old key returns the historical expired request. Queued, confirmed,
failed and unknown-outcome reservations are not automatically released.
`signing_nonce_in_use` is a structured HTTP 409, including concurrent preparation.
Idempotency hashes bind the path resource and body, so reuse across resources
returns 409. Receipt evidence must be uploaded by the original Recipient.

Migration `c31003a20003` replaces the permanent nonce uniqueness constraint with
an index excluding expired requests. Downgrade refuses when expired history
exists, because the preceding schema cannot retain it safely.

## Local review integration — additive 004

The published 003 remains byte-for-byte unchanged. Successor
`c31003a20004` adds uint/canonical-text guards, stale/unbroadcast retirement,
canonical policy/event projections, and live-only reservation indexes. It is
forward-only; restore a verified isolated backup with its matching application
version for rollback. Backup restoration has not been rehearsed in this review.

Replay accepts all preserved historical hashes (9189 body-only, Hank resource
type/ID/body and procurement ID/body, and the newer resource ID/business ID/body).
Every format independently checks the saved target plus current authorization;
existing payload hashes, operations and audit history are never bulk rewritten.

Fresh signing validates the current nonce, typed digest, material, allowed AI
signer and human policy. PRE renewal permits accepted states 1/2/3 and human
Reserve renewal 2/3. Review/Reject outcomes remain risk evidence, not an AI veto.
Retirement requires never-submitted expiry/staleness, or a failed submission with
persistent positive proof of no broadcast. Queued, ambiguous and consumed
authorizations are protected; a new key/deadline never reuses an old signature.
Expiry keeps `signing_request.expire` with the original operation pointer,
deadline and chain timestamp. Other stale retirement is separately audited.

Recipient uploader mismatch retains HTTP 403
`receipt_evidence_uploader_mismatch`; durable caller/nonce collision retains
HTTP 409 `chain_nonce_history_conflict` and `chain.nonce_conflict` audit. Definite
read-only rejection is `not_broadcast`; transport uncertainty is not proof of it.
Readiness freshly checks chain identity/authority and requires schema head004.
An exact persisted snapshot supports offchain reads and authorized replay only,
not a claim that the chain is currently verified or permission for new sends.

New Receipt requests bind `receiptEvidenceDocumentVersionId` in their persisted
context (not a new EIP-712 field). Signing and submission recheck that exact
immutable version's namespace, procurement, category, uploader and frozen hash.
Legacy missing context cannot be repaired by a later same-hash upload. Only
unsubmitted prepared/signed authorization may be safely retired and rebuilt;
new mutation is also refused whenever a submitted operation pointer exists.
Worker checks original source before a queued Receipt creates an attempt and
before a prepared envelope makes a new send. Invalid source is audited as
`chain.receipt_source_blocked` / `requires_attention`, not proof of no broadcast
and not permission to release its reserved nonce. Exact already-broadcast
reconciliation and confirmed historical facts remain untouched.

This is a local review candidate only: no GitHub update, acceptance tag, release,
settlement, payment, closure, refund or production deployment is authorized here.

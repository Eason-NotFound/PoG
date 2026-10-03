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

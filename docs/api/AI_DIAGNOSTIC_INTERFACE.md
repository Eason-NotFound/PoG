# Private AI diagnostic interface v0.1

This opt-in candidate adds real Qwen report generation and local private report
storage for a confirmed PRE purchase order. It does not create a RiskReport,
on-chain PRE/FINAL assessment, wallet signature, human approval, reservation or
payment. A report may inform the assigned human reviewer; it cannot authorize funds.

`POST /v2/procurements/{procurementId}/ai-diagnostics` requires an Idempotency-Key
and `{purchaseOrderVersionId, confirm:true, contextSource, diagnosticContext}`.
`contextSource` is `user_declared` (default) or explicitly `demo_generated`.
The context requires positive decimal strings `quantity`, `quoteAmountAtomic`,
`unitPriceLimitAtomic`, nonblank `category` and `description`, and explicit
`periodStart`/`periodEnd` dates in YYYY-MM-DD format. No missing values are inferred.
The diagnostic budget is the actual Registry procurement budget cap, expressly
labelled `registry_procurement_budget_cap`; user conditions never change formal
project policy. A demo-generated context is labelled `syntheticInput:true` even
when the Qwen model invocation is real.

POST returns HTTP 202 with queued operation and diagnostic. A single background
attempt runs outside any open DB transaction and has no automatic retry.
GET on the procurement route returns the latest 20 private diagnostics, exact
PO version/MIME, configuration availability and deployment binding. GET
`/v2/ai-diagnostics/{id}` recovers the durable state. GET `/{id}/report` under that
resource returns exact UTF-8 CJSON with `X-Report-Hash` (Ethereum Keccak-256) and
`X-Report-SHA256`, both lowercase 0x hashes; content type is application/json.
Access requires the owning Foundation or this project's assigned human approver.

Enable only with `POG_AI_DIAGNOSTIC_ENABLED=true`, `POG_AI_DIAGNOSTIC_URL` and
`POG_AI_DIAGNOSTIC_TOKEN_FILE` (absolute private file path). This candidate permits
only the specifically approved LAN service `http://192.168.0.246:18765`; it forbids
redirects and proxy fallback. Credentials, original files and reports stay private.

Frozen interface d74a7a0 schemas and byte verifier are bundled unchanged and hash
checked. Canonical same-block Registry evidence, the three original uploaded
PO/request/goods-request versions, source provenance, content hashes, response
request/input/evidence binding, exact CJSON bytes, pinned model versions and an
unsigned response are verified before storing a completed report. Only image
evidence is supported in this checkpoint. The pinned service extracts visible
fields from one PO image; references do not mean all documents were reviewed.
Results remain Review / incomplete / riskScoreBps:null with explicit missing
contexts. `signingEnabled` remains false. Existing synthetic signing fixtures and
financial gates retain their existing semantics.

Migration c31004a30006 adds only ai_diagnostics and immutable-history triggers.
No existing tables, rows, balances, deployment instance or signing domains are
rewritten. The migration is forward-only to preserve diagnostic evidence.

# Portal demo and current V2 integration boundary

This portal is an independent local demonstration. Its same-origin `/api/*`
routes and JSON store do not call `services/api`, an AI model, a signer or a
chain. No wallet signature, token release or supplier transfer is performed.
The design document preserves an earlier UI draft; it is not a V2 protocol.

The current contract source and generated ABI are authoritative:
`contracts/src/PoGRegistryV2.sol`, `contracts/src/ProcurementEscrowV2.sol`,
`packages/contract-abis/v2`, and `M2_V2_INTERFACE_IMPLEMENTED.md`.

| Current demo | Required before connecting current V2 |
| --- | --- |
| Own `/api/*` routes and local JSON | Explicit adapter to the separately versioned API and confirmed chain read model; A1 AI/chain/payment adapters are unavailable |
| String IDs | Resolve confirmed project/procurement bytes32 IDs and current chain instance; never pass HTTP UUIDs directly |
| Two-decimal display amounts stored as numbers | Convert using the declared asset decimals; V2 token amounts are six-decimal atomic integers transported as decimal strings, never floating-point chain values |
| Fixed AI scores 18/12 and demo threshold 80 | Consume a versioned report under an agreed policy; these demo values do not define production scores or contract vetoes |
| SHA-256 JSON record/file commitments | Keep their algorithm and purpose explicit; compute Registry evidence using Keccak-256 and exact ABI-encoded recorded facts |
| Purchase/delivery UI review labels | Explicitly map to V2 PrePurchase=0 / FinalRelease=1 after confirming evidence and state requirements |
| Local approval/accounting updates | Separate human approval, current AI assessment, relayer submission and confirmed V2 events |

V2 `BudgetReserved` confirms reservation. `FundsReleasedToFoundation` confirms transfer to
Foundation. Subsequent redemption and supplier settlement are separate; only
the relevant settlement facts and human confirmation produce
`MockPaymentConfirmed`. Registry state `FundsReleased` is not Vendor payment. The older
`executePayment` / `PaymentExecuted` UI proposal is not the active V2 interface.

AI assessments are signed risk evidence, not human procurement/payment approval.
The Registry does not automatically veto Reserve/Release based on outcome or
risk score. Production report bytes/hash rules, null-score handling, signer
responsibility, global signer nonce coordination and actual domain/instance
inputs require the responsible teams' confirmation. Missing/unscored/model
failure states must not silently become a signed zero-risk assessment.

Portal tests/build validate this local demo. They do not establish actual AI,
API, EIP-712, chain, exchange, supplier-payment or Donor-proof integration.
Connecting those modules requires separate bounded tasks and new validation.

# PoG V2 contract ABIs

Version: `0.2.0-m2-rc.1` (technical candidate, not user acceptance).

Generated from Foundry 1.8.4 / Solc 0.8.24:

- `MockHKD.json` — identical token interface to accepted M1, valueless demo coin.
- `PoGRegistryV2.json` — PO-first review, signed receipt and mock settlement state.
- `ProcurementEscrowV2.json` — donor custody, human approvals, Foundation release,
  returned funds, closure and refunds.

Do not use the historical parent-directory Registry ABI for V2. Signed domains
are `PoGRegistryV2` / `ProcurementEscrowV2`, version `2`, not version `1`.
ABIs are generated; check consistency with `bash scripts/check-blockchain.sh`.
No addresses or deployment manifest are supplied in M2. M3 is not authorized.

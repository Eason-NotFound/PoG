# M2 V2 candidate — Foundation settlement and refunds

Milestone: M2 V2, technically verified, awaiting user acceptance. The user
authorized this revised scope on 2026-10-03. Do not merge or enter M3 automatically.

Base: accepted `blockchain-v0.1.0-m1` / `45e6bab`. Historical M1 source, tests,
specifications, dependencies and ABIs remain unchanged. Source rollback reference
is that immutable tag; no deployment or live-fund migration has occurred.

## Changes

- Independent RegistryV2/EscrowV2; unchanged valueless MockHKD.
- Project-specific locked donor deposits, PO-first AI/human reserve, immutable
  Invoice/goods and expected Recipient EIP712 receipt, final AI/human release of
  exactly invoiceAmount to fixed Foundation.
- Distinct full mock supplier-payment evidence and human confirmation; the
  chain does not pretend Foundation release proves bank/vendor payment.
- Active remainder locking, Closing restrictions and old-obligation completion,
  actual Closing-only returns that do not erase debt, human reconciliation,
  original-wallet proportional stablecoin refunds with no dust/sweep.
- Version-2 domains/signature types/events/ABIs. Integration must use
  `packages/contract-abis/v2`, not the historical M1 Registry ABI. Exact calls,
  ordinals, hash formulas and API/payment hand-off are documented separately.

## Verification

CEO independently reran `bash scripts/check-blockchain.sh` and
`forge test --fuzz-runs 2000 -vv` on the frozen source:

- 81/81 tests: 44 unchanged M1, 14 coder V2, 23 independent CEO V2.
- All five fuzz tests passed 2,000 inputs each; no failures/skips.
- Format/build/strict source lint, five ABI comparisons, M1 SHA-256 15/15,
  push-guard 12/12 and both diff checks passed.
- RegistryV2 runtime 21,328 bytes; EscrowV2 24,366 bytes, 210-byte EIP170 margin.
  No compiler/optimizer/code-size-limit relaxation.
- V2 MockHKD ABI is byte-identical to M1. The 18-file V2 technical snapshot is
  `docs/baselines/blockchain-m2-v2-rc.1.sha256`; old M1 manifest remains intact.

Independent review identified and confirmed fixes for invoice cancellation,
settlement-stage returns, Closing-only returns, global asset insolvency,
zero-time missing-vote counting, close/signing fail-fast checks and max-uint net
math. No remaining functional P1/P2 was found in the final review; this is not a
third-party security audit. A sandbox signature-cache warning is environmental,
not a compilation/lint/test failure.

## Limits / next gate

No Anvil deployment, actual backend/model/HKD ledger, running booth, real bank,
real stablecoin, proxy/NFT/DAO or M3 work. All fiat exchange/payment is simulated;
on-chain evidence is authorization/commitment, not proof of real-world truth.
Max 64 distinct donor wallets and 16 approvers per project. Nonzero returned
funds leave an unresolved exception; no partial payment, debt waiver or top-up
path is hidden in M2. Unclaimed refunds stay locked without an admin sweep.

Publish only this verified feature-branch candidate and a new immutable `rc`
snapshot, check CI, report to the user and stop. GitHub approval review is not
required per user decision; PR/CI, immutable tags and milestone acceptance are.

# M2 V2 local verification and acceptance

Date: 2026-10-03. M2 is a technical candidate; accepting it is the user's decision.
M3 deployment/API/booth integration starts only after a separate authorization.

## Verification commands

From the repository root:

```sh
bash scripts/check-blockchain.sh
```

The script finds the installed Forge binary even when VS Code's shell does not
include it in PATH. It checks format, build, EVM runtime sizes, all M1/V2 tests,
strict source lint, five generated ABI files, all 15 accepted M1 baseline hashes,
12 push-guard regressions and working/staged diff whitespace.

For a stronger randomized V2-only repeat, if Forge is in PATH:

```sh
forge test --match-path 'contracts/test/*V2.t.sol' --fuzz-runs 2000 -vv
```

Use `~/.foundry/bin/forge` when it is not in PATH. These tests use Foundry's
in-memory EVM and synthetic signatures/evidence; they do not start Anvil RPC or
run a web/API/model/payment service. No production wallet or evidence is needed.

## Files to review

- `contracts/src/PoGRegistryV2.sol`: PO-first facts, two AI stages, Recipient
  receipt, distinct mock settlement and project/procurement lifecycle.
- `contracts/src/ProcurementEscrowV2.sol`: credited donations, human threshold
  votes, exact transfers to Foundation, returned funds and donor refunds.
- `contracts/test/CEOSafetyV2.t.sol`: independent adversarial/economic acceptance.
- `contracts/test/ProcurementEscrowV2.t.sol`: coder-owned signature/token tests.
- `docs/FOUNDATION_SETTLEMENT_FLOW.md`: the revised business flow diagram.
- `docs/M2_V2_INTERFACE_IMPLEMENTED.md`: exact implemented signing/call contract.
- `docs/M2_V2_INTEGRATION.md`: concrete hand-off to other teams; suggested APIs
  are plans, not running endpoints.
- `packages/contract-abis/v2`: generated candidate ABI package.

## What M2 acceptance means

The contract composition verifies project-specific locking, invoice-limited
Foundation release, independent Recipient and AI/human gates, full mock vendor
payment attestation, and closure followed by proportional original-wallet
stablecoin refunds. The old M1 source/tests/ABIs and immutable tags stay intact.

It does not mean real AI detects real invoices, real fiat conversion works,
Anvil is deployed, or the three booth computers already share a running app.
Those are integration deliverables for the next authorized milestone.

## Deliberate MVP limits

- MockHKD is freely mintable and valueless; both HKD conversion directions and
  supplier payments are simulated, with no bank/real stablecoin connection.
- Up to 64 distinct donors and 16 approvers per project. Repeat donations are
  cumulative and share final project costs under the displayed refund rule.
- Goods and bank facts still depend on signed evidence and human reconciliation.
  The chain cannot force Foundation to pay a supplier after receiving tokens.
- Any actual return requires Closing and leaves the original debt unresolved.
  M2 has no partial payment, Foundation top-up, debt waiver or retry settlement
  path for that exception; it keeps Closing/refunds blocked, not falsely settled.
- Recipient-confirmed goods cannot be cancelled by Foundation. Expired AI can
  be renewed for the same immutable evidence before reserve/release executes.
- No administrator withdrawal, unclaimed-refund sweep, cross-project migration,
  proxy, NFT, DAO, Chainlink, zkML, public deployment or real-money asset.
- Runtime size is enforced on every check. Future contract changes must rerun
  it; a passing version's binary/tag must never be silently replaced.

## Version gate

Publish through the M2 feature branch / PR #2 with passing CI. A new `rc` tag is
a verified immutable candidate, not acceptance. Report results and stop. Do not
merge M2 into main, create its accepted tag or start M3 without user approval.
